/**
 * HTML page renderer — v3.
 * Converts Paragraph[] + article metadata into a complete HTML page.
 * Images reference miro.medium.com directly — no bytes stored locally.
 */

import type { MediumArticle } from "../types/article";
import { renderParagraph, groupListItems, escapeHtml, escapeAttr } from "./markup-renderer";
import { mediumImageUrl } from "../utils/urls";

// ── CSS ───────────────────────────────────────────────────────────────────────

const CSS = `
:root{--bg:#fff;--surface:#f9f9f9;--text:#1a1a1a;--muted:#555;--accent:#1a8917;--border:#e0e0e0;--code-bg:#f4f4f4;--bq-border:#ccc;--sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;--serif:Georgia,"Times New Roman",serif;--mono:"SFMono-Regular",Consolas,"Liberation Mono",Menlo,monospace}
@media(prefers-color-scheme:dark){:root{--bg:#121212;--surface:#1e1e1e;--text:#e8e8e8;--muted:#aaa;--accent:#3aa82c;--border:#333;--code-bg:#2a2a2a;--bq-border:#555}}
*,*::before,*::after{box-sizing:border-box}html{font-size:18px}
body{margin:0;background:var(--bg);color:var(--text);font-family:var(--serif);line-height:1.75}
.site-header{background:var(--surface);border-bottom:1px solid var(--border);padding:.75rem 1.5rem;display:flex;align-items:center;gap:1rem}
.site-header a{color:var(--accent);text-decoration:none;font-family:var(--sans);font-weight:700;font-size:1.1rem}
.site-header form{display:flex;flex:1;gap:.5rem;max-width:600px}
.site-header input{flex:1;padding:.4rem .75rem;border:1px solid var(--border);border-radius:4px;background:var(--bg);color:var(--text);font-size:.9rem;font-family:var(--sans)}
.site-header button{padding:.4rem 1rem;background:var(--accent);color:#fff;border:none;border-radius:4px;cursor:pointer;font-family:var(--sans);font-size:.9rem}
.format-bar{background:var(--surface);border-bottom:1px solid var(--border);padding:.35rem 1.5rem;display:flex;align-items:center;gap:.6rem;font-family:var(--sans);font-size:.78rem;color:var(--muted)}
.format-bar a{color:var(--accent);text-decoration:none;padding:.15rem .45rem;border-radius:3px}
.format-bar a:hover{background:var(--border)}
.format-bar a.active{background:var(--accent);color:#fff}
.format-bar .source-badge{margin-left:auto;font-size:.72rem;padding:.1rem .4rem;border-radius:3px;border:1px solid var(--border)}
.format-bar .source-badge.kv{color:#1a8917;border-color:#1a8917}
.format-bar .source-badge.d1{color:#0066cc;border-color:#0066cc}
.format-bar .source-badge.net{color:#cc6600;border-color:#cc6600}
.paywall-banner{background:#fff3cd;color:#856404;border-bottom:1px solid #ffc107;padding:.65rem 1.5rem;text-align:center;font-family:var(--sans);font-size:.85rem}
.wrapper{max-width:740px;margin:2.5rem auto;padding:0 1.25rem}
.article-meta{font-family:var(--sans);margin-bottom:1.5rem}
.pub-label{color:var(--accent);font-weight:600;text-transform:uppercase;font-size:.72rem;letter-spacing:.08em;margin-bottom:.5rem}
.article-title{font-family:var(--serif);font-size:clamp(1.6rem,4vw,2.4rem);font-weight:700;line-height:1.25;margin:0 0 .5rem}
.article-subtitle{font-size:1.1rem;color:var(--muted);margin:0 0 1.2rem;font-weight:400}
.author-row{display:flex;align-items:center;gap:.75rem;font-size:.88rem}
.author-avatar{width:40px;height:40px;border-radius:50%;object-fit:cover}
.author-name{font-weight:600}.article-date,.reading-time{color:var(--muted);font-size:.82rem}
.tags{margin-top:.65rem;display:flex;flex-wrap:wrap;gap:.35rem}
.tags span{background:var(--surface);border:1px solid var(--border);border-radius:99px;padding:.12rem .6rem;font-size:.75rem;color:var(--muted)}
.hero{margin:1.5rem 0}.hero img{width:100%;height:auto;border-radius:4px;display:block}
.body{margin-top:2rem}
.body p{margin:0 0 1.25em}
.body h1,.body h2,.body h3,.body h4{font-family:var(--serif);margin:1.75em 0 .5em;line-height:1.3}
.body h2{font-size:1.5rem}.body h3{font-size:1.25rem}
.body blockquote{border-left:3px solid var(--bq-border);margin:1.5em 0;padding:.25em 1.25em;color:var(--muted);font-style:italic}
.body pre{background:var(--code-bg);border-radius:6px;padding:1.25rem;overflow-x:auto;font-size:.83rem;margin:1.5em 0;line-height:1.5}
.body code{font-family:var(--mono);background:var(--code-bg);padding:.1em .3em;border-radius:3px;font-size:.85em}
.body pre code{background:none;padding:0;font-size:inherit}
.body a{color:var(--accent);text-decoration:underline;text-underline-offset:2px}
.body ul,.body ol{padding-left:1.5em;margin:1em 0}.body li{margin:.35em 0}
.body hr{border:none;border-top:1px solid var(--border);margin:2.5em 0}
.article-image{margin:1.75em 0;text-align:center}
.article-image img{max-width:100%;height:auto;border-radius:4px}
.article-image figcaption{font-size:.78rem;color:var(--muted);margin-top:.5em;font-family:var(--sans);font-style:italic}
.article-embed{margin:1.75em 0}.article-embed iframe{width:100%;min-height:300px;border-radius:6px;border:1px solid var(--border)}
.article-mixtape-embed{display:flex;align-items:stretch;border:1px solid var(--border);border-radius:6px;text-decoration:none;color:var(--text);overflow:hidden;margin:1.5em 0;font-family:var(--sans);transition:box-shadow .15s}
.article-mixtape-embed:hover{box-shadow:0 2px 8px rgba(0,0,0,.1)}
.mixtape-thumb{width:120px;object-fit:cover;flex-shrink:0}
.mixtape-content{padding:.75rem 1rem;display:flex;flex-direction:column;justify-content:center;gap:.25rem}
.mixtape-title{font-size:.88rem}.mixtape-desc{font-size:.78rem;color:var(--muted)}
.site-footer{margin-top:4rem;padding:2rem 1.5rem;border-top:1px solid var(--border);text-align:center;font-family:var(--sans);font-size:.78rem;color:var(--muted)}
.site-footer a{color:var(--accent)}
.home-form{max-width:600px;margin:4rem auto;padding:0 1.5rem;text-align:center;font-family:var(--sans)}
.home-form h1{font-size:2rem;margin-bottom:.5rem}
.home-form p{color:var(--muted);margin-bottom:2rem}
.home-form .row{display:flex;gap:.5rem}
.home-form input{flex:1;padding:.65rem 1rem;border:2px solid var(--border);border-radius:6px;font-size:1rem;background:var(--bg);color:var(--text)}
.home-form input:focus{outline:none;border-color:var(--accent)}
.home-form button{padding:.65rem 1.5rem;background:var(--accent);color:#fff;border:none;border-radius:6px;font-size:1rem;cursor:pointer;font-weight:600}
.error-box{max-width:600px;margin:4rem auto;padding:2rem;text-align:center;font-family:var(--sans)}
.error-box h1{color:#c0392b}
@media(max-width:480px){.mixtape-thumb{display:none}.home-form .row{flex-direction:column}}
`.trim();

// ── Shell ─────────────────────────────────────────────────────────────────────

function shell(opts: { title: string; description?: string; ogImage?: string; canonicalUrl?: string; body: string }): string {
  const metas = [
    `<meta charset="UTF-8">`,
    `<meta name="viewport" content="width=device-width,initial-scale=1.0">`,
    `<title>${escapeHtml(opts.title)}</title>`,
    opts.description ? `<meta name="description" content="${escapeAttr(opts.description)}">` : "",
    `<meta property="og:title" content="${escapeAttr(opts.title)}">`,
    opts.description ? `<meta property="og:description" content="${escapeAttr(opts.description)}">` : "",
    opts.ogImage ? `<meta property="og:image" content="${escapeAttr(opts.ogImage)}">` : "",
    opts.canonicalUrl ? `<link rel="canonical" href="${escapeAttr(opts.canonicalUrl)}">` : "",
    `<meta name="robots" content="noindex,nofollow">`,
  ].filter(Boolean).join("\n    ");

  return `<!DOCTYPE html>
<html lang="en">
<head>
    ${metas}
    <style>${CSS}</style>
</head>
<body>
${opts.body}
</body>
</html>`;
}

function header(): string {
  return `<header class="site-header">
  <a href="/">Medium Proxy</a>
  <form action="/" method="get">
    <input type="text" name="url" placeholder="Paste a Medium article URL…" autocomplete="off" />
    <button type="submit">Read</button>
  </form>
</header>`;
}

function footer(): string {
  return `<footer class="site-footer">
  <p>Open proxy for educational &amp; research purposes. Content belongs to original authors on <a href="https://medium.com" rel="noopener">Medium</a>. Images served from <a href="https://miro.medium.com" rel="noopener">miro.medium.com</a>.</p>
</footer>`;
}

// ── Public API ────────────────────────────────────────────────────────────────

/**
 * Render a full article HTML page.
 * @param article    The parsed MediumArticle
 * @param proxyPath  The path portion of the proxy URL (used to build format switcher links)
 * @param source     Where the article was served from (kv | d1 | network)
 */
export function renderArticlePage(
  article: MediumArticle,
  proxyPath: string,
  source: "kv" | "d1" | "network" = "network",
): string {
  const dateStr = new Date(article.publishedAt).toLocaleDateString("en-US", {
    year: "numeric", month: "long", day: "numeric",
  });

  const enc = encodeURIComponent(proxyPath);

  const sourceLabel = source === "kv"  ? "KV cache"
    : source === "d1" ? "D1 store"
    : "live fetch";
  const sourceCls = source === "kv" ? "kv" : source === "d1" ? "d1" : "net";

  const formatBar = `<div class="format-bar">
  <span>View as:</span>
  <a href="${enc}?format=html"     class="active">HTML</a>
  <a href="${enc}?format=markdown">Markdown</a>
  <a href="${enc}?format=json">JSON</a>
  <span class="source-badge ${sourceCls}">${sourceLabel}</span>
</div>`;

  const paywallBanner = article.isPaywalled
    ? `<div class="paywall-banner">⚠️ Paywalled on Medium — served via subscription credentials.</div>`
    : "";

  const avatar = article.author.imageId
    ? `<img src="${escapeAttr(mediumImageUrl(article.author.imageId, 80))}" alt="${escapeAttr(article.author.name)}" class="author-avatar" loading="lazy" />`
    : "";

  const hero = article.previewImage
    ? `<div class="hero"><img src="${escapeAttr(mediumImageUrl(article.previewImage.id, 1200))}" alt="${escapeAttr(article.title)}" loading="eager" /></div>`
    : "";

  const bodyParts  = article.content.map(p => renderParagraph(p));
  const bodyHtml   = groupListItems(bodyParts).join("\n");

  const tags = article.tags.length > 0
    ? `<div class="tags">${article.tags.map(t => `<span>${escapeHtml(t)}</span>`).join("")}</div>`
    : "";

  const body = `
${header()}
${paywallBanner}
${formatBar}
<main class="wrapper">
  <article>
    <header class="article-meta">
      ${article.publication ? `<div class="pub-label">${escapeHtml(article.publication.name)}</div>` : ""}
      <h1 class="article-title">${escapeHtml(article.title)}</h1>
      ${article.subtitle ? `<p class="article-subtitle">${escapeHtml(article.subtitle)}</p>` : ""}
      <div class="author-row">
        ${avatar}
        <div>
          <div class="author-name">${escapeHtml(article.author.name)}</div>
          <div>
            <span class="article-date">${dateStr}</span>
            ${article.readingTime ? ` · <span class="reading-time">${Math.ceil(article.readingTime)} min read</span>` : ""}
            ${article.claps ? ` · <span>${article.claps.toLocaleString()} claps</span>` : ""}
          </div>
        </div>
      </div>
      ${tags}
    </header>
    ${hero}
    <div class="body">${bodyHtml}</div>
  </article>
</main>
${footer()}`;

  return shell({
    title:       `${article.title} — Medium Proxy`,
    description: article.subtitle,
    ogImage:     article.previewImage ? mediumImageUrl(article.previewImage.id, 1200) : undefined,
    canonicalUrl: article.url,
    body,
  });
}

export function renderHomePage(prefill = ""): string {
  const body = `
${header()}
<div class="home-form">
  <h1>Medium Proxy</h1>
  <p>Paste a Medium article URL to read it without a paywall prompt.</p>
  <form action="/" method="get">
    <div class="row">
      <input type="text" name="url" value="${escapeAttr(prefill)}"
             placeholder="https://medium.com/…" autocomplete="off" autofocus />
      <button type="submit">Read</button>
    </div>
  </form>
</div>
${footer()}`;
  return shell({ title: "Medium Proxy — Read without paywalls", body });
}

export function renderErrorPage(status: number, message: string, detail?: string): string {
  const body = `
${header()}
<div class="error-box">
  <h1>${status}</h1>
  <p>${escapeHtml(message)}</p>
  ${detail ? `<p style="font-size:.82rem;color:var(--muted)">${escapeHtml(detail)}</p>` : ""}
  <p><a href="/">← Back to home</a></p>
</div>
${footer()}`;
  return shell({ title: `${status} — Medium Proxy`, body });
}
