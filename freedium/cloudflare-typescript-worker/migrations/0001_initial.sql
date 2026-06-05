-- migrations/0001_initial.sql
-- D1 schema for medium-proxy-worker v3
--
-- Two tables:
--   articles         — article metadata (queryable columns, small rows)
--   article_content  — gzip-compressed Paragraph[] JSON (large blob, keyed by article id)
--
-- Design decisions:
--   • paragraphs_gz is BLOB (gzip of JSON Paragraph[]).
--     Typical article: 10–60 KB uncompressed → 3–15 KB gzipped.
--     D1 row limit is 1 MB; we are well within it.
--   • All image URLs are constructed at render time from image IDs stored
--     inside the Paragraph JSON — no image bytes are stored in D1.
--     Images continue to be served from miro.medium.com.
--   • article_id is SHA-256 hex of the normalised canonical URL — same key
--     used in KV — so both stores are always addressable by the same ID.

CREATE TABLE IF NOT EXISTS articles (
  -- Primary key: SHA-256 hex of normalised canonical URL
  article_id       TEXT    NOT NULL PRIMARY KEY,

  -- Canonical URL (stored for display and dedup checks)
  url              TEXT    NOT NULL UNIQUE,

  -- Core metadata (all queryable without decompressing content)
  title            TEXT    NOT NULL,
  subtitle         TEXT    NOT NULL DEFAULT '',
  author_id        TEXT    NOT NULL DEFAULT '',
  author_name      TEXT    NOT NULL DEFAULT '',
  author_username  TEXT    NOT NULL DEFAULT '',
  publication_name TEXT    NOT NULL DEFAULT '',

  -- tags stored as JSON array text e.g. '["AI","TypeScript"]'
  tags_json        TEXT    NOT NULL DEFAULT '[]',

  reading_time_min REAL    NOT NULL DEFAULT 0,
  is_paywalled     INTEGER NOT NULL DEFAULT 0,  -- 0 | 1

  -- Timestamps (Unix milliseconds)
  published_at     INTEGER NOT NULL DEFAULT 0,
  updated_at       INTEGER NOT NULL DEFAULT 0,
  fetched_at       INTEGER NOT NULL DEFAULT 0,

  claps            INTEGER NOT NULL DEFAULT 0,
  language         TEXT    NOT NULL DEFAULT 'en',

  -- preview image ID from miro.medium.com (NULL if no hero image)
  preview_image_id TEXT
);

-- Full-text search index across the most-queried text columns
CREATE INDEX IF NOT EXISTS idx_articles_title      ON articles(title);
CREATE INDEX IF NOT EXISTS idx_articles_author     ON articles(author_name);
CREATE INDEX IF NOT EXISTS idx_articles_published  ON articles(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_articles_fetched    ON articles(fetched_at DESC);

-- ── article_content ──────────────────────────────────────────────────────────
-- One row per article, holding the compressed paragraph data.
-- Kept in a separate table so metadata scans never touch the blob column.

CREATE TABLE IF NOT EXISTS article_content (
  article_id    TEXT    NOT NULL PRIMARY KEY
                        REFERENCES articles(article_id) ON DELETE CASCADE,

  -- gzip-compressed JSON of Paragraph[] (the lossless source of truth)
  -- Rendered to HTML or Markdown on demand — never stored pre-rendered.
  paragraphs_gz BLOB    NOT NULL,

  -- Byte sizes for diagnostics / admin UI
  compressed_bytes   INTEGER NOT NULL DEFAULT 0,
  uncompressed_bytes INTEGER NOT NULL DEFAULT 0,

  -- SHA-256 of the uncompressed JSON for cache-busting if Medium updates the article
  content_hash  TEXT    NOT NULL DEFAULT '',

  stored_at     INTEGER NOT NULL DEFAULT 0
);
