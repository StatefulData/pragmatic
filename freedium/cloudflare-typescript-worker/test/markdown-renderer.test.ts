/**
 * Tests for the Markdown renderer.
 */
import { describe, it, expect } from "vitest";
import { renderArticleMarkdown } from "../src/output/markdown-renderer";
import type { MediumArticle } from "../src/types/article";

function makeArticle(overrides: Partial<MediumArticle> = {}): MediumArticle {
  return {
    id: "abc123",
    title: "Hello World",
    subtitle: "A subtitle with **markdown** characters",
    author: { id: "u1", name: "Jane Doe", username: "janedoe" },
    content: [
      { id: "p1", type: "H2", text: "Introduction", markups: [] },
      { id: "p2", type: "P",  text: "Hello world",  markups: [{ type: "STRONG", start: 6, end: 11 }] },
      { id: "p3", type: "PRE", text: "const x = 1;", markups: [], codeBlockMetadata: { lang: "javascript" } },
      { id: "p4", type: "OLI", text: "First",  markups: [] },
      { id: "p5", type: "OLI", text: "Second", markups: [] },
      { id: "p6", type: "ULI", text: "Bullet", markups: [] },
      { id: "p7", type: "BQ",  text: "A quote", markups: [] },
      { id: "p8", type: "HR",  text: "",        markups: [] },
    ],
    tags: ["TypeScript", "Cloudflare"],
    isPaywalled: false,
    publishedAt: 1700000000000,
    url: "https://medium.com/@janedoe/hello-abc123",
    ...overrides,
  };
}

describe("renderArticleMarkdown", () => {
  it("includes YAML front-matter", () => {
    const md = renderArticleMarkdown(makeArticle());
    expect(md).toMatch(/^---\n/);
    expect(md).toContain('title: "Hello World"');
    expect(md).toContain("author: \"Jane Doe\"");
    expect(md).toContain("published:");
    expect(md).toContain("is_paywalled: false");
    expect(md).toContain("source_url:");
    expect(md).toContain("TypeScript");
  });

  it("renders h2 heading", () => {
    const md = renderArticleMarkdown(makeArticle());
    expect(md).toContain("## Introduction");
  });

  it("renders bold markup", () => {
    const md = renderArticleMarkdown(makeArticle());
    expect(md).toContain("**world**");
  });

  it("renders code block with language", () => {
    const md = renderArticleMarkdown(makeArticle());
    expect(md).toContain("```javascript\nconst x = 1;\n```");
  });

  it("renders ordered list items with numbers", () => {
    const md = renderArticleMarkdown(makeArticle());
    expect(md).toContain("1. First");
    expect(md).toContain("2. Second");
  });

  it("renders unordered list items", () => {
    const md = renderArticleMarkdown(makeArticle());
    expect(md).toContain("- Bullet");
  });

  it("renders blockquote", () => {
    const md = renderArticleMarkdown(makeArticle());
    expect(md).toContain("> A quote");
  });

  it("renders horizontal rule", () => {
    const md = renderArticleMarkdown(makeArticle());
    expect(md).toContain("---");
  });

  it("renders image paragraph", () => {
    const article = makeArticle({
      content: [{
        id: "img1", type: "IMG", text: "A caption", markups: [],
        image: { id: "imgId123", originalWidth: 800, originalHeight: 600 },
      }],
    });
    const md = renderArticleMarkdown(article);
    expect(md).toContain("![");
    expect(md).toContain("imgId123");
  });

  it("renders iframe as link", () => {
    const article = makeArticle({
      content: [{
        id: "if1", type: "IFRAME", text: "", markups: [],
        iframe: { mediaResourceId: "mr1", iframeSrc: "https://youtube.com/embed/abc", title: "Demo Video" },
      }],
    });
    const md = renderArticleMarkdown(article);
    expect(md).toContain("▶ Demo Video");
    expect(md).toContain("https://youtube.com/embed/abc");
  });

  it("renders mixtape embed as blockquote link", () => {
    const article = makeArticle({
      content: [{
        id: "mx1", type: "MIXTAPE_EMBED", text: "", markups: [],
        mixtapeMetadata: { href: "https://example.com", title: "Example", description: "A great site" },
      }],
    });
    const md = renderArticleMarkdown(article);
    expect(md).toContain("[Example](https://example.com)");
    expect(md).toContain("A great site");
  });

  it("escapes markdown special characters in plain text", () => {
    const article = makeArticle({
      content: [{ id: "p1", type: "P", text: "Hello *world* [link]", markups: [] }],
    });
    const md = renderArticleMarkdown(article);
    expect(md).toContain("\\*world\\*");
    expect(md).toContain("\\[link\\]");
  });

  it("renders subtitle in front-matter", () => {
    const md = renderArticleMarkdown(makeArticle({ subtitle: "My subtitle" }));
    expect(md).toContain('subtitle: "My subtitle"');
  });

  it("renders paywalled flag correctly", () => {
    const md = renderArticleMarkdown(makeArticle({ isPaywalled: true }));
    expect(md).toContain("is_paywalled: true");
  });

  it("ends with footer attribution", () => {
    const md = renderArticleMarkdown(makeArticle());
    expect(md).toContain("Originally published on");
    expect(md).toContain("medium.com/@janedoe/hello-abc123");
  });
});
