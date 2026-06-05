/**
 * medium-proxy-worker v3 — Cloudflare Worker entry point.
 *
 * Storage: Workers KV (hot cache) + D1 (cold persistent store).
 * No Durable Objects, no R2, no external WASM.
 */

import { createRouter } from "./router/router";
import type { Env } from "./types/env";

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    return createRouter(env, ctx).handle(request);
  },
} satisfies ExportedHandler<Env>;
