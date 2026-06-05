/**
 * Tests for the article parser.
 */
import { describe, it, expect } from "vitest";
import { parseRawPost } from "../src/parser/article-parser";
import type { RawMediumPost } from "../src/parser/medium-api";

function makeRawPost(overrides: Partial<RawMediumPost["post"]> = {}): RawMediumPost {
  return {
    post: {
      id: "abc123def456",
      title: "Test Article",
      subtitle: "A subtitle",
      previewImage: { id: "img1", originalWidth: 1200, originalHeight: 630, mimeType: "image/jpeg" },
      creator: {
        id: "user1",
        name: "John Doe",
        username: "johndoe",
        bio: "Writer",
        imageId: "avatar1",
        twitterScreenName: "johndoe",
      },
      collection: null as unknown as RawMediumPost["post"]["collection"],
      content: {
        bodyModel: {
          paragraphs: [
            { id: "p1", type: "H2", text: "Introduction", markups: [] },
            { id: "p2", type: "P", text: "Hello world", markups: [{ type: "STRONG", start: 6, end: 11 }] },
            { id: "p3", type: "PRE", text: "const x = 1;", markups: [], codeBlockMetadata: { lang: "javascript" } },
          ],
        },
      },
      tags: [{ id: "t1", displayTitle: "Programming" }, { id: "t2", displayTitle: "TypeScript" }],
      readingTime: 3.5,
      isPaywalled: true,
      firstPublishedAt: 1700000000000,
      updatedAt: 1700010000000,
      clapCount: 42,
      canonicalUrl: "https://medium.com/@johndoe/test-abc123def456",
      language: "en",
      ...overrides,
    },
  };
}

describe("parseRawPost", () => {
  it("parses basic article fields", () => {
    const raw = makeRawPost();
    const article = parseRawPost(raw, "https://medium.com/@johndoe/test-abc123def456");
    expect(article.id).toBe("abc123def456");
    expect(article.title).toBe("Test Article");
    expect(article.subtitle).toBe("A subtitle");
    expect(article.isPaywalled).toBe(true);
    expect(article.claps).toBe(42);
  });

  it("parses author correctly", () => {
    const article = parseRawPost(makeRawPost(), "https://medium.com/x");
    expect(article.author.name).toBe("John Doe");
    expect(article.author.username).toBe("johndoe");
  });

  it("parses tags as string array", () => {
    const article = parseRawPost(makeRawPost(), "https://medium.com/x");
    expect(article.tags).toEqual(["Programming", "TypeScript"]);
  });

  it("parses paragraphs with correct types", () => {
    const article = parseRawPost(makeRawPost(), "https://medium.com/x");
    expect(article.content).toHaveLength(3);
    expect(article.content[0]?.type).toBe("H2");
    expect(article.content[1]?.type).toBe("P");
    expect(article.content[2]?.type).toBe("PRE");
  });

  it("parses markup ranges on paragraphs", () => {
    const article = parseRawPost(makeRawPost(), "https://medium.com/x");
    const para = article.content[1]!;
    expect(para.markups).toHaveLength(1);
    expect(para.markups[0]?.type).toBe("STRONG");
    expect(para.markups[0]?.start).toBe(6);
  });

  it("parses code block language", () => {
    const article = parseRawPost(makeRawPost(), "https://medium.com/x");
    const codePara = article.content[2]!;
    expect(codePara.codeBlockMetadata?.lang).toBe("javascript");
  });

  it("handles null collection gracefully", () => {
    const article = parseRawPost(makeRawPost(), "https://medium.com/x");
    expect(article.publication).toBeUndefined();
  });

  it("maps unknown paragraph type to UNKNOWN", () => {
    const raw = makeRawPost();
    raw.post.content.bodyModel.paragraphs.push({
      id: "p4", type: "TOTALLY_UNKNOWN", text: "x", markups: [],
    } as Parameters<typeof makeRawPost>[0]["content"]["bodyModel"]["paragraphs"][0]);
    const article = parseRawPost(raw, "https://medium.com/x");
    const last = article.content[article.content.length - 1];
    expect(last?.type).toBe("UNKNOWN");
  });
});
