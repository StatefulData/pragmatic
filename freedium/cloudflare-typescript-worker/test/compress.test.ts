/**
 * Tests for gzip compress/decompress utilities.
 * Uses the Web Streams CompressionStream API — available in Node.js 18+.
 */
import { describe, it, expect } from "vitest";
import { gzipString, gunzipString, sha256Hex } from "../src/utils/compress";

describe("gzipString / gunzipString", () => {
  it("round-trips a short string", async () => {
    const input = "Hello, Medium Proxy!";
    const gz    = await gzipString(input);
    expect(gz).toBeInstanceOf(Uint8Array);
    expect(gz.byteLength).toBeGreaterThan(0);
    const out = await gunzipString(gz);
    expect(out).toBe(input);
  });

  it("round-trips a large JSON payload", async () => {
    const payload = JSON.stringify(Array.from({ length: 200 }, (_, i) => ({
      id: `p${i}`, type: "P", text: `Paragraph ${i} with some content`.repeat(3), markups: [],
    })));
    const gz  = await gzipString(payload);
    const out = await gunzipString(gz);
    expect(out).toBe(payload);
  });

  it("compresses: gzip output is smaller than input for repetitive JSON", async () => {
    const input = JSON.stringify(Array.from({ length: 50 }, () => ({
      type: "P", text: "The quick brown fox jumps over the lazy dog.", markups: [],
    })));
    const gz = await gzipString(input);
    expect(gz.byteLength).toBeLessThan(input.length);
  });

  it("accepts ArrayBuffer as input to gunzipString", async () => {
    const input = "ArrayBuffer round-trip";
    const gz    = await gzipString(input);
    const ab    = gz.buffer as ArrayBuffer;
    const out   = await gunzipString(ab);
    expect(out).toBe(input);
  });

  it("round-trips Unicode content", async () => {
    const input = "日本語テスト — тест — اختبار — 测试 🎉";
    expect(await gunzipString(await gzipString(input))).toBe(input);
  });
});

describe("sha256Hex", () => {
  it("returns a 64-char hex string", async () => {
    const h = await sha256Hex("test");
    expect(h).toHaveLength(64);
    expect(h).toMatch(/^[0-9a-f]+$/);
  });

  it("is deterministic", async () => {
    expect(await sha256Hex("medium")).toBe(await sha256Hex("medium"));
  });

  it("differs for different inputs", async () => {
    expect(await sha256Hex("a")).not.toBe(await sha256Hex("b"));
  });
});
