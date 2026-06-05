/**
 * Article parser — transforms raw Medium GraphQL response into MediumArticle.
 */

import type { RawMediumPost } from "./medium-api";
import type {
  MediumArticle, Paragraph, ParagraphType,
  MarkupRange, ImageMetadata, IframeMetadata, MixtapeEmbed,
  Author, Publication,
} from "../types/article";

function mapParagraphType(raw: string): ParagraphType {
  const map: Record<string, ParagraphType> = {
    P:"P", H1:"H1", H2:"H2", H3:"H3", H4:"H4",
    PRE:"PRE", BQ:"BQ", IMG:"IMG", MIXTAPE_EMBED:"MIXTAPE_EMBED",
    OLI:"OLI", ULI:"ULI", IFRAME:"IFRAME", HR:"HR",
  };
  return map[raw] ?? "UNKNOWN";
}

function mapMarkupType(raw: string): MarkupRange["type"] {
  const map: Record<string, MarkupRange["type"]> = {
    STRONG:"STRONG", EM:"EM", CODE:"CODE", A:"A", STRIKETHROUGH:"STRIKETHROUGH", U:"U",
  };
  return map[raw] ?? "STRONG";
}

function parseImage(raw?: { id: string; originalWidth?: number; originalHeight?: number; mimeType?: string; focusPercentX?: number; focusPercentY?: number; alt?: string }): ImageMetadata | undefined {
  if (!raw) return undefined;
  return { id: raw.id, originalWidth: raw.originalWidth, originalHeight: raw.originalHeight,
           mimeType: raw.mimeType, focusPercentX: raw.focusPercentX,
           focusPercentY: raw.focusPercentY, alt: raw.alt };
}

function parseIframe(raw?: { mediaResourceId: string; iframeSrc?: string; thumbnailUrl?: string; title?: string; type?: string }): IframeMetadata | undefined {
  if (!raw) return undefined;
  return { mediaResourceId: raw.mediaResourceId, iframeSrc: raw.iframeSrc,
           thumbnailUrl: raw.thumbnailUrl, title: raw.title, type: raw.type };
}

function parseMixtape(raw?: { thumbnailImageId?: string; title?: string; description?: string; href?: string; mimeType?: string }): MixtapeEmbed | undefined {
  if (!raw) return undefined;
  return { thumbnailImageId: raw.thumbnailImageId, title: raw.title,
           description: raw.description, href: raw.href, mimeType: raw.mimeType };
}

export function parseRawPost(raw: RawMediumPost, url: string): MediumArticle {
  const p = raw.post;

  const author: Author = {
    id: p.creator.id, name: p.creator.name, username: p.creator.username,
    bio: p.creator.bio, imageId: p.creator.imageId, twitterScreenName: p.creator.twitterScreenName,
  };

  const publication: Publication | undefined = p.collection ? {
    id: p.collection.id, name: p.collection.name, description: p.collection.description,
    logoImageId: p.collection.logoImage?.id, domain: p.collection.domain,
  } : undefined;

  const content: Paragraph[] = (p.content?.bodyModel?.paragraphs ?? []).map(para => ({
    id: para.id, name: para.name,
    type: mapParagraphType(para.type),
    text: para.text,
    markups: (para.markups ?? []).map(m => ({
      type: mapMarkupType(m.type), start: m.start, end: m.end,
      href: m.href, anchorType: m.anchorType, userId: m.userId, linkMetadata: m.linkMetadata,
    })),
    layout: para.layout,
    image: parseImage(para.metadata),
    iframe: parseIframe(para.iframe),
    mixtapeMetadata: parseMixtape(para.mixtapeMetadata),
    codeBlockMetadata: para.codeBlockMetadata,
    dropCapImage: parseImage(para.dropCapImage as Parameters<typeof parseImage>[0]),
  }));

  return {
    id: p.id, title: p.title, subtitle: p.subtitle,
    previewImage: parseImage(p.previewImage),
    author, publication, content,
    tags: (p.tags ?? []).map(t => t.displayTitle),
    readingTime: p.readingTime, isPaywalled: p.isPaywalled,
    publishedAt: p.firstPublishedAt, updatedAt: p.updatedAt,
    claps: p.clapCount, url, canonicalUrl: p.canonicalUrl, language: p.language,
  };
}

export function parseHtmlFallback(html: string, url: string): Partial<MediumArticle> | null {
  const match = html.match(/window\.__APOLLO_STATE__\s*=\s*(\{[\s\S]*?\});\s*<\/script>/);
  if (!match?.[1]) return null;
  let state: Record<string, unknown>;
  try { state = JSON.parse(match[1]) as Record<string, unknown>; }
  catch { return null; }
  const postKey = Object.keys(state).find(k => k.startsWith("Post:"));
  if (!postKey) return null;
  const post = state[postKey] as Record<string, unknown>;
  return {
    id: (post["id"] as string | undefined) ?? "",
    title: (post["title"] as string | undefined) ?? "Untitled",
    subtitle: post["subtitle"] as string | undefined,
    url,
    isPaywalled: (post["isPaywalled"] as boolean | undefined) ?? false,
    publishedAt: (post["firstPublishedAt"] as number | undefined) ?? Date.now(),
    content: [], tags: [],
    author: { id: "", name: "Unknown", username: "unknown" },
  };
}
