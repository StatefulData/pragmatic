/**
 * Tests for the markup renderer.
 */
import { describe, it, expect } from "vitest";
import { applyMarkups, escapeHtml, groupListItems } from "../src/renderer/markup-renderer";
import type { MarkupRange } from "../src/types/article";

describe("escapeHtml", () => {
  it("escapes ampersands", () => expect(escapeHtml("a & b")).toBe("a &amp; b"));
  it("escapes angle brackets", () => expect(escapeHtml("<script>")).toBe("&lt;script&gt;"));
  it("escapes quotes", () => expect(escapeHtml('"hello"')).toBe("&quot;hello&quot;"));
  it("leaves plain text unchanged", () => expect(escapeHtml("hello world")).toBe("hello world"));
});

describe("applyMarkups", () => {
  it("returns escaped text with no markups", () => {
    expect(applyMarkups("hello & world", [])).toBe("hello &amp; world");
  });

  it("applies bold markup", () => {
    const markups: MarkupRange[] = [{ type: "STRONG", start: 0, end: 5 }];
    const result = applyMarkups("Hello world", markups);
    expect(result).toContain("<strong>Hello</strong>");
  });

  it("applies link markup with href", () => {
    const markups: MarkupRange[] = [{ type: "A", start: 0, end: 4, href: "https://example.com" }];
    const result = applyMarkups("link text", markups);
    expect(result).toContain('<a href="https://example.com"');
    expect(result).toContain("link");
  });

  it("applies code markup", () => {
    const markups: MarkupRange[] = [{ type: "CODE", start: 4, end: 8 }];
    expect(applyMarkups("use code here", markups)).toContain("<code>code</code>");
  });

  it("handles non-overlapping multiple markups", () => {
    const markups: MarkupRange[] = [
      { type: "STRONG", start: 0, end: 5 },
      { type: "EM", start: 6, end: 11 },
    ];
    const result = applyMarkups("Hello world", markups);
    expect(result).toContain("<strong>Hello</strong>");
    expect(result).toContain("<em>world</em>");
  });
});

describe("groupListItems", () => {
  it("wraps ordered list items in <ol>", () => {
    const parts = [
      '<li class="ordered">First</li>',
      '<li class="ordered">Second</li>',
      "<p>After</p>",
    ];
    const result = groupListItems(parts);
    expect(result).toHaveLength(2);
    expect(result[0]).toMatch(/^<ol>/);
    expect(result[0]).toContain("First");
    expect(result[0]).toContain("Second");
  });

  it("wraps unordered list items in <ul>", () => {
    const parts = [
      '<li class="unordered">A</li>',
      '<li class="unordered">B</li>',
    ];
    const [result] = groupListItems(parts);
    expect(result).toMatch(/^<ul>/);
  });

  it("leaves non-list items unchanged", () => {
    const parts = ["<p>Para</p>", "<blockquote><p>Quote</p></blockquote>"];
    expect(groupListItems(parts)).toEqual(parts);
  });
});
