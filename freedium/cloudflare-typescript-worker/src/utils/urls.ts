/** URL utilities for Medium article URL parsing and normalisation. */

const MEDIUM_DOMAINS = new Set([
  "medium.com", "www.medium.com",
  "towardsdatascience.com", "betterprogramming.pub",
  "levelup.gitconnected.com", "itnext.io",
  "uxdesign.cc", "entrepreneurshandbook.co",
  "thestartup.medium.com",
]);

/** Extract the post ID (8–12 hex chars) from the tail of a Medium URL slug. */
export function extractPostId(url: string): string | null {
  try {
    const parsed = new URL(url);
    const segments = parsed.pathname.split("/").filter(Boolean);
    const last = segments[segments.length - 1];
    if (!last) return null;
    return last.match(/([a-f0-9]{8,12})$/i)?.[1] ?? null;
  } catch { return null; }
}

export function isMediumUrl(rawUrl: string): boolean {
  try {
    const url = new URL(rawUrl);
    const host = url.hostname.toLowerCase();
    return host === "medium.com" || host.endsWith(".medium.com") || MEDIUM_DOMAINS.has(host);
  } catch { return false; }
}

/**
 * Extract the target Medium URL from an incoming proxy request URL.
 * Handles:
 *   /https://medium.com/...   → full URL in path
 *   /@user/slug               → relative path → medium.com/<path>
 */
export function extractTargetUrl(requestUrl: string): string | null {
  try {
    const url = new URL(requestUrl);
    const path = url.pathname.slice(1);
    if (path.startsWith("https://") || path.startsWith("http://")) {
      return new URL(path).href;
    }
    if (path.length > 1) return `https://medium.com/${path}`;
    return null;
  } catch { return null; }
}

/** Normalise a Medium URL: strip query/fragment, lowercase host. */
export function normaliseMediumUrl(rawUrl: string): string {
  const url = new URL(rawUrl);
  url.search = "";
  url.hash = "";
  url.hostname = url.hostname.toLowerCase();
  return url.href;
}

/**
 * Construct a miro.medium.com CDN URL from a Medium image ID.
 * Images are always served live from Medium's CDN — never stored locally.
 *
 * @param imageId  Medium image identifier (e.g. "1*AbCdEfGhIjKlMnOp")
 * @param width    Requested width for the resize:fit transform (default 800)
 */
export function mediumImageUrl(imageId: string, width = 800): string {
  return `https://miro.medium.com/v2/resize:fit:${width}/${imageId}`;
}

/** Derive a stable SHA-256 hex ID from a canonical URL string. */
export async function urlToId(canonicalUrl: string): Promise<string> {
  const data = new TextEncoder().encode(normaliseMediumUrl(canonicalUrl));
  const buf  = await crypto.subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(buf))
    .map(b => b.toString(16).padStart(2, "0"))
    .join("");
}
