import type { OutputFormat } from "../types/article";

const MARKDOWN_TYPES = new Set(["text/markdown", "text/x-markdown", "text/plain"]);
const JSON_TYPES     = new Set(["application/json"]);

export function selectFormat(request: Request): OutputFormat {
  const url = new URL(request.url);
  const qf  = url.searchParams.get("format");
  if (qf === "markdown" || qf === "md") return "markdown";
  if (qf === "json")  return "json";
  if (qf === "html")  return "html";

  const accept = request.headers.get("Accept") ?? "";
  for (const mime of accept.split(",").map(p => p.trim().split(";")[0]?.trim() ?? "")) {
    if (MARKDOWN_TYPES.has(mime)) return "markdown";
    if (JSON_TYPES.has(mime))     return "json";
    if (mime === "text/html" || mime === "*/*") return "html";
  }
  return "html";
}

export function formatMimeType(fmt: OutputFormat): string {
  switch (fmt) {
    case "html":     return "text/html; charset=utf-8";
    case "markdown": return "text/markdown; charset=utf-8";
    case "json":     return "application/json; charset=utf-8";
  }
}
