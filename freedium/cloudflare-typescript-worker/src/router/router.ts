/**
 * Request router — v3.
 *
 * Storage: Workers KV (hot) → D1 (cold) → Medium API (network)
 * Writes are non-blocking via ctx.waitUntil so they never slow down responses.
 *
 * Routes
 * ──────
 *   GET  /                              Home / URL entry form
 *   GET  /?url=<url>                    Redirect to proxy path
 *   GET  /healthz                       Health + storage stats (JSON)
 *   GET  /api/article?url=<url>         Article (JSON, respects ?format=)
 *   GET  /api/search?q=<q>&limit=<n>    Full-text search (D1, JSON)
 *   GET  /api/recent?limit=<n>          Recently fetched (D1, JSON)
 *   GET  /api/stats                     Storage diagnostics (D1, JSON)
 *   POST /api/cache/purge               Purge KV entry (admin)
 *   GET  /api/cache/list                List KV keys (admin)
 *   GET  /<any>?format=html|markdown|json  Article proxy
 */

import type { Env } from "../types/env";
import { parseConfig } from "../types/env";
import { Logger } from "../utils/logger";
import { KvCache } from "../cache/kv-cache";
import { D1Store } from "../db/d1-store";
import { MediumApiClient } from "../parser/medium-api";
import { ArticleFetchService, isFetchError } from "../parser/fetch-service";
import { renderArticlePage, renderHomePage, renderErrorPage } from "../renderer/html-renderer";
import { renderArticleMarkdown } from "../output/markdown-renderer";
import { selectFormat, formatMimeType } from "../output/format-selector";
import { extractTargetUrl } from "../utils/urls";
import type { MediumArticle } from "../types/article";
import type { FetchResult } from "../parser/fetch-service";

// ── Response helpers ──────────────────────────────────────────────────────────

function jsonResp(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data, null, 2), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", "X-Content-Type-Options": "nosniff" },
  });
}

function htmlResp(html: string, status = 200, extra: Record<string, string> = {}): Response {
  return new Response(html, {
    status,
    headers: {
      "Content-Type":          "text/html; charset=utf-8",
      "X-Content-Type-Options":"nosniff",
      "X-Frame-Options":       "SAMEORIGIN",
      "Referrer-Policy":       "no-referrer",
      ...extra,
    },
  });
}

function errorToStatus(type: string): number {
  switch (type) {
    case "INVALID_URL":       return 400;
    case "NOT_FOUND":         return 404;
    case "POST_ID_NOT_FOUND": return 422;
    default:                  return 502;
  }
}

// ── Router factory ────────────────────────────────────────────────────────────

export function createRouter(env: Env, ctx: ExecutionContext) {
  const config   = parseConfig(env);
  const logger   = new Logger(config.logLevel);
  const kv       = new KvCache(env.ARTICLE_CACHE, config.kvTTLSeconds, logger);
  const d1       = new D1Store(env.DB, logger);
  const api      = new MediumApiClient(config.mediumCookies, logger);
  const fetchSvc = new ArticleFetchService(api, kv, d1, logger);

  function isAdmin(req: Request): boolean {
    if (config.adminTokens.size === 0) return false;
    const token = (req.headers.get("Authorization") ?? "").replace(/^Bearer\s+/i, "").trim();
    return config.adminTokens.has(token);
  }

  /** Persist a freshly fetched article to both stores non-blocking. */
  function persistAsync(article: MediumArticle, url: string): void {
    ctx.waitUntil(
      Promise.all([
        kv.set(url, article).catch(e => logger.warn("KV write failed", { url, err: String(e) })),
        d1.upsert(article).catch(e => logger.warn("D1 write failed",  { url, err: String(e) })),
      ])
    );
  }

  async function handleArticle(
    request: Request,
    targetUrl: string,
    proxyPath: string,
  ): Promise<Response> {
    const format = selectFormat(request);
    const result = await fetchSvc.fetch(targetUrl);

    if (isFetchError(result)) {
      const status = errorToStatus(result.type);
      if (format !== "html") return jsonResp({ error: result.type, message: result.message }, status);
      return htmlResp(renderErrorPage(status, result.message), status);
    }

    const { article, source } = result as FetchResult;

    // Persist to stores if this was a live fetch
    if (source === "network") persistAsync(article, targetUrl);
    // Backfill KV if served from D1 (already done in fetch-service, but belt+suspenders)

    const cacheHdrs: Record<string, string> = {
      "X-Cache":         source === "kv" ? "HIT" : "MISS",
      "X-Cache-Source":  source,
      "Cache-Control":   `public, max-age=${config.kvTTLSeconds}`,
    };

    switch (format) {
      case "json":
        return new Response(JSON.stringify(article, null, 2), {
          headers: { "Content-Type": "application/json; charset=utf-8", ...cacheHdrs },
        });

      case "markdown": {
        const md = renderArticleMarkdown(article);
        return new Response(md, {
          headers: {
            "Content-Type":        formatMimeType("markdown"),
            "Content-Disposition": `inline; filename="${article.id}.md"`,
            ...cacheHdrs,
          },
        });
      }

      default: { // html
        const html = renderArticlePage(article, proxyPath, source);
        // Also populate Cloudflare's CDN cache (non-blocking)
        ctx.waitUntil(
          caches.default.put(
            new Request(targetUrl),
            new Response(html, {
              headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": `public, max-age=${config.kvTTLSeconds}` },
            }),
          ).catch(() => {/* best-effort */})
        );
        return htmlResp(html, 200, cacheHdrs);
      }
    }
  }

  return {
    async handle(request: Request): Promise<Response> {
      const url    = new URL(request.url);
      const path   = url.pathname;
      const method = request.method.toUpperCase();
      const sp     = url.searchParams;

      logger.debug("Request", { method, path });

      // ── Health / stats ──────────────────────────────────────────────────────
      if (path === "/healthz") {
        const stats = await d1.storageStats();
        return jsonResp({ status: "ok", ts: Date.now(), version: "3.0.0", storage: stats });
      }

      // ── Home ────────────────────────────────────────────────────────────────
      if (path === "/" && method === "GET") {
        const qUrl = sp.get("url");
        if (qUrl) {
          const dest = new URL(request.url);
          dest.pathname = `/${qUrl}`;
          dest.search   = "";
          return Response.redirect(dest.href, 302);
        }
        return htmlResp(renderHomePage());
      }

      // ── Article API ─────────────────────────────────────────────────────────
      if (path === "/api/article" && method === "GET") {
        const targetUrl = sp.get("url");
        if (!targetUrl) return jsonResp({ error: "MISSING_PARAM", message: "url required" }, 400);
        return handleArticle(request, targetUrl, path + url.search);
      }

      // ── Search (D1) ─────────────────────────────────────────────────────────
      if (path === "/api/search" && method === "GET") {
        const q     = sp.get("q") ?? "";
        const limit = Math.min(parseInt(sp.get("limit") ?? "10", 10), 50);
        if (!q) return jsonResp({ error: "MISSING_PARAM", message: "q required" }, 400);
        const rows = await d1.search(q, limit);
        return jsonResp({ query: q, count: rows.length, results: rows });
      }

      // ── Recent (D1) ─────────────────────────────────────────────────────────
      if (path === "/api/recent" && method === "GET") {
        const limit = Math.min(parseInt(sp.get("limit") ?? "20", 10), 100);
        const rows  = await d1.recent(limit);
        return jsonResp({ count: rows.length, articles: rows });
      }

      // ── Storage stats (D1) ──────────────────────────────────────────────────
      if (path === "/api/stats") {
        const stats = await d1.storageStats();
        return jsonResp({ version: "3.0.0", ...stats });
      }

      // ── Cache admin: purge ──────────────────────────────────────────────────
      if (path === "/api/cache/purge" && method === "POST") {
        if (!isAdmin(request)) return jsonResp({ error: "UNAUTHORIZED" }, 401);
        let body: { url?: string };
        try { body = await request.json() as { url?: string }; }
        catch { return jsonResp({ error: "INVALID_BODY" }, 400); }
        if (!body.url) return jsonResp({ error: "MISSING_FIELD", message: "url required" }, 400);
        await kv.purge(body.url);
        return jsonResp({ status: "purged", url: body.url });
      }

      // ── Cache admin: list ───────────────────────────────────────────────────
      if (path === "/api/cache/list" && method === "GET") {
        if (!isAdmin(request)) return jsonResp({ error: "UNAUTHORIZED" }, 401);
        const keys = await kv.listKeys(100);
        return jsonResp({ count: keys.length, keys });
      }

      // ── Article proxy (catch-all) ───────────────────────────────────────────
      if (method === "GET") {
        const targetUrl = extractTargetUrl(request.url);
        if (!targetUrl) return htmlResp(renderHomePage());
        return handleArticle(request, targetUrl, path);
      }

      return htmlResp(renderErrorPage(405, "Method not allowed"), 405);
    },
  };
}
