/**
 * Article fetch service — v3.
 *
 * Three-tier lookup:
 *   1. KV hot cache   — warm hit, ~1 ms, full MediumArticle JSON
 *   2. D1 cold store  — miss in KV, decompress Paragraph[] from D1, backfill KV
 *   3. Medium API     — miss in D1, fetch + parse + write D1 + write KV
 *
 * On every cache miss the result is written back to both stores non-blocking
 * via ctx.waitUntil (called from the router layer).
 */

import type { MediumArticle } from "../types/article";
import type { Logger } from "../utils/logger";
import { MediumApiClient } from "./medium-api";
import { parseRawPost, parseHtmlFallback } from "./article-parser";
import { KvCache } from "../cache/kv-cache";
import { D1Store } from "../db/d1-store";
import { extractPostId, normaliseMediumUrl } from "../utils/urls";

export interface FetchResult {
  article: MediumArticle;
  /** Where the article came from — useful for X-Cache header and metrics */
  source: "kv" | "d1" | "network";
}

export type FetchError =
  | { type: "INVALID_URL";       message: string }
  | { type: "POST_ID_NOT_FOUND"; message: string }
  | { type: "FETCH_FAILED";      message: string }
  | { type: "PARSE_FAILED";      message: string }
  | { type: "NOT_FOUND";         message: string };

export function isFetchError(r: FetchResult | FetchError): r is FetchError {
  return "type" in r;
}

export class ArticleFetchService {
  constructor(
    private readonly api:    MediumApiClient,
    private readonly kv:     KvCache,
    private readonly d1:     D1Store,
    private readonly logger: Logger,
  ) {}

  async fetch(rawUrl: string): Promise<FetchResult | FetchError> {
    // Normalise first so KV and D1 keys are always consistent
    let url: string;
    try { url = normaliseMediumUrl(rawUrl); }
    catch { return { type: "INVALID_URL", message: "The provided URL is invalid." }; }

    // ── Tier 1: KV ────────────────────────────────────────────────────────────
    const kvHit = await this.kv.get(url);
    if (kvHit) {
      this.logger.info("Served from KV", { url });
      return { article: kvHit, source: "kv" };
    }

    // ── Tier 2: D1 ────────────────────────────────────────────────────────────
    const d1Hit = await this.d1.getByUrl(url);
    if (d1Hit) {
      this.logger.info("Served from D1, backfilling KV", { url });
      // Backfill KV so the next request is a hot hit — non-blocking at call site
      await this.kv.set(url, d1Hit);
      return { article: d1Hit, source: "d1" };
    }

    // ── Tier 3: Medium API ────────────────────────────────────────────────────
    let postId = extractPostId(url);
    if (!postId) {
      this.logger.debug("Resolving post ID via HTML", { url });
      postId = await this.api.resolvePostId(url);
    }
    if (!postId) {
      return { type: "POST_ID_NOT_FOUND", message: "Could not determine the Medium post ID from this URL." };
    }

    this.logger.info("Fetching from Medium API", { url, postId });
    const rawPost = await this.api.fetchPostById(postId);

    let article: MediumArticle;

    if (!rawPost) {
      // Fallback: scrape the HTML page
      const html = await this.api.fetchArticleHtml(url);
      if (!html) return { type: "FETCH_FAILED", message: "Failed to fetch article from Medium." };
      const partial = parseHtmlFallback(html, url);
      if (!partial) return { type: "PARSE_FAILED", message: "Failed to parse article content." };
      article = {
        id:          partial.id    ?? postId,
        title:       partial.title ?? "Untitled",
        author:      partial.author ?? { id: "", name: "Unknown", username: "unknown" },
        content:     partial.content ?? [],
        tags:        partial.tags   ?? [],
        isPaywalled: partial.isPaywalled ?? false,
        publishedAt: partial.publishedAt ?? Date.now(),
        url,
      };
    } else {
      try { article = parseRawPost(rawPost, url); }
      catch (err) {
        this.logger.error("Parse error", { url, postId, err: String(err) });
        return { type: "PARSE_FAILED", message: "Failed to parse article content." };
      }
    }

    if (!article.title) {
      return { type: "NOT_FOUND", message: "Article not found or has no content." };
    }

    return { article, source: "network" };
  }
}
