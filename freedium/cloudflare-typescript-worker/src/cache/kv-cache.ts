/**
 * KV hot cache — v3.
 *
 * Stores the full MediumArticle (parsed, ready to render) as JSON with a
 * TTL. A cache hit avoids both the D1 query and the gzip decompress step.
 *
 * Key format:  article:<sha256-hex-of-normalised-url>
 * Value:       JSON-stringified KvCacheEnvelope (≤ 25 MB KV value limit)
 */

import type { KvCacheEnvelope, MediumArticle } from "../types/article";
import { urlToId } from "../utils/urls";
import type { Logger } from "../utils/logger";

export class KvCache {
  constructor(
    private readonly kv: KVNamespace,
    private readonly ttlSeconds: number,
    private readonly logger: Logger,
  ) {}

  private async key(url: string): Promise<string> {
    return `article:${await urlToId(url)}`;
  }

  async get(url: string): Promise<MediumArticle | null> {
    const k = await this.key(url);
    try {
      const raw = await this.kv.get(k, "json");
      if (!raw) return null;
      this.logger.debug("KV hit", { url, key: k });
      return (raw as KvCacheEnvelope).article;
    } catch (err) {
      this.logger.warn("KV get error", { url, key: k, err: String(err) });
      return null;
    }
  }

  async set(url: string, article: MediumArticle): Promise<void> {
    const k = await this.key(url);
    const envelope: KvCacheEnvelope = { article, cachedAt: Date.now(), ttl: this.ttlSeconds };
    try {
      await this.kv.put(k, JSON.stringify(envelope), { expirationTtl: this.ttlSeconds });
      this.logger.debug("KV set", { url, key: k, ttl: this.ttlSeconds });
    } catch (err) {
      this.logger.warn("KV set error", { url, key: k, err: String(err) });
    }
  }

  async purge(url: string): Promise<void> {
    const k = await this.key(url);
    await this.kv.delete(k);
    this.logger.info("KV purged", { url, key: k });
  }

  async listKeys(limit = 100): Promise<string[]> {
    const result = await this.kv.list({ prefix: "article:", limit });
    return result.keys.map(k => k.name);
  }
}
