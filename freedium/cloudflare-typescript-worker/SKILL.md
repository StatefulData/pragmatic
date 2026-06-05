# SKILL.md — Maintenance Skill Sheet

This file encodes the structured knowledge needed to maintain and evolve
`medium-proxy-worker` over time. It is written for an AI assistant performing
code changes, but is equally useful for human contributors.

---

## 1. Upstream change map

Freedium source: `https://codeberg.org/Freedium-cfd/web`

The table below maps every Freedium module to the TypeScript file(s) that
implement equivalent functionality, with a risk score for how likely a change
in the upstream file will require a change here.

| Freedium path | Our equivalent | Risk | What to watch for |
|---|---|---|---|
| `medium-parser/main.py` | `src/parser/medium-api.ts` | 🔴 HIGH | `POST_QUERY` string, GQL endpoint URL, cookie header names |
| `medium-parser/main.py` | `src/parser/article-parser.ts` | 🔴 HIGH | New `ParagraphType` values, renamed GQL fields, new `metadata` sub-fields |
| `medium-parser/rl_string_helper.py` | `src/renderer/markup-renderer.ts` | 🟡 MEDIUM | New `markup.type` values, changed HTML output for existing types |
| `web/templates/article.html` | `src/renderer/html-renderer.ts` | 🟢 LOW | Layout structure, CSS class names (cosmetic only) |
| `web/templates/article.html` | `src/output/markdown-renderer.ts` | 🟢 LOW | Rendering logic for specific paragraph types |
| `database-lib/` | `src/db/d1-store.ts` + `src/cache/kv-cache.ts` | 🟡 MEDIUM | New cached fields, cache key changes, TTL policy |
| `freedium-library/` | `src/types/article.ts` + `src/utils/` | 🟡 MEDIUM | New shared types, URL pattern changes |
| `docker-compose.yml` | `wrangler.toml` | 🟢 LOW | New service dependencies (may need new bindings) |

---

## 2. Medium GraphQL schema

The GraphQL endpoint is `https://medium.com/_/graphql`.

The current `POST_QUERY` in `src/parser/medium-api.ts` requests:

```graphql
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
}
```

### Diagnosing a GraphQL change

If articles suddenly return empty content or missing fields:

1. Fetch the article page in a browser with your subscription cookies.
2. Open DevTools → Network → XHR, filter by `_/graphql`.
3. Inspect the request payload — compare operation name and field selection.
4. If field names have changed, update `POST_QUERY` and the `Gql*` interfaces.
5. If the endpoint has moved, update `MediumApiClient.GQL` constant.

### Known GQL field aliases

Medium has renamed fields historically. If you see `null` for a field, check
whether Medium uses a different name in the current schema:

| Old name (may still work) | Current name | Our field |
|---|---|---|
| `firstPublishedAt` | `firstPublishedAt` | `publishedAt` |
| `clapCount` | `clapCount` | `claps` |
| `creator` | `creator` | `author` |
| `collection` | `collection` | `publication` |
| `bodyModel.paragraphs` | `bodyModel.paragraphs` | `content` |

---

## 3. Paragraph type catalogue

Complete list of `ParagraphType` values we handle. Source of truth:
`src/types/article.ts` → `ParagraphType` union.

| Value | HTML output | Markdown output | Notes |
|---|---|---|---|
| `P` | `<p>` | paragraph text | Most common |
| `H1` | `<h1>` | `# ` | Rare at start; Medium uses H3/H4 mostly |
| `H2` | `<h2>` | `## ` | Common section heading |
| `H3` | `<h3>` | `### ` | Most common heading |
| `H4` | `<h4>` | `#### ` | Sub-heading |
| `PRE` | `<pre><code class="language-X">` | ` ```lang ` | `codeBlockMetadata.lang` used |
| `BQ` | `<blockquote><p>` | `> ` | Blockquote |
| `IMG` | `<figure><img src="miro.medium.com/...">` | `![alt](miro.medium.com/...)` | `Paragraph.image` holds ID |
| `MIXTAPE_EMBED` | `<a class="article-mixtape-embed">` | `> **[title](href)**` | Link preview card |
| `OLI` | `<li class="ordered">` → grouped in `<ol>` | `1. ` / `2. ` | Consecutive runs grouped |
| `ULI` | `<li class="unordered">` → grouped in `<ul>` | `- ` | Consecutive runs grouped |
| `IFRAME` | `<iframe src="...">` | `[▶ title](src)` | YouTube, CodePen, etc. |
| `HR` | `<hr />` | `---` | Section divider |
| `UNKNOWN` | `<p>` if text non-empty, else `""` | same | Future-proofing fallback |

### Adding a new paragraph type

When Freedium's parser recognises a new type:

```typescript
// 1. src/types/article.ts — add to ParagraphType union
export type ParagraphType = ... | "NEW_TYPE";

// 2. src/parser/article-parser.ts — add to mapParagraphType()
const map: Record<string, ParagraphType> = {
  ...
  NEW_TYPE: "NEW_TYPE",
};

// 3. src/renderer/markup-renderer.ts — add case to renderParagraph()
case "NEW_TYPE":
  return renderNewType(para);

// 4. src/output/markdown-renderer.ts — add case to renderParagraphMd()
case "NEW_TYPE":
  return `...markdown...\n`;

// 5. test/article-parser.test.ts — add test
// 6. test/markup-renderer.test.ts — add test
```

---

## 4. Markup type catalogue

Complete list of inline markup types handled.
Source: `MarkupRange["type"]` in `src/types/article.ts`.

| Type | HTML | Markdown | Notes |
|---|---|---|---|
| `STRONG` | `<strong>` | `**` | Bold |
| `EM` | `<em>` | `_` | Italic |
| `CODE` | `<code>` | `` ` `` | Inline code |
| `A` | `<a href target rel>` | `[text](href)` | Uses `markup.href` |
| `STRIKETHROUGH` | `<s>` | `~~` | GFM strikethrough |
| `U` | `<u>` | `_` | Underline — mapped to italic in Markdown (no MD equivalent) |

---

## 5. Image URL construction

All image rendering uses `mediumImageUrl(id, width)` from `src/utils/urls.ts`:

```typescript
// Format: https://miro.medium.com/v2/resize:fit:<width>/<imageId>
export function mediumImageUrl(imageId: string, width = 800): string {
  return `https://miro.medium.com/v2/resize:fit:${width}/${imageId}`;
}
```

**Used widths by context:**

| Context | Width | Where |
|---|---|---|
| Article body images | 800 | `renderParagraph()` default |
| Hero / preview image | 1200 | `renderArticlePage()` + markdown renderer |
| Mixtape embed thumbnails | 200 | `renderMixtape()` |
| Author avatars | 80 | `renderArticlePage()` |

If Medium migrates to a different CDN or URL structure, update `mediumImageUrl()`
in `src/utils/urls.ts`. Every renderer will pick up the change automatically.

---

## 6. Three-tier cache flow

```
Request arrives
      │
      ▼
KvCache.get(url)  ──hit──▶  return { article, source: "kv" }
      │ miss
      ▼
D1Store.getByUrl(url)
      │  ├─ JOIN articles + article_content
      │  ├─ gunzipString(paragraphs_gz)
      │  └─ JSON.parse → Paragraph[]
      │  ──hit──▶  kv.set(url, article)  [await — backfill]
      │            return { article, source: "d1" }
      │ miss
      ▼
MediumApiClient.fetchPostById(postId)
      │  ├─ POST https://medium.com/_/graphql
      │  └─ Cookie: uid=...; sid=...
      │ null? → fetchArticleHtml() → parseHtmlFallback()
      │
      ▼
parseRawPost(rawPost, url)  →  MediumArticle
      │
      ▼
return { article, source: "network" }
      │
router: ctx.waitUntil(
          kv.set(url, article),   // async, does not block response
          d1.upsert(article)      // async, does not block response
        )
```

### Cache key derivation

Both KV and D1 use the same key: `SHA-256(normaliseMediumUrl(url))`.

```
normaliseMediumUrl("https://Medium.COM/@user/slug?source=email#top")
  → "https://medium.com/@user/slug"

urlToId("https://medium.com/@user/slug")
  → "3f4a8b2c..."  (64-char hex)

KV key:  "article:3f4a8b2c..."
D1 key:  article_id = "3f4a8b2c..."
```

---

## 7. D1 schema — column reference

### `articles` table

| Column | Type | Source field | Notes |
|---|---|---|---|
| `article_id` | TEXT PK | `urlToId(url)` | SHA-256 hex |
| `url` | TEXT UNIQUE | `article.url` | Normalised |
| `title` | TEXT | `article.title` | |
| `subtitle` | TEXT | `article.subtitle ?? ""` | |
| `author_id` | TEXT | `article.author.id` | |
| `author_name` | TEXT | `article.author.name` | Searchable |
| `author_username` | TEXT | `article.author.username` | |
| `publication_name` | TEXT | `article.publication?.name ?? ""` | |
| `tags_json` | TEXT | `JSON.stringify(article.tags)` | `'["AI","TS"]'` |
| `reading_time_min` | REAL | `article.readingTime ?? 0` | |
| `is_paywalled` | INTEGER | `article.isPaywalled ? 1 : 0` | 0 or 1 |
| `published_at` | INTEGER | `article.publishedAt` | Unix ms |
| `updated_at` | INTEGER | `article.updatedAt ?? publishedAt` | Unix ms |
| `fetched_at` | INTEGER | `Date.now()` | Unix ms |
| `claps` | INTEGER | `article.claps ?? 0` | |
| `language` | TEXT | `article.language ?? "en"` | |
| `preview_image_id` | TEXT\|NULL | `article.previewImage?.id` | Medium image ID |

### `article_content` table

| Column | Type | Notes |
|---|---|---|
| `article_id` | TEXT PK FK | References `articles.article_id` CASCADE |
| `paragraphs_gz` | BLOB | `gzipString(JSON.stringify(article.content))` |
| `compressed_bytes` | INTEGER | `gz.byteLength` |
| `uncompressed_bytes` | INTEGER | `contentJson.length` |
| `content_hash` | TEXT | `sha256Hex(contentJson)` — for cache invalidation |
| `stored_at` | INTEGER | `Date.now()` |

---

## 8. Adding a new migration

Migrations are applied in order by filename. Never edit an existing file.

```bash
# File naming convention: NNNN_description.sql
migrations/
  0001_initial.sql         ← DO NOT EDIT
  0002_add_my_feature.sql  ← new file
```

Template for a new migration:

```sql
-- migrations/0002_add_my_feature.sql
-- Description: <why this change is needed>
-- Affected code: src/db/d1-store.ts, src/types/article.ts

ALTER TABLE articles ADD COLUMN new_field TEXT NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_articles_new_field ON articles(new_field);
```

Apply steps:

```bash
wrangler d1 migrations apply medium-proxy-db              # local/preview
wrangler d1 migrations apply medium-proxy-db --env production
```

After applying, update in order:
1. `src/types/article.ts` — add to `D1ArticleRow`
2. `src/db/d1-store.ts` — `articleToRow()`, `rowToArticle()`, relevant queries
3. Tests — `test/d1-store.test.ts` fixture and assertions
4. Renderers if the field appears in output

---

## 9. Testing strategy

| Test file | What it covers | Mock strategy |
|---|---|---|
| `test/urls.test.ts` | URL parsing, post ID extraction, normalisation | Pure functions, no mocks |
| `test/compress.test.ts` | gzip round-trips, sha256 | Native `CompressionStream` (Node.js 18+) |
| `test/markup-renderer.test.ts` | `applyMarkups()`, `renderParagraph()`, `groupListItems()` | Pure functions |
| `test/article-parser.test.ts` | `parseRawPost()`, `parseHtmlFallback()` | Hand-crafted GQL fixtures |
| `test/markdown-renderer.test.ts` | Full Markdown output including YAML front-matter | Article fixtures |
| `test/format-selector.test.ts` | `selectFormat()` logic | Synthetic `Request` objects |
| `test/d1-store.test.ts` | `D1Store` upsert/read/search/recent | In-memory `MockD1` class |

### The D1 mock

`test/d1-store.test.ts` contains a `MockD1` class that implements the subset
of the D1 interface used by `D1Store`. It stores rows in JS arrays in memory.

If you add a new `D1Store` method:
1. Identify which SQL pattern it uses (`JOIN`, `WHERE`, `LIKE`, `ORDER BY`).
2. Add handling for that pattern in `MockStatement.allRows()` or `MockStatement.exec()`.
3. Add a test that calls the new method and asserts the result.

The mock deliberately does not implement all of D1 — it only needs to cover
what D1Store actually calls. Keep the mock minimal.

---

## 10. Deployment checklist

Before deploying a change:

- [ ] `npm run typecheck` — zero errors
- [ ] `npm test` — all 74 tests pass (or more if you added tests)
- [ ] New paragraph types have both HTML and Markdown renderers
- [ ] New D1 columns have a migration file
- [ ] Migration applied to local D1: `wrangler d1 migrations apply medium-proxy-db`
- [ ] Secrets are current: `wrangler secret list`
- [ ] KV TTL is appropriate for the environment (`wrangler.toml [env.*]`)
- [ ] `wrangler deploy --dry-run` completes without error

---

## 11. Environment variables quick reference

| Variable | Set in | Default | Notes |
|---|---|---|---|
| `KV_TTL_SECONDS` | `wrangler.toml [vars]` | `3600` | Dev: `120`, Prod: `7200` |
| `LOG_LEVEL` | `wrangler.toml [vars]` | `info` | `debug`/`info`/`warn`/`error` |
| `MEDIUM_COOKIES` | `wrangler secret put` | — | Required. `uid=...; sid=...` |
| `ADMIN_TOKENS` | `wrangler secret put` | `""` | Optional. Comma-separated. |
