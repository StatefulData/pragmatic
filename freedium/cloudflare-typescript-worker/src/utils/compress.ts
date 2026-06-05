/**
 * Gzip compression/decompression using the Web Streams CompressionStream API.
 *
 * Cloudflare Workers have had CompressionStream / DecompressionStream since
 * compatibility_date 2023-03-01. No Node.js zlib needed.
 *
 * Used to compress Paragraph[] JSON before storing in D1 and to decompress
 * it when reading back. Typical compression ratio for Medium article JSON
 * is 4–6×, taking a 30 KB article down to ~6 KB.
 */

/** Compress a UTF-8 string with gzip. Returns raw bytes. */
export async function gzipString(text: string): Promise<Uint8Array> {
  const input   = new TextEncoder().encode(text);
  const stream  = new CompressionStream("gzip");
  const writer  = stream.writable.getWriter();
  const reader  = stream.readable.getReader();

  const chunks: Uint8Array[] = [];
  const pump = async (): Promise<void> => {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      chunks.push(value);
    }
  };

  await Promise.all([
    (async () => {
      await writer.write(input);
      await writer.close();
    })(),
    pump(),
  ]);

  const totalLen = chunks.reduce((n, c) => n + c.length, 0);
  const result   = new Uint8Array(totalLen);
  let offset = 0;
  for (const chunk of chunks) {
    result.set(chunk, offset);
    offset += chunk.length;
  }
  return result;
}

/** Decompress gzip bytes and return the UTF-8 string. */
export async function gunzipString(bytes: ArrayBuffer | Uint8Array): Promise<string> {
  const input   = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  const stream  = new DecompressionStream("gzip");
  const writer  = stream.writable.getWriter();
  const reader  = stream.readable.getReader();

  const chunks: Uint8Array[] = [];
  const pump = async (): Promise<void> => {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      chunks.push(value);
    }
  };

  await Promise.all([
    (async () => {
      await writer.write(input);
      await writer.close();
    })(),
    pump(),
  ]);

  const totalLen = chunks.reduce((n, c) => n + c.length, 0);
  const result   = new Uint8Array(totalLen);
  let offset = 0;
  for (const chunk of chunks) {
    result.set(chunk, offset);
    offset += chunk.length;
  }
  return new TextDecoder().decode(result);
}

/** SHA-256 hex of an arbitrary string — used for content_hash. */
export async function sha256Hex(text: string): Promise<string> {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return Array.from(new Uint8Array(buf))
    .map(b => b.toString(16).padStart(2, "0"))
    .join("");
}
