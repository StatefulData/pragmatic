/**
 * Tests for D1Store — article upsert, retrieval, search, and stats.
 *
 * D1Database is a Cloudflare binding unavailable in vitest's Node environment.
 * We build a minimal in-memory mock that satisfies the subset of the D1
 * interface used by D1Store, without importing any Cloudflare-specific modules.
 */
import { describe, it, expect, beforeEach } from "vitest";
import { D1Store } from "../src/db/d1-store";
import { Logger } from "../src/utils/logger";
import type { MediumArticle, D1ArticleRow } from "../src/types/article";
import { gzipString, gunzipString } from "../src/utils/compress";

// ── Minimal D1 mock ───────────────────────────────────────────────────────────

type Row = Record<string, unknown>;

class MockD1 {
  private readonly tables: Record<string, Row[]> = {
    articles:        [],
    article_content: [],
  };

  prepare(sql: string): MockStatement {
    return new MockStatement(sql, this.tables);
  }

  async batch(stmts: MockStatement[]): Promise<void> {
    for (const s of stmts) await s.run();
  }
}

class MockStatement {
  private bindings: unknown[] = [];

  constructor(
    private readonly sql: string,
    private readonly tables: Record<string, Row[]>,
  ) {}

  bind(...args: unknown[]): this { this.bindings = args; return this; }

  private tableName(): string {
    // crude extraction: "FROM articles" or "INTO articles"
    const m = this.sql.match(/(?:FROM|INTO)\s+(\w+)/i);
    return m?.[1] ?? "";
  }

  async run(): Promise<void> { await this.exec(); }

  async first<T>(): Promise<T | null> {
    const rows = await this.allRows();
    return (rows[0] as T | undefined) ?? null;
  }

  async all<T>(): Promise<{ results: T[] }> {
    return { results: (await this.allRows()) as T[] };
  }

  private async allRows(): Promise<Row[]> {
    const sql  = this.sql.trim().toUpperCase();
    const name = this.tableName();
    const tbl  = this.tables[name] ?? [];

    // INSERT OR REPLACE
    if (sql.startsWith("INSERT")) {
      await this.exec();
      return [];
    }

    // DELETE
    if (sql.startsWith("DELETE")) {
      await this.exec();
      return [];
    }

    // SELECT COUNT
    if (sql.includes("COUNT(*)")) {
      return [{ n: tbl.length, article_count: tbl.length, total_compressed: 0, total_uncompressed: 0 }];
    }

    // SELECT with JOIN (articles + article_content)
    if (sql.includes("JOIN") && this.bindings[0]) {
      const id = this.bindings[0] as string;
      const a  = (this.tables["articles"] ?? []).find(r => r["article_id"] === id);
      const c  = (this.tables["article_content"] ?? []).find(r => r["article_id"] === id);
      if (!a || !c) return [];
      return [{ ...a, paragraphs_gz: c["paragraphs_gz"] }];
    }

    // SELECT all (recent / search-like) — must come before WHERE check since search SQL contains both
    if (sql.includes("LIKE")) {
      const pattern = (this.bindings[0] as string).replace(/%/g, "").toLowerCase();
      return tbl.filter(r =>
        ["title", "subtitle", "author_name", "tags_json"].some(
          col => String(r[col] ?? "").toLowerCase().includes(pattern),
        )
      );
    }

    // SELECT WHERE article_id = ?
    if (sql.includes("WHERE") && this.bindings[0]) {
      const val = this.bindings[0] as string;
      return tbl.filter(r => Object.values(r).includes(val));
    }

    // ORDER BY fetched_at DESC LIMIT
    const limitMatch = this.sql.match(/LIMIT\s+\?/i);
    if (limitMatch) {
      const lim = this.bindings[this.bindings.length - 1] as number;
      return [...tbl].sort((a, b) => (b["fetched_at"] as number) - (a["fetched_at"] as number)).slice(0, lim);
    }

    return tbl;
  }

  async exec(): Promise<void> {
    const sql  = this.sql.trim().toUpperCase();
    const name = this.tableName();
    if (!this.tables[name]) this.tables[name] = [];
    const tbl = this.tables[name]!;

    if (sql.startsWith("INSERT")) {
      // Parse column list from SQL
      const colMatch = this.sql.match(/\(([^)]+)\)\s+VALUES/i);
      const cols     = colMatch?.[1]?.split(",").map(c => c.trim()) ?? [];
      const row: Row = {};
      cols.forEach((c, i) => { row[c] = this.bindings[i]; });
      // OR REPLACE: remove existing row with same article_id
      const id = row["article_id"] as string | undefined;
      if (id) {
        const idx = tbl.findIndex(r => r["article_id"] === id);
        if (idx >= 0) tbl.splice(idx, 1);
      }
      tbl.push(row);
    } else if (sql.startsWith("DELETE")) {
      const id = this.bindings[0] as string;
      const idx = tbl.findIndex(r => r["article_id"] === id);
      if (idx >= 0) tbl.splice(idx, 1);
    }
  }
}

// ── Test fixtures ─────────────────────────────────────────────────────────────

function makeArticle(overrides: Partial<MediumArticle> = {}): MediumArticle {
  return {
    id:          "abc123def456",
    title:       "Test Article",
    subtitle:    "A subtitle",
    author:      { id: "u1", name: "Jane Doe", username: "janedoe" },
    publication: { id: "pub1", name: "TestPub" },
    content:     [
      { id: "p1", type: "H2",  text: "Intro",       markups: [] },
      { id: "p2", type: "P",   text: "Hello world", markups: [] },
      { id: "p3", type: "IMG", text: "A caption",   markups: [],
        image: { id: "imgABC", originalWidth: 800, originalHeight: 600 } },
    ],
    tags:        ["TypeScript", "Cloudflare"],
    readingTime: 4,
    isPaywalled: true,
    publishedAt: 1_700_000_000_000,
    updatedAt:   1_700_010_000_000,
    claps:       99,
    url:         "https://medium.com/@janedoe/test-abc123def456",
    language:    "en",
    previewImage: { id: "heroImg", originalWidth: 1200, originalHeight: 630 },
    ...overrides,
  };
}

// ── Tests ─────────────────────────────────────────────────────────────────────

describe("D1Store", () => {
  let store: D1Store;
  let db: MockD1;

  beforeEach(() => {
    db    = new MockD1();
    store = new D1Store(db as unknown as D1Database, new Logger("error"));
  });

  it("upsert stores and getByUrl retrieves a full article", async () => {
    const article = makeArticle();
    await store.upsert(article);

    const retrieved = await store.getByUrl(article.url);
    expect(retrieved).not.toBeNull();
    expect(retrieved!.title).toBe("Test Article");
    expect(retrieved!.author.name).toBe("Jane Doe");
    expect(retrieved!.tags).toEqual(["TypeScript", "Cloudflare"]);
    expect(retrieved!.isPaywalled).toBe(true);
  });

  it("content Paragraph[] survives gzip round-trip", async () => {
    const article = makeArticle();
    await store.upsert(article);
    const retrieved = await store.getByUrl(article.url);
    expect(retrieved!.content).toHaveLength(3);
    expect(retrieved!.content[0]?.type).toBe("H2");
    expect(retrieved!.content[2]?.image?.id).toBe("imgABC");
  });

  it("image IDs are preserved (not URLs, not bytes)", async () => {
    const article = makeArticle();
    await store.upsert(article);
    const out = await store.getByUrl(article.url);
    // image is stored as ID, not as a URL or blob
    expect(out!.content[2]?.image?.id).toBe("imgABC");
    expect(out!.previewImage?.id).toBe("heroImg");
  });

  it("upsert is idempotent — second upsert replaces first", async () => {
    await store.upsert(makeArticle());
    await store.upsert(makeArticle({ title: "Updated Title", claps: 200 }));
    const out = await store.getByUrl(makeArticle().url);
    expect(out!.title).toBe("Updated Title");
    expect(out!.claps).toBe(200);
  });

  it("getByUrl returns null for unknown URL", async () => {
    const out = await store.getByUrl("https://medium.com/@nobody/nonexistent-aabbccdd");
    expect(out).toBeNull();
  });

  it("search matches on title", async () => {
    await store.upsert(makeArticle({ title: "TypeScript Deep Dive" }));
    await store.upsert(makeArticle({
      url: "https://medium.com/@other/other-aabbccdd",
      title: "Python Tutorial",
    }));
    const results = await store.search("TypeScript", 10);
    expect(results.some((r: D1ArticleRow) => r.title === "TypeScript Deep Dive")).toBe(true);
  });

  it("recent returns articles ordered by fetched_at", async () => {
    await store.upsert(makeArticle({ url: "https://medium.com/@u/a-aabbccdd", title: "First" }));
    await store.upsert(makeArticle({ url: "https://medium.com/@u/b-aabbccdd", title: "Second" }));
    const rows = await store.recent(10);
    expect(rows.length).toBeGreaterThanOrEqual(2);
  });

  it("count reflects inserted articles", async () => {
    expect(await store.count()).toBe(0);
    await store.upsert(makeArticle());
    expect(await store.count()).toBe(1);
  });

  it("storageStats returns a compression ratio", async () => {
    const stats = await store.storageStats();
    expect(stats).toHaveProperty("articleCount");
    expect(stats).toHaveProperty("avgCompressionRatio");
  });
});
