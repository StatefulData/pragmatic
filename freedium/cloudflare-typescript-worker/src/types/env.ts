/**
 * Cloudflare Worker environment bindings — v3.
 *
 * Storage architecture:
 *   Workers KV  → hot article cache  (TTL-based, edge-local reads, ~ms latency)
 *   D1          → cold persistent store (article metadata + gzip Paragraph[] JSON)
 *
 * No R2, no Durable Objects, no external WASM — pure Cloudflare-native stack.
 * Both stores are keyed by the same SHA-256 hex of the normalised article URL.
 */
export interface Env {
  /** KV namespace — hot cache; stores full MediumArticle JSON with TTL */
  ARTICLE_CACHE: KVNamespace;

  /** D1 database — cold store; articles + article_content tables */
  DB: D1Database;

  /** Secret: Medium subscription cookies (uid=…; sid=…) */
  MEDIUM_COOKIES: string;

  /** Secret (optional): comma-separated admin tokens for /api/cache/purge */
  ADMIN_TOKENS?: string;

  /** Var: KV TTL in seconds (default 3600) */
  KV_TTL_SECONDS: string;

  /** Var: log level */
  LOG_LEVEL: string;
}

export interface AppConfig {
  kvTTLSeconds: number;
  logLevel: "debug" | "info" | "warn" | "error";
  mediumCookies: string;
  adminTokens: Set<string>;
}

export function parseConfig(env: Env): AppConfig {
  return {
    kvTTLSeconds:  parseInt(env.KV_TTL_SECONDS ?? "3600", 10),
    logLevel:      (env.LOG_LEVEL ?? "info") as AppConfig["logLevel"],
    mediumCookies: env.MEDIUM_COOKIES ?? "",
    adminTokens:   new Set(
      (env.ADMIN_TOKENS ?? "").split(",").map(t => t.trim()).filter(Boolean),
    ),
  };
}
