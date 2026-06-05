/**
 * Medium API client.
 *
 * Authenticates with Medium using subscription cookies (uid + sid) to
 * fetch paywalled article content via the private GraphQL endpoint.
 */

import type { Logger } from "../utils/logger";

// ── GraphQL query ─────────────────────────────────────────────────────────────

const POST_QUERY = `
query GetPost($postId: ID!) {
  post(id: $postId) {
    id title subtitle
    previewImage { id originalWidth originalHeight mimeType }
    creator { id name username bio imageId twitterScreenName }
    collection { id name description logoImage { id } domain }
    content { bodyModel { paragraphs {
      id name type text
      markups { type start end href anchorType userId linkMetadata { url title } }
      layout
      metadata { id originalWidth originalHeight mimeType focusPercentX focusPercentY alt }
      iframe { mediaResourceId iframeSrc thumbnailUrl title type }
      mixtapeMetadata { thumbnailImageId title description href mimeType }
      codeBlockMetadata { lang mode }
      dropCapImage { id originalWidth originalHeight }
    } } }
    tags { id displayTitle }
    readingTime isPaywalled firstPublishedAt updatedAt clapCount canonicalUrl language
  }
}`.trim();

// ── Raw GQL shapes ────────────────────────────────────────────────────────────

interface GqlImage {
  id: string; originalWidth?: number; originalHeight?: number;
  mimeType?: string; focusPercentX?: number; focusPercentY?: number; alt?: string;
}
interface GqlIframe {
  mediaResourceId: string; iframeSrc?: string; thumbnailUrl?: string; title?: string; type?: string;
}
interface GqlMixtape {
  thumbnailImageId?: string; title?: string; description?: string; href?: string; mimeType?: string;
}
interface GqlMarkup {
  type: string; start: number; end: number;
  href?: string; anchorType?: string; userId?: string;
  linkMetadata?: { url: string; title?: string };
}
interface GqlParagraph {
  id: string; name?: string; type: string; text: string; markups: GqlMarkup[];
  layout?: string; metadata?: GqlImage; iframe?: GqlIframe;
  mixtapeMetadata?: GqlMixtape; codeBlockMetadata?: { lang?: string; mode?: string };
  dropCapImage?: GqlImage;
}
interface GqlPost {
  id: string; title: string; subtitle?: string; previewImage?: GqlImage;
  creator: { id: string; name: string; username: string; bio?: string; imageId?: string; twitterScreenName?: string };
  collection?: { id: string; name: string; description?: string; logoImage?: { id: string }; domain?: string };
  content: { bodyModel: { paragraphs: GqlParagraph[] } };
  tags: { id: string; displayTitle: string }[];
  readingTime?: number; isPaywalled: boolean;
  firstPublishedAt: number; updatedAt?: number; clapCount?: number;
  canonicalUrl?: string; language?: string;
}

export interface RawMediumPost { post: GqlPost; }

// ── Client ────────────────────────────────────────────────────────────────────

export class MediumApiClient {
  private static readonly GQL = "https://medium.com/_/graphql";
  private static readonly UA  =
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36";

  constructor(
    private readonly cookies: string,
    private readonly logger: Logger,
  ) {}

  private headers(extra?: Record<string, string>): Headers {
    return new Headers({
      "Accept":          "application/json",
      "Content-Type":    "application/json",
      "Cookie":          this.cookies,
      "Origin":          "https://medium.com",
      "Referer":         "https://medium.com/",
      "User-Agent":      MediumApiClient.UA,
      "X-Obvious-CID":  "members",
      "X-Client-Date":  Date.now().toString(),
      "Sec-Fetch-Dest":  "empty",
      "Sec-Fetch-Mode":  "cors",
      "Sec-Fetch-Site":  "same-origin",
      ...extra,
    });
  }

  async fetchPostById(postId: string): Promise<RawMediumPost | null> {
    this.logger.debug("GraphQL fetch", { postId });
    let resp: Response;
    try {
      resp = await fetch(MediumApiClient.GQL, {
        method: "POST",
        headers: this.headers(),
        body: JSON.stringify({ operationName: "GetPost", query: POST_QUERY, variables: { postId } }),
      });
    } catch (err) {
      this.logger.error("GraphQL network error", { postId, err: String(err) });
      return null;
    }
    if (!resp.ok) {
      this.logger.warn("GraphQL non-2xx", { postId, status: resp.status });
      return null;
    }
    const json: { data?: { post?: GqlPost }; errors?: unknown[] } = await resp.json();
    if (json.errors?.length) this.logger.warn("GraphQL errors", { postId, errors: json.errors });
    return json.data?.post ? { post: json.data.post } : null;
  }

  async fetchArticleHtml(url: string): Promise<string | null> {
    this.logger.debug("HTML fetch", { url });
    try {
      const resp = await fetch(url, {
        method: "GET",
        headers: this.headers({ "Accept": "text/html,application/xhtml+xml", "Content-Type": "" }),
        redirect: "follow",
      });
      return resp.ok ? resp.text() : null;
    } catch (err) {
      this.logger.error("HTML fetch error", { url, err: String(err) });
      return null;
    }
  }

  async resolvePostId(url: string): Promise<string | null> {
    const html = await this.fetchArticleHtml(url);
    if (!html) return null;
    const apolloMatch = html.match(/"Post:([a-f0-9]{8,12})"/i);
    if (apolloMatch?.[1]) return apolloMatch[1];
    const ogMatch = html.match(/<meta[^>]+property="og:url"[^>]+content="([^"]+)"/i);
    if (ogMatch?.[1]) {
      const { extractPostId } = await import("../utils/urls");
      return extractPostId(ogMatch[1]);
    }
    return html.match(/window\.__MEDIUM_POST_ID__\s*=\s*["']([a-f0-9]{8,12})["']/i)?.[1] ?? null;
  }
}
