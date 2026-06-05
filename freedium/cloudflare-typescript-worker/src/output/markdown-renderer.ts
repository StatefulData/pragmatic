/**
 * Markdown renderer — converts MediumArticle to Markdown with YAML front-matter.
 * Unchanged from v2. Images reference miro.medium.com — no bytes stored.
 */

import type { MediumArticle, Paragraph, MarkupRange } from "../types/article";
import { mediumImageUrl } from "../utils/urls";

function yamlEscape(s: string): string {
  return s.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
}

function yamlFrontMatter(a: MediumArticle): string {
  const date = new Date(a.publishedAt).toISOString().slice(0, 10);
  const tags = a.tags.map(t => `  - "${yamlEscape(t)}"`).join("\n");
  return [
    "---",
    `title: "${yamlEscape(a.title)}"`,
    a.subtitle   ? `subtitle: "${yamlEscape(a.subtitle)}"` : null,
    `author: "${yamlEscape(a.author.name)}"`,
    `author_username: "${yamlEscape(a.author.username)}"`,
    a.publication ? `publication: "${yamlEscape(a.publication.name)}"` : null,
    `published: ${date}`,
    a.readingTime ? `reading_time_min: ${Math.ceil(a.readingTime)}` : null,
    `is_paywalled: ${a.isPaywalled}`,
    a.language   ? `language: ${a.language}` : null,
    a.claps      ? `claps: ${a.claps}` : null,
    `source_url: "${a.url}"`,
    a.canonicalUrl && a.canonicalUrl !== a.url ? `canonical_url: "${a.canonicalUrl}"` : null,
    a.tags.length > 0 ? `tags:\n${tags}` : "tags: []",
    "---",
  ].filter(Boolean).join("\n");
}

const MD_ESC = /([*_`~[\]\\])/g;
function escapeMdChar(c: string): string { return c.replace(MD_ESC, "\\$1"); }
function escapeMdText(s: string): string { return s.replace(MD_ESC, "\\$1"); }

function applyMdMarkups(text: string, markups: MarkupRange[]): string {
  if (!markups.length) return escapeMdText(text);

  const opens:  Record<number, string[]> = {};
  const closes: Record<number, string[]> = {};

  for (const m of [...markups].sort((a, b) => a.start - b.start || b.end - a.end)) {
    const s = Math.max(0, m.start);
    const e = Math.min(text.length, m.end);
    if (s >= e) continue;
    const { open, close } = mdTokens(m);
    if (!open) continue;
    (opens[s]  ??= []).push(open);
    (closes[e] ??= []).unshift(close);
  }

  let result = "";
  for (let i = 0; i <= text.length; i++) {
    result += (closes[i] ?? []).join("");
    result += (opens[i]  ?? []).join("");
    if (i < text.length) result += escapeMdChar(text[i] ?? "");
  }
  return result;
}

function mdTokens(m: MarkupRange): { open: string; close: string } {
  switch (m.type) {
    case "STRONG":        return { open: "**", close: "**" };
    case "EM":            return { open: "_",  close: "_" };
    case "CODE":          return { open: "`",  close: "`" };
    case "STRIKETHROUGH": return { open: "~~", close: "~~" };
    case "U":             return { open: "_",  close: "_" };
    case "A":             return { open: "[",  close: `](${m.href ?? "#"})` };
    default:              return { open: "",   close: "" };
  }
}

function renderParagraphMd(para: Paragraph, listState: { olIdx: number }): string {
  const tx = applyMdMarkups(para.text, para.markups);
  switch (para.type) {
    case "P":   return `${tx}\n`;
    case "H1":  return `# ${tx}\n`;
    case "H2":  return `## ${tx}\n`;
    case "H3":  return `### ${tx}\n`;
    case "H4":  return `#### ${tx}\n`;
    case "BQ":  return `> ${tx}\n`;
    case "HR":  return `---\n`;
    case "PRE": {
      const lang = para.codeBlockMetadata?.lang ?? "";
      return `\`\`\`${lang}\n${para.text}\n\`\`\`\n`;
    }
    case "OLI": {
      listState.olIdx += 1;
      return `${listState.olIdx}. ${tx}\n`;
    }
    case "ULI": return `- ${tx}\n`;
    case "IMG": {
      if (!para.image) return "";
      const src = mediumImageUrl(para.image.id, 800);
      const alt = para.text || para.image.alt || "";
      const cap = alt ? `\n_${escapeMdText(alt)}_` : "";
      return `![${escapeMdText(alt)}](${src})${cap}\n`;
    }
    case "IFRAME": {
      if (!para.iframe?.iframeSrc) return "";
      return `[▶ ${escapeMdText(para.iframe.title ?? "Embedded content")}](${para.iframe.iframeSrc})\n`;
    }
    case "MIXTAPE_EMBED": {
      const m = para.mixtapeMetadata;
      if (!m?.href) return "";
      const desc = m.description ? ` — ${escapeMdText(m.description)}` : "";
      return `> **[${escapeMdText(m.title ?? "Link")}](${m.href})**${desc}\n`;
    }
    default: return tx ? `${tx}\n` : "";
  }
}

function renderParagraphs(paras: Paragraph[]): string {
  const parts: string[] = [];
  const listState = { olIdx: 0 };
  let prev = "";
  for (const para of paras) {
    if (para.type === "OLI" && prev !== "OLI") listState.olIdx = 0;
    if (prev !== "" && prev !== para.type) parts.push("");
    parts.push(renderParagraphMd(para, listState));
    prev = para.type;
  }
  return parts.join("\n");
}

export function renderArticleMarkdown(article: MediumArticle): string {
  const fm   = yamlFrontMatter(article);
  const hero = article.previewImage
    ? `![${escapeMdText(article.title)}](${mediumImageUrl(article.previewImage.id, 1200)})\n`
    : "";
  const byline = [
    `**${escapeMdText(article.author.name)}**`,
    article.publication ? `in ${escapeMdText(article.publication.name)}` : null,
    `· ${new Date(article.publishedAt).toLocaleDateString("en-US", { year:"numeric", month:"long", day:"numeric" })}`,
    article.readingTime ? `· ${Math.ceil(article.readingTime)} min read` : null,
    article.claps ? `· ${article.claps.toLocaleString()} claps` : null,
  ].filter(Boolean).join(" ");

  const footer = [
    "---",
    `_Originally published on [Medium](${article.url})._`,
    article.tags.length > 0 ? `_Tags: ${article.tags.map(t => `\`${t}\``).join(", ")}_` : null,
  ].filter(Boolean).join("\n");

  return [fm, "", hero, `# ${escapeMdText(article.title)}`, "", byline, "", renderParagraphs(article.content), "", footer, ""].join("\n");
}
