/**
 * Core domain types — v3.
 *
 * Source of truth for article content is Paragraph[] (stored gzip-compressed
 * in D1). Both HTML and Markdown are derived from it at render time — neither
 * is stored pre-rendered. Image URLs are always constructed from image IDs at
 * render time pointing to miro.medium.com; no image bytes are stored.
 */

// ── Paragraph types ──────────────────────────────────────────────────────────

export type ParagraphType =
  | "P" | "H1" | "H2" | "H3" | "H4"
  | "PRE" | "BQ" | "IMG" | "MIXTAPE_EMBED"
  | "OLI" | "ULI" | "IFRAME" | "HR" | "UNKNOWN";

export interface MarkupRange {
  start: number;
  end: number;
  type: "STRONG" | "EM" | "CODE" | "A" | "STRIKETHROUGH" | "U";
  href?: string;
  anchorType?: string;
  userId?: string;
  linkMetadata?: { url: string; title?: string };
}

export interface ImageMetadata {
  id: string;
  originalWidth?: number;
  originalHeight?: number;
  alt?: string;
  focusPercentX?: number;
  focusPercentY?: number;
  mimeType?: string;
}

export interface IframeMetadata {
  mediaResourceId: string;
  iframeSrc?: string;
  thumbnailUrl?: string;
  title?: string;
  type?: string;
}

export interface MixtapeEmbed {
  thumbnailImageId?: string;
  title?: string;
  description?: string;
  href?: string;
  mimeType?: string;
}

export interface Paragraph {
  id: string;
  name?: string;
  type: ParagraphType;
  text: string;
  markups: MarkupRange[];
  /** Medium layout variant: FULL_WIDTH | INSET_CENTER | OUTSET_CENTER | FILL_WIDTH | FULL_WIDTH */
  layout?: string;
  image?: ImageMetadata;
  iframe?: IframeMetadata;
  mixtapeMetadata?: MixtapeEmbed;
  codeBlockMetadata?: { lang?: string; mode?: string };
  dropCapImage?: ImageMetadata;
}

// ── Author / Publication ─────────────────────────────────────────────────────

export interface Author {
  id: string;
  name: string;
  username: string;
  bio?: string;
  imageId?: string;
  twitterScreenName?: string;
}

export interface Publication {
  id: string;
  name: string;
  description?: string;
  logoImageId?: string;
  domain?: string;
}

// ── Full article (in-memory domain model) ────────────────────────────────────

export interface MediumArticle {
  id: string;
  title: string;
  subtitle?: string;
  previewImage?: ImageMetadata;
  author: Author;
  publication?: Publication;
  content: Paragraph[];
  tags: string[];
  readingTime?: number;
  isPaywalled: boolean;
  publishedAt: number;   // Unix ms
  updatedAt?: number;    // Unix ms
  claps?: number;
  url: string;
  canonicalUrl?: string;
  language?: string;
}

// ── KV cache envelope ────────────────────────────────────────────────────────

export interface KvCacheEnvelope {
  /** Full MediumArticle (source of truth when KV is warm) */
  article: MediumArticle;
  cachedAt: number;
  ttl: number;
}

// ── D1 row shapes ─────────────────────────────────────────────────────────────

/**
 * Row shape returned by D1 for the `articles` table.
 * All columns are stored as TEXT/INTEGER/REAL — D1 returns them as JS primitives.
 */
export interface D1ArticleRow {
  article_id:       string;
  url:              string;
  title:            string;
  subtitle:         string;
  author_id:        string;
  author_name:      string;
  author_username:  string;
  publication_name: string;
  tags_json:        string;   // JSON array string
  reading_time_min: number;
  is_paywalled:     number;   // 0 | 1
  published_at:     number;   // Unix ms
  updated_at:       number;
  fetched_at:       number;
  claps:            number;
  language:         string;
  preview_image_id: string | null;
}

/**
 * Row shape returned by D1 for the `article_content` table.
 */
export interface D1ContentRow {
  article_id:         string;
  paragraphs_gz:      ArrayBuffer;  // gzip-compressed JSON of Paragraph[]
  compressed_bytes:   number;
  uncompressed_bytes: number;
  content_hash:       string;
  stored_at:          number;
}

// ── Output format ─────────────────────────────────────────────────────────────

export type OutputFormat = "html" | "markdown" | "json";
