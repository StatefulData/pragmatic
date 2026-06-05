# CLAUDE.md — AI Assistant Working Guide

This file tells Claude (or any AI assistant) how to work effectively in this
codebase: how it is structured, what invariants must never be broken, and how
to reason about upstream changes from Freedium.

---

## Project identity

**freedium/cloudflare-typescript-worker** is a TypeScript Cloudflare Worker that proxies
Medium articles. It is a spiritual port of
[Freedium](https://codeberg.org/Freedium-cfd/web) (Python/FastAPI/Docker) to
the Cloudflare edge stack (TypeScript, Workers KV, D1 SQLite).

The canonical upstream to watch for breaking changes is:
```
https://codeberg.org/Freedium-cfd/web
```
The most change-sensitive files in that repo relative to ours are listed in
SKILL.md under "Upstream change map".

---

## Module map — what each file does

```
src/index.ts
  Entry point. Exports the fetch() handler. One line: createRouter(env, ctx).handle(request).
  Touch only to add new Worker-level exports (e.g. scheduled handlers).

src/types/env.ts
  Cloudflare binding declarations (KV, D1) and AppConfig.
  Touch when adding a new binding or config var to wrangler.toml.

src/types/article.ts
  All domain types: MediumArticle, Paragraph, MarkupRange, ImageMetadata,
  IframeMetadata, MixtapeEmbed, Author, Publication, D1ArticleRow, D1ContentRow,
  OutputFormat, KvCacheEnvelope.
  Touch when Medium's GraphQL schema adds new paragraph types or fields.

src/utils/logger.ts
  Structured JSON logger. Level-filtered. No external deps. Rarely changes.

src/utils/urls.ts
  normaliseMediumUrl(), extractPostId(), extractTargetUrl(), isMediumUrl(),
  mediumImageUrl(), urlToId().
  Touch when Medium changes URL patterns or image CDN structure.

src/utils/compress.ts
  gzipString(), gunzipString(), sha256Hex().
  Uses native CompressionStream API (no Node.js zlib).
  Touch only if the compression format changes.

src/cache/kv-cache.ts
  Workers KV hot cache. Key = "article:" + SHA-256 of normalised URL.
  get() / set() / purge() / listKeys().
  Touch when KV key schema changes.

src/db/d1-store.ts
  D1 cold store. Two tables: articles (metadata) + article_content (blob).
  upsert() compresses Paragraph[] with gzip and stores as BLOB.
  getByUrl() / getById() JOIN both tables and decompress.
  search() / recent() / count() / storageStats() scan articles only.
  Touch when the D1 schema changes (add a migration too).

src/parser/medium-api.ts
  MediumApiClient: fetchPostById() via GraphQL, fetchArticleHtml(), resolvePostId().
  The GraphQL query string (POST_QUERY) is the most upstream-sensitive part.
  Touch when Medium's GraphQL schema changes.

src/parser/article-parser.ts
  parseRawPost(): GQL response → MediumArticle.
  parseHtmlFallback(): Apollo state JSON → partial MediumArticle.
  Touch when Medium adds new paragraph types or GraphQL field names change.

src/parser/fetch-service.ts
  ArticleFetchService.fetch(): three-tier KV → D1 → network lookup.
  Returns FetchResult { article, source } or FetchError.
  Touch when adding new tiers, changing cache-backfill logic, or error types.

src/renderer/markup-renderer.ts
  applyMarkups(): markup range list → annotated HTML string.
  renderParagraph(): single Paragraph → HTML element string.
  groupListItems(): consecutive OLI/ULI → <ol>/<ul> wrappers.
  Touch when Medium adds new markup types or paragraph layout variants.

src/renderer/html-renderer.ts
  renderArticlePage(): full HTML page with inline CSS, format switcher, source badge.
  renderHomePage(): URL entry form.
  renderErrorPage(): error display.
  Touch for visual/UX changes, CSS updates, new UI features.

src/output/markdown-renderer.ts
  renderArticleMarkdown(): YAML front-matter + article body in Markdown.
  Touch when Markdown output format changes or new metadata fields are added.

src/output/format-selector.ts
  selectFormat(): determines OutputFormat from ?format= query param or Accept header.
  Touch only if new output formats are added.

src/router/router.ts
  createRouter(): wires all modules, defines all routes, calls ctx.waitUntil for writes.
  Touch when adding routes, changing response headers, or wiring new modules.

migrations/0001_initial.sql
  D1 schema. Never edit — add new migration files instead (0002_..., 0003_...).
```

---

## Invariants — never break these

**1. Paragraph[] is the single source of truth.**
Never store pre-rendered HTML or Markdown in D1 or KV. Both formats are
derived from `Paragraph[]` at request time. This ensures zero round-trip loss
when rendering either format.

**2. Images are always reference-only.**
No image bytes are stored anywhere. The only image data stored is the Medium
image ID (`string`) and dimensional metadata (`originalWidth`, `originalHeight`,
`focusPercentX`, `focusPercentY`) inside `Paragraph.image`. The render-time
function `mediumImageUrl(id, width)` constructs the `miro.medium.com` CDN URL.
Never write code that downloads or stores image bytes.

**3. Both stores use the same key.**
`urlToId(url)` produces a SHA-256 hex string from the normalised URL. This is
used as `article:${id}` in KV and as `article_id` in D1. They must always be
derived the same way. Do not introduce a second key derivation function.

**4. D1 migrations are additive only.**
Never ALTER or DROP columns in existing migration files. Add a new numbered
migration file. The D1Store code must remain backwards-compatible across
migrations until a deliberate breaking-change version bump.

**5. ctx.waitUntil for all writes.**
KV writes and D1 writes on the hot path must go through `ctx.waitUntil()` so
they never block the response. The only exception is the D1→KV backfill in
`fetch-service.ts`, which awaits intentionally since the result is served
immediately after.

**6. All imports are relative.**
No barrel (index.ts) files. Import directly from the file that owns the type
or function. This keeps the module graph explicit.

**7. No HTML or Markdown stored in D1.**
`article_content.paragraphs_gz` is the only content column. It contains
gzip-compressed `Paragraph[]` JSON. Never add a `html_gz` or `markdown_gz`
column — derive on demand.

---

## How to handle an upstream Freedium diff

When the Freedium Codeberg repo changes, follow this reasoning process:

### Step 1 — Classify the change

Read the diff and classify each changed file:

| Freedium file | Maps to our file(s) | Change sensitivity |
|---|---|---|
| `medium-parser/main.py` | `src/parser/medium-api.ts` + `src/parser/article-parser.ts` | HIGH — GraphQL query, field names |
| `medium-parser/rl_string_helper.py` | `src/renderer/markup-renderer.ts` | MEDIUM — markup type handling |
| `web/` (Jinja2 templates) | `src/renderer/html-renderer.ts` + `src/output/markdown-renderer.ts` | LOW — visual/layout only |
| `database-lib/` | `src/db/d1-store.ts` + `src/cache/kv-cache.ts` | MEDIUM — schema, query patterns |
| `freedium-library/` | `src/types/article.ts` + `src/utils/` | MEDIUM — shared helpers |
| `docker-compose.yml` | `wrangler.toml` | LOW — infra config only |
| `requirements.txt` | `package.json` | LOW — dependency management |

### Step 2 — GraphQL schema changes (highest risk)

If `POST_QUERY` in `medium-api.ts` needs updating:

1. Update the query string in `src/parser/medium-api.ts`.
2. Check if any new fields need new GQL interface types (the `Gql*` interfaces).
3. Update `src/parser/article-parser.ts` to map new fields into `MediumArticle`
   or `Paragraph`.
4. If the new field is a new `ParagraphType`, add it to:
   - `ParagraphType` union in `src/types/article.ts`
   - `mapParagraphType()` in `src/parser/article-parser.ts`
   - `renderParagraph()` switch in `src/renderer/markup-renderer.ts`
   - `renderParagraphMd()` switch in `src/output/markdown-renderer.ts`
5. Add a test case in `test/article-parser.test.ts` for the new type.

### Step 3 — Markup type changes (medium risk)

If Freedium adds a new `markup.type` in `rl_string_helper.py`:

1. Add the type to `MarkupRange["type"]` in `src/types/article.ts`.
2. Add a case to `mapMarkupType()` in `src/parser/article-parser.ts`.
3. Add HTML rendering in `htmlTokens()` in `src/renderer/markup-renderer.ts`.
4. Add Markdown rendering in `mdTokens()` in `src/output/markdown-renderer.ts`.
5. Add test in `test/markup-renderer.test.ts`.

### Step 4 — URL pattern changes (medium risk)

If Medium changes how post IDs appear in URLs:

1. Update `extractPostId()` regex in `src/utils/urls.ts`.
2. Update `resolvePostId()` selectors in `src/parser/medium-api.ts` if the
   Apollo state or og:url pattern changes.
3. Add/update tests in `test/urls.test.ts`.

### Step 5 — Schema changes (low risk if additive)

If new article metadata fields are added:

1. Add column to `articles` table in a new migration file (`0002_add_field.sql`).
2. Update `D1ArticleRow` in `src/types/article.ts`.
3. Update `articleToRow()` and `rowToArticle()` in `src/db/d1-store.ts`.
4. Update `renderArticlePage()` and/or `renderArticleMarkdown()` if the field
   should appear in output.
5. Update `test/d1-store.test.ts` fixture and assertions.

---

## Adding a new output format

Example: adding `?format=rss` for a feed endpoint.

1. Add `"rss"` to `OutputFormat` in `src/types/article.ts`.
2. Add `selectFormat()` case in `src/output/format-selector.ts`.
3. Create `src/output/rss-renderer.ts` with `renderArticleRss()`.
4. Handle `"rss"` case in the `switch (format)` block in `src/router/router.ts`.
5. Add test in `test/format-selector.test.ts` for the new trigger.
6. Add test in `test/rss-renderer.test.ts`.

---

## Adding a new D1 migration

```bash
# 1. Create the migration file
touch migrations/0002_add_vector_embedding.sql

# 2. Write SQL (additive only — no DROP, no ALTER of existing columns)
# ALTER TABLE articles ADD COLUMN embedding_json TEXT;

# 3. Apply locally
wrangler d1 migrations apply medium-proxy-db --local

# 4. Apply to production
wrangler d1 migrations apply medium-proxy-db --env production

# 5. Update D1ArticleRow in src/types/article.ts
# 6. Update d1-store.ts (articleToRow, rowToArticle)
# 7. Update tests
```

---

## Running checks before committing

```bash
npm run typecheck   # tsc --noEmit
npm test            # vitest run (74 tests)
```

Both must pass clean before any commit.

---

## Common pitfalls

**"My new paragraph type renders as `<p>` instead of nothing."**
`renderParagraph()` defaults to `<p>${content}</p>` for unrecognised types.
Add an explicit case that returns `""` if the type should be invisible.

**"D1 returns `null` for `paragraphs_gz`."**
D1 returns `ArrayBuffer` for BLOB columns. The store wraps it in `Uint8Array`
before passing to `gunzipString`. If you add another BLOB column, do the same.

**"KV and D1 keys are out of sync."**
Always derive keys with `urlToId(normaliseMediumUrl(url))`. Never construct
keys manually. `urlToId()` is in `src/utils/urls.ts`.

**"Search returns wrong results."**
`D1Store.search()` uses `LIKE` with four bindings (one per column). The mock
in `test/d1-store.test.ts` checks LIKE before WHERE — if you add a new
searchable column, update both the SQL and the mock.

**"The format switcher shows wrong active state."**
`renderArticlePage()` hardcodes `class="active"` on the HTML link. Update
`html-renderer.ts` if the active link logic needs to be dynamic.
