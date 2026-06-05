/**
 * Tests for URL utilities.
 * Run with: npm test
 */
import { describe, it, expect } from "vitest";
import { extractPostId, isMediumUrl, extractTargetUrl, normaliseMediumUrl } from "../src/utils/urls";

describe("extractPostId", () => {
  it("extracts post ID from standard medium URL", () => {
    const url = "https://medium.com/@user/my-article-title-a1b2c3d4e5f6";
    expect(extractPostId(url)).toBe("a1b2c3d4e5f6");
  });

  it("extracts post ID from publication URL", () => {
    const url = "https://medium.com/publication/article-abc123def456";
    expect(extractPostId(url)).toBe("abc123def456");
  });

  it("returns null for URL without post ID", () => {
    expect(extractPostId("https://medium.com/@user")).toBeNull();
  });

  it("returns null for invalid URL", () => {
    expect(extractPostId("not-a-url")).toBeNull();
  });
});

describe("isMediumUrl", () => {
  it("accepts medium.com", () => {
    expect(isMediumUrl("https://medium.com/some/article")).toBe(true);
  });

  it("accepts subdomain.medium.com", () => {
    expect(isMediumUrl("https://towardsdatascience.com/article-abc123")).toBe(true);
  });

  it("rejects non-medium URLs", () => {
    expect(isMediumUrl("https://example.com/blog/post")).toBe(false);
  });

  it("rejects invalid URLs", () => {
    expect(isMediumUrl("not a url")).toBe(false);
  });
});

describe("extractTargetUrl", () => {
  it("extracts full URL embedded in path", () => {
    const proxy = "https://proxy.example.com/https://medium.com/@user/article-abc123";
    expect(extractTargetUrl(proxy)).toBe("https://medium.com/@user/article-abc123");
  });

  it("constructs medium.com URL from relative path", () => {
    const proxy = "https://proxy.example.com/@user/article-abc123";
    expect(extractTargetUrl(proxy)).toBe("https://medium.com/@user/article-abc123");
  });

  it("returns null for root path", () => {
    expect(extractTargetUrl("https://proxy.example.com/")).toBeNull();
  });
});

describe("normaliseMediumUrl", () => {
  it("strips query params and fragments", () => {
    const url = "https://medium.com/@user/article-abc123?source=email#top";
    const normalised = normaliseMediumUrl(url);
    expect(normalised).not.toContain("source=");
    expect(normalised).not.toContain("#top");
  });

  it("lowercases the hostname", () => {
    const url = "https://Medium.COM/@user/article-abc123";
    expect(normaliseMediumUrl(url)).toContain("medium.com");
  });
});
