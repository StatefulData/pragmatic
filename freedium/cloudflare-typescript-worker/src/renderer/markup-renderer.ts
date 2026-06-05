/**
 * Markup renderer — applies Medium markup ranges to text, producing HTML.
 * Unchanged from v2.
 */

import type { MarkupRange, Paragraph } from "../types/article";
import { mediumImageUrl } from "../utils/urls";

// ── Markup → HTML ─────────────────────────────────────────────────────────────

export function applyMarkups(text: string, markups: MarkupRange[]): string {
  if (!markups.length) return escapeHtml(text);

  const opens:  Record<number, string[]> = {};
  const closes: Record<number, string[]> = {};

  for (const m of [...markups].sort((a, b) => a.start - b.start || b.end - a.end)) {
    const s = Math.max(0, m.start);
    const e = Math.min(text.length, m.end);
    if (s >= e) continue;
    const { open, close } = htmlTokens(m);
    (opens[s]  ??= []).push(open);
    (closes[e] ??= []).unshift(close);
  }

  let result = "";
  for (let i = 0; i <= text.length; i++) {
    result += (closes[i] ?? []).join("");
    result += (opens[i]  ?? []).join("");
    if (i < text.length) result += escapeHtml(text[i] ?? "");
  }
  return result;
}

function htmlTokens(m: MarkupRange): { open: string; close: string } {
  switch (m.type) {
    case "STRONG":        return { open: "<strong>",  close: "</strong>" };
    case "EM":            return { open: "<em>",      close: "</em>" };
    case "CODE":          return { open: "<code>",    close: "</code>" };
    case "STRIKETHROUGH": return { open: "<s>",       close: "</s>" };
    case "U":             return { open: "<u>",       close: "</u>" };
    case "A": {
      const href = m.href ? ` href="${escapeAttr(m.href)}"` : "";
      return { open: `<a${href} target="_blank" rel="noopener noreferrer">`, close: "</a>" };
    }
    default: return { open: "", close: "" };
  }
}

export function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c] ?? c));
}
export function escapeAttr(s: string): string { return escapeHtml(s); }

// ── Paragraph → HTML ──────────────────────────────────────────────────────────

export interface RenderOptions { imageWidth?: number; lazyImages?: boolean; }
const DEFAULTS: Required<RenderOptions> = { imageWidth: 800, lazyImages: true };

export function renderParagraph(para: Paragraph, opts: RenderOptions = {}): string {
  const o  = { ...DEFAULTS, ...opts };
  const tx = applyMarkups(para.text, para.markups);
  switch (para.type) {
    case "P":   return `<p>${tx}</p>`;
    case "H1":  return `<h1>${tx}</h1>`;
    case "H2":  return `<h2>${tx}</h2>`;
    case "H3":  return `<h3>${tx}</h3>`;
    case "H4":  return `<h4>${tx}</h4>`;
    case "BQ":  return `<blockquote><p>${tx}</p></blockquote>`;
    case "HR":  return "<hr />";
    case "PRE": {
      const lang = para.codeBlockMetadata?.lang ?? "";
      return `<pre><code${lang ? ` class="language-${escapeAttr(lang)}"` : ""}>${escapeHtml(para.text)}</code></pre>`;
    }
    case "OLI": return `<li class="ordered">${tx}</li>`;
    case "ULI": return `<li class="unordered">${tx}</li>`;
    case "IMG": return renderImg(para.image, para.text, o);
    case "IFRAME": return renderIframe(para.iframe);
    case "MIXTAPE_EMBED": return renderMixtape(para.mixtapeMetadata);
    default: return tx ? `<p>${tx}</p>` : "";
  }
}

function renderImg(img: Paragraph["image"], alt: string, o: Required<RenderOptions>): string {
  if (!img) return "";
  const src   = mediumImageUrl(img.id, o.imageWidth);
  const altA  = escapeAttr(alt || img.alt || "");
  const lazy  = o.lazyImages ? ' loading="lazy"' : "";
  const w     = img.originalWidth  ? ` width="${img.originalWidth}"`  : "";
  const h     = img.originalHeight ? ` height="${img.originalHeight}"` : "";
  const cap   = alt ? `<figcaption>${escapeHtml(alt)}</figcaption>` : "";
  return `<figure class="article-image"><img src="${escapeAttr(src)}" alt="${altA}"${w}${h}${lazy} />${cap}</figure>`;
}

function renderIframe(iframe: Paragraph["iframe"]): string {
  if (!iframe) return "";
  if (iframe.iframeSrc) {
    return `<div class="article-embed"><iframe src="${escapeAttr(iframe.iframeSrc)}" title="${escapeAttr(iframe.title ?? "Embedded content")}" loading="lazy" frameborder="0" allowfullscreen></iframe></div>`;
  }
  if (iframe.thumbnailUrl) {
    const title = escapeHtml(iframe.title ?? "Embedded content");
    return `<figure class="article-embed"><img src="${escapeAttr(iframe.thumbnailUrl)}" alt="${title}" loading="lazy" /><figcaption>${title}</figcaption></figure>`;
  }
  return "";
}

function renderMixtape(m: Paragraph["mixtapeMetadata"]): string {
  if (!m?.href) return "";
  const title = escapeHtml(m.title ?? "");
  const desc  = m.description ? `<span class="mixtape-desc">${escapeHtml(m.description)}</span>` : "";
  const thumb = m.thumbnailImageId
    ? `<img src="${escapeAttr(mediumImageUrl(m.thumbnailImageId, 200))}" alt="${title}" loading="lazy" class="mixtape-thumb" />`
    : "";
  return `<a href="${escapeAttr(m.href)}" class="article-mixtape-embed" rel="noopener noreferrer" target="_blank">${thumb}<span class="mixtape-content"><strong class="mixtape-title">${title}</strong>${desc}</span></a>`;
}

// ── List grouping ─────────────────────────────────────────────────────────────

export function groupListItems(parts: string[]): string[] {
  const out: string[] = [];
  let i = 0;
  while (i < parts.length) {
    const p = parts[i] ?? "";
    if (p.startsWith('<li class="ordered">')) {
      const items: string[] = [];
      while (i < parts.length && (parts[i] ?? "").startsWith('<li class="ordered">')) items.push(parts[i++] ?? "");
      out.push(`<ol>${items.join("")}</ol>`);
    } else if (p.startsWith('<li class="unordered">')) {
      const items: string[] = [];
      while (i < parts.length && (parts[i] ?? "").startsWith('<li class="unordered">')) items.push(parts[i++] ?? "");
      out.push(`<ul>${items.join("")}</ul>`);
    } else { out.push(p); i++; }
  }
  return out;
}
