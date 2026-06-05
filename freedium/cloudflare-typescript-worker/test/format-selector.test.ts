/**
 * Tests for output format selection logic.
 */
import { describe, it, expect } from "vitest";
import { selectFormat } from "../src/output/format-selector";

function req(url: string, accept?: string): Request {
  return new Request(url, accept ? { headers: { Accept: accept } } : {});
}

describe("selectFormat", () => {
  it("returns html by default", () => {
    expect(selectFormat(req("https://proxy.example.com/https://medium.com/x"))).toBe("html");
  });

  it("honours ?format=markdown", () => {
    expect(selectFormat(req("https://proxy.example.com/x?format=markdown"))).toBe("markdown");
  });

  it("honours ?format=md alias", () => {
    expect(selectFormat(req("https://proxy.example.com/x?format=md"))).toBe("markdown");
  });

  it("honours ?format=json", () => {
    expect(selectFormat(req("https://proxy.example.com/x?format=json"))).toBe("json");
  });

  it("honours Accept: text/markdown header", () => {
    expect(selectFormat(req("https://proxy.example.com/x", "text/markdown"))).toBe("markdown");
  });

  it("honours Accept: application/json header", () => {
    expect(selectFormat(req("https://proxy.example.com/x", "application/json"))).toBe("json");
  });

  it("query param takes precedence over Accept header", () => {
    const r = new Request("https://proxy.example.com/x?format=markdown", {
      headers: { Accept: "application/json" },
    });
    expect(selectFormat(r)).toBe("markdown");
  });

  it("falls back to html for Accept: text/html", () => {
    expect(selectFormat(req("https://proxy.example.com/x", "text/html"))).toBe("html");
  });

  it("falls back to html for Accept: */*", () => {
    expect(selectFormat(req("https://proxy.example.com/x", "*/*"))).toBe("html");
  });
});
