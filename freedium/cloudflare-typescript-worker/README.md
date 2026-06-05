# medium-proxy-worker v3

A TypeScript-native Cloudflare Worker that proxies Medium articles, bypassing
paywalls using your own subscription cookies. Derived from
[Freedium](https://codeberg.org/Freedium-cfd/web) (Python/Docker), rewritten
for the Cloudflare edge with no Docker, no Durable Objects, no external WASM.

---

## Architecture

```
Browser / AI Agent
       │
       ▼
┌─────────────────────────────────────────────────────┐
│              Cloudflare Edge Worker                  │
│                                                      │
│  fetch()  →  router.ts                              │
│               ├─ format-selector  (HTML/MD/JSON)    │
│               └─ ArticleFetchService                │
│                     │                               │
│                     ├─① KvCache.get()              │  ~1 ms
│                     │      Workers KV (hot, TTL)   │
│                     │                               │
│                     ├─② D1Store.getByUrl()         │  ~5–20 ms
│                     │      D1 SQLite (cold)         │
│                     │      decompress paragraphs_gz │
│                     │      backfill KV              │
│                     │                               │
│                     └─③ MediumApiClient            │  ~300–800 ms
│                            GraphQL + cookie auth    │
│                            parseRawPost()           │
│                            ctx.waitUntil(           │
│                              kv.set + d1.upsert)    │
│                                                     │
│  Renderers (from Paragraph[] — never pre-stored)   │
│    html-renderer.ts    → text/html                 │
│    markdown-renderer.ts→ text/markdown              │
│    JSON pass-through   → application/json           │
└─────────────────────────────────────────────────────┘
```

### Storage layers

| Layer | Binding | Role | Latency |
|---|---|---|---|
| Workers KV | `ARTICLE_CACHE` | Hot cache — full `MediumArticle` JSON, TTL-based | ~1 ms |
| D1 SQLite | `DB` | Cold store — metadata + gzip `Paragraph[]` blob | ~5–20 ms |

Images are **never stored**. All image IDs from Medium's API are preserved in
the `Paragraph[]` JSON and resolved to `miro.medium.com` CDN URLs at render
time via `mediumImageUrl(id, width)`.

### Source of truth

`Paragraph[]` JSON, gzip-compressed, stored in `article_content.paragraphs_gz`.
Both HTML and Markdown are derived from it on demand. Neither format is stored
pre-rendered. Typical compression: 15–80 KB → 4–18 KB (4–5× ratio).

---

## Project structure

```
medium-proxy-worker-v3/
├── migrations/
│   └── 0001_initial.sql        D1 schema (articles + article_content tables)
├── src/
│   ├── index.ts                Worker entry — fetch() handler
│   ├── types/
│   │   ├── env.ts              Cloudflare bindings (KV, D1) + AppConfig
│   │   └── article.ts          Domain model: MediumArticle, Paragraph,
│   │                           D1ArticleRow, D1ContentRow, OutputFormat
│   ├── utils/
│   │   ├── logger.ts           Structured JSON logger (level-filtered)
│   │   ├── urls.ts             URL normalisation, post ID extraction, urlToId()
│   │   └── compress.ts         gzipString / gunzipString / sha256Hex
│   ├── cache/
│   │   └── kv-cache.ts         Workers KV hot cache (get / set / purge / list)
│   ├── db/
│   │   └── d1-store.ts         D1 cold store (upsert / getByUrl / search /
│   │                           recent / count / storageStats)
│   ├── parser/
│   │   ├── medium-api.ts       Medium GraphQL client + HTML fallback
│   │   ├── article-parser.ts   GQL response → MediumArticle
│   │   └── fetch-service.ts    Three-tier orchestrator (KV → D1 → network)
│   ├── renderer/
│   │   ├── markup-renderer.ts  Markup ranges → HTML inline + list grouping
│   │   └── html-renderer.ts    MediumArticle → full HTML page (format switcher)
│   ├── output/
│   │   ├── markdown-renderer.ts  MediumArticle → Markdown + YAML front-matter
│   │   └── format-selector.ts    ?format= / Accept: content negotiation
│   └── router/
│       └── router.ts           All routes, wires all modules
├── test/
│   ├── urls.test.ts            (13 tests)
│   ├── compress.test.ts        (8 tests)
│   ├── markup-renderer.test.ts (12 tests)
│   ├── article-parser.test.ts  (8 tests)
│   ├── markdown-renderer.test.ts (15 tests)
│   ├── format-selector.test.ts (9 tests)
│   └── d1-store.test.ts        (9 tests)  — in-memory D1 mock
├── wrangler.toml
├── tsconfig.json
├── vitest.config.ts
└── package.json
```

---

## Setup

### Prerequisites

- Node.js 18+
- Cloudflare account with **Workers Paid plan** (required for KV + D1)
- A Medium subscription — cookies unlock paywalled content

### 1. Install

```bash
npm install
```

### 2. Create Cloudflare resources

```bash
# KV namespace — hot cache
wrangler kv:namespace create ARTICLE_CACHE
wrangler kv:namespace create ARTICLE_CACHE --preview

# D1 database — cold store
wrangler d1 create medium-proxy-db
```

Paste the returned IDs into `wrangler.toml`:

```toml
kv_namespaces = [
  { binding = "ARTICLE_CACHE", id = "<paste>", preview_id = "<paste>" }
]

[[d1_databases]]
binding       = "DB"
database_name = "medium-proxy-db"
database_id   = "<paste>"
```

### 3. Run D1 migration

```bash
npm run migrate
# runs: wrangler d1 migrations apply medium-proxy-db
```

### 4. Set secrets

Log into medium.com in your browser, open DevTools → Application → Cookies,
copy the values of `uid` and `sid`:

```bash
wrangler secret put MEDIUM_COOKIES
# paste: uid=your_uid_value; sid=your_sid_value

# optional — enables /api/cache/purge and /api/cache/list
wrangler secret put ADMIN_TOKENS
# paste: your_secret_token   (comma-separate multiple: token1,token2)
```

### 5. Run locally

```bash
npm run dev
# → http://localhost:8787
```

### 6. Run tests

```bash
npm test
# 74 tests across 7 files
```

### 7. Typecheck

```bash
npm run typecheck
```

### 8. Deploy

```bash
npm run deploy
# or: npm run deploy -- --env production
```

---

## API reference

### Article proxy

```
GET /<medium-url>                         proxy by embedding full URL in path
GET /@<username>/<slug>                   proxy relative path (→ medium.com/<path>)
GET /?url=<medium-url>                    redirect to proxy path
GET /api/article?url=<medium-url>         explicit API endpoint (same as above)
```

All article endpoints honour `?format=` and `Accept:` negotiation:

| Format | Trigger | MIME type |
|---|---|---|
| HTML (default) | `?format=html` or `Accept: text/html` | `text/html` |
| Markdown | `?format=markdown` or `Accept: text/markdown` | `text/markdown` |
| JSON | `?format=json` or `Accept: application/json` | `application/json` |

```bash
# Browser — HTML
curl https://proxy.example.com/https://medium.com/@user/slug-abc123def456

# AI agent — Markdown with YAML front-matter
curl "https://proxy.example.com/@user/slug-abc123def456?format=markdown"
curl -H "Accept: text/markdown" https://proxy.example.com/@user/slug-abc123def456

# Raw article JSON
curl "https://proxy.example.com/@user/slug-abc123def456?format=json"
```

### Metadata API (D1-backed)

```bash
# Full-text search (title, subtitle, author, tags)
GET /api/search?q=<query>&limit=10

# Recently fetched articles (default 20, max 100)
GET /api/recent?limit=20

# Storage diagnostics
GET /api/stats

# Health check + D1 stats
GET /healthz
```

### Admin endpoints (require `Authorization: Bearer <token>`)

```bash
# Purge a single article from KV cache
POST /api/cache/purge
Content-Type: application/json
{ "url": "https://medium.com/@user/slug-abc123def456" }

# List KV cache keys (diagnostic)
GET /api/cache/list
```

### Response headers

Every article response includes:

| Header | Values | Meaning |
|---|---|---|
| `X-Cache` | `HIT` / `MISS` | KV hot cache result |
| `X-Cache-Source` | `kv` / `d1` / `network` | Which tier served the response |
| `Cache-Control` | `public, max-age=<KV_TTL_SECONDS>` | Browser/CDN cache hint |

---

## Configuration

All vars are set in `wrangler.toml [vars]` and can be overridden per environment:

| Var | Default | Description |
|---|---|---|
| `KV_TTL_SECONDS` | `3600` | Hot-cache TTL. Dev: 120. Prod: 7200. |
| `LOG_LEVEL` | `info` | `debug` / `info` / `warn` / `error` |

---

## Markdown output format

Suitable for LLMs, RAG pipelines, and AI agents. Every article begins with a
YAML front-matter block containing structured metadata:

```markdown
---
title: "Article Title"
subtitle: "Subtitle text"
author: "Author Name"
author_username: "authorusername"
publication: "Publication Name"
published: 2024-11-15
reading_time_min: 8
is_paywalled: true
language: en
claps: 1247
source_url: "https://medium.com/@user/slug-postid"
tags:
  - "TypeScript"
  - "AI"
---

![Article Title](https://miro.medium.com/v2/resize:fit:1200/<heroImageId>)

# Article Title

**Author Name** in Publication Name · November 15, 2024 · 8 min read · 1,247 claps

## Section heading

Body text with **bold**, _italic_, `inline code`, and [links](https://example.com).

```typescript
const example = "fenced code block with language annotation";
```

> Blockquote

- Unordered list item
- Another item

1. Ordered list item
2. Another item

---

_Originally published on [Medium](https://medium.com/@user/slug-postid)._
_Tags: `TypeScript`, `AI`_
```

---

## D1 schema

```sql
-- Article metadata (queryable, no blobs)
articles (
  article_id       TEXT PRIMARY KEY,   -- SHA-256 of normalised URL
  url              TEXT UNIQUE,
  title            TEXT,
  subtitle         TEXT,
  author_id        TEXT,
  author_name      TEXT,
  author_username  TEXT,
  publication_name TEXT,
  tags_json        TEXT,               -- JSON array: '["AI","TypeScript"]'
  reading_time_min REAL,
  is_paywalled     INTEGER,            -- 0 | 1
  published_at     INTEGER,            -- Unix ms
  updated_at       INTEGER,
  fetched_at       INTEGER,
  claps            INTEGER,
  language         TEXT,
  preview_image_id TEXT                -- NULL if no hero image
)

-- Content blob (separate table — metadata scans never touch it)
article_content (
  article_id         TEXT PRIMARY KEY REFERENCES articles ON DELETE CASCADE,
  paragraphs_gz      BLOB,             -- gzip(JSON(Paragraph[]))
  compressed_bytes   INTEGER,
  uncompressed_bytes INTEGER,
  content_hash       TEXT,             -- SHA-256 of uncompressed JSON
  stored_at          INTEGER
)
```

Indexes: `title`, `author_name`, `published_at DESC`, `fetched_at DESC`.

---

## Legal notice

Personal and educational use only. You must hold a valid Medium subscription.
Content copyright belongs to the original authors. Do not redistribute paywalled
content publicly.
