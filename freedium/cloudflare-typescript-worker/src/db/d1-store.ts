/**
 * D1 cold store — v3.
 *
 * Provides read and write access to the two D1 tables:
 *   articles         — article metadata (small, queryable)
 *   article_content  — gzip-compressed Paragraph[] JSON (blob, keyed by article_id)
 *
 * Compression strategy
 * ────────────────────
 * Paragraph[] JSON is gzip-compressed before INSERT and decompressed after
 * SELECT. Typical ratios observed on Medium articles:
 *   Uncompressed: 15–80 KB   Compressed: 4–18 KB   Ratio: ~4–5×
 *
 * Image references
 * ────────────────
 * Image IDs are preserved inside the Paragraph[] JSON exactly as returned
 * by Medium's GraphQL API. At render time, mediumImageUrl(id, width) constructs
 * the miro.medium.com CDN URL. No image bytes are stored in D1 or anywhere else.
 *
 * D1 BLOB handling
 * ────────────────
 * D1 stores BLOB columns as ArrayBuffer in JS. We pass a Uint8Array on write
 * (D1 accepts BufferSource) and receive an ArrayBuffer on read, which we wrap
 * in Uint8Array before passing to gunzipString().
 */

import type {
  MediumArticle,
  Paragraph,
  D1ArticleRow,
  D1ContentRow,
} from "../types/article";
import { urlToId } from "../utils/urls";
import { gzipString, gunzipString, sha256Hex } from "../utils/compress";
import type { Logger } from "../utils/logger";

// ── Helpers ──────────────────────────────────────────────────────────────────

/** Convert a MediumArticle into a flat D1ArticleRow (no blob). */
function articleToRow(article: MediumArticle, articleId: string): Omit<D1ArticleRow, never> {
  return {
    article_id:       articleId,
    url:              article.url,
    title:            article.title,
    subtitle:         article.subtitle ?? "",
    author_id:        article.author.id,
    author_name:      article.author.name,
    author_username:  article.author.username,
    publication_name: article.publication?.name ?? "",
    tags_json:        JSON.stringify(article.tags),
    reading_time_min: article.readingTime ?? 0,
    is_paywalled:     article.isPaywalled ? 1 : 0,
    published_at:     article.publishedAt,
    updated_at:       article.updatedAt ?? article.publishedAt,
    fetched_at:       Date.now(),
    claps:            article.claps ?? 0,
    language:         article.language ?? "en",
    preview_image_id: article.previewImage?.id ?? null,
  };
}

/** Reconstruct a MediumArticle from a D1ArticleRow + Paragraph[]. */
function rowToArticle(row: D1ArticleRow, content: Paragraph[]): MediumArticle {
  return {
    id:          row.article_id,
    title:       row.title,
    subtitle:    row.subtitle || undefined,
    author: {
      id:       row.author_id,
      name:     row.author_name,
      username: row.author_username,
    },
    publication: row.publication_name
      ? { id: "", name: row.publication_name }
      : undefined,
    content,
    tags:         JSON.parse(row.tags_json) as string[],
    readingTime:  row.reading_time_min || undefined,
    isPaywalled:  row.is_paywalled === 1,
    publishedAt:  row.published_at,
    updatedAt:    row.updated_at || undefined,
    claps:        row.claps || undefined,
    url:          row.url,
    language:     row.language || undefined,
    previewImage: row.preview_image_id
      ? { id: row.preview_image_id }
      : undefined,
  };
}

// ── D1Store class ─────────────────────────────────────────────────────────────

export class D1Store {
  constructor(
    private readonly db: D1Database,
    private readonly logger: Logger,
  ) {}

  // ── Read ───────────────────────────────────────────────────────────────────

  /**
   * Load a full MediumArticle from D1 by its canonical URL.
   * Returns null if not found. Decompresses Paragraph[] from the BLOB column.
   */
  async getByUrl(url: string): Promise<MediumArticle | null> {
    const articleId = await urlToId(url);
    return this.getById(articleId);
  }

  async getById(articleId: string): Promise<MediumArticle | null> {
    // Single query joining both tables — avoids two round-trips
    const row = await this.db
      .prepare(`
        SELECT a.*, c.paragraphs_gz
        FROM   articles a
        JOIN   article_content c USING (article_id)
        WHERE  a.article_id = ?
        LIMIT  1
      `)
      .bind(articleId)
      .first<D1ArticleRow & { paragraphs_gz: ArrayBuffer }>();

    if (!row) return null;

    const content = await this.decompressContent(row.paragraphs_gz, articleId);
    if (!content) return null;

    this.logger.debug("D1 hit", { articleId });
    return rowToArticle(row, content);
  }

  private async decompressContent(
    blob: ArrayBuffer,
    articleId: string,
  ): Promise<Paragraph[] | null> {
    try {
      const json = await gunzipString(new Uint8Array(blob));
      return JSON.parse(json) as Paragraph[];
    } catch (err) {
      this.logger.error("D1 decompress error", { articleId, err: String(err) });
      return null;
    }
  }

  // ── Write ──────────────────────────────────────────────────────────────────

  /**
   * Upsert a full MediumArticle into D1.
   * Compresses Paragraph[] to gzip before INSERT.
   * Uses INSERT OR REPLACE so re-fetching an updated article just overwrites.
   */
  async upsert(article: MediumArticle): Promise<void> {
    const articleId = await urlToId(article.url);
    const row       = articleToRow(article, articleId);

    // Compress content
    const contentJson = JSON.stringify(article.content);
    const gz          = await gzipString(contentJson);
    const hash        = await sha256Hex(contentJson);

    this.logger.debug("D1 upsert", {
      articleId,
      uncompressedBytes: contentJson.length,
      compressedBytes:   gz.byteLength,
    });

    // Use a batch to write both rows atomically
    await this.db.batch([
      this.db.prepare(`
        INSERT OR REPLACE INTO articles (
          article_id, url, title, subtitle,
          author_id, author_name, author_username, publication_name,
          tags_json, reading_time_min, is_paywalled,
          published_at, updated_at, fetched_at,
          claps, language, preview_image_id
        ) VALUES (
          ?, ?, ?, ?,
          ?, ?, ?, ?,
          ?, ?, ?,
          ?, ?, ?,
          ?, ?, ?
        )
      `).bind(
        row.article_id, row.url, row.title, row.subtitle,
        row.author_id, row.author_name, row.author_username, row.publication_name,
        row.tags_json, row.reading_time_min, row.is_paywalled,
        row.published_at, row.updated_at, row.fetched_at,
        row.claps, row.language, row.preview_image_id,
      ),

      this.db.prepare(`
        INSERT OR REPLACE INTO article_content (
          article_id, paragraphs_gz,
          compressed_bytes, uncompressed_bytes,
          content_hash, stored_at
        ) VALUES (?, ?, ?, ?, ?, ?)
      `).bind(
        articleId,
        gz,                    // Uint8Array — D1 accepts BufferSource for BLOB
        gz.byteLength,
        contentJson.length,
        hash,
        Date.now(),
      ),
    ]);
  }

  /** Delete an article and its content (CASCADE handles content row). */
  async delete(url: string): Promise<void> {
    const articleId = await urlToId(url);
    await this.db.prepare("DELETE FROM articles WHERE article_id = ?").bind(articleId).run();
    this.logger.info("D1 deleted", { articleId });
  }

  // ── Search / List ──────────────────────────────────────────────────────────

  /**
   * Full-text search across title, subtitle, author_name, and tags_json.
   * Uses LIKE — adequate for this use case; add FTS5 virtual table in a later
   * migration if you need ranked search over large article volumes.
   * Returns metadata rows only (no content blob).
   */
  async search(query: string, limit = 10): Promise<D1ArticleRow[]> {
    const like = `%${query.replace(/%/g, "\\%").replace(/_/g, "\\_")}%`;
    const rows = await this.db
      .prepare(`
        SELECT * FROM articles
        WHERE  title            LIKE ? ESCAPE '\\'
            OR subtitle         LIKE ? ESCAPE '\\'
            OR author_name      LIKE ? ESCAPE '\\'
            OR tags_json        LIKE ? ESCAPE '\\'
        ORDER  BY fetched_at DESC
        LIMIT  ?
      `)
      .bind(like, like, like, like, limit)
      .all<D1ArticleRow>();
    return rows.results;
  }

  /** Return the N most-recently fetched article metadata rows. */
  async recent(limit = 20): Promise<D1ArticleRow[]> {
    const rows = await this.db
      .prepare("SELECT * FROM articles ORDER BY fetched_at DESC LIMIT ?")
      .bind(limit)
      .all<D1ArticleRow>();
    return rows.results;
  }

  /** Total count of stored articles. */
  async count(): Promise<number> {
    const row = await this.db
      .prepare("SELECT COUNT(*) AS n FROM articles")
      .first<{ n: number }>();
    return row?.n ?? 0;
  }

  /** Storage diagnostics — sum of compressed and uncompressed bytes. */
  async storageStats(): Promise<{
    articleCount: number;
    totalCompressedBytes: number;
    totalUncompressedBytes: number;
    avgCompressionRatio: number;
  }> {
    const row = await this.db
      .prepare(`
        SELECT
          COUNT(*)                        AS article_count,
          SUM(compressed_bytes)           AS total_compressed,
          SUM(uncompressed_bytes)         AS total_uncompressed
        FROM article_content
      `)
      .first<{
        article_count: number;
        total_compressed: number;
        total_uncompressed: number;
      }>();

    const compressed   = row?.total_compressed   ?? 0;
    const uncompressed = row?.total_uncompressed ?? 0;
    return {
      articleCount:           row?.article_count ?? 0,
      totalCompressedBytes:   compressed,
      totalUncompressedBytes: uncompressed,
      avgCompressionRatio:    uncompressed > 0
        ? Math.round((uncompressed / compressed) * 10) / 10
        : 0,
    };
  }
}
