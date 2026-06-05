/** Structured JSON logger for Cloudflare Workers. */

type Level = "debug" | "info" | "warn" | "error";

const LEVEL_ORDER: Record<Level, number> = {
  debug: 0, info: 1, warn: 2, error: 3,
};

export class Logger {
  private readonly minLevel: number;

  constructor(level: Level = "info") {
    this.minLevel = LEVEL_ORDER[level];
  }

  private log(level: Level, message: string, meta?: Record<string, unknown>): void {
    if (LEVEL_ORDER[level] < this.minLevel) return;
    const entry = { level, message, timestamp: new Date().toISOString(), ...meta };
    // eslint-disable-next-line no-console
    console[level === "debug" ? "log" : level](JSON.stringify(entry));
  }

  debug(message: string, meta?: Record<string, unknown>): void { this.log("debug", message, meta); }
  info (message: string, meta?: Record<string, unknown>): void { this.log("info",  message, meta); }
  warn (message: string, meta?: Record<string, unknown>): void { this.log("warn",  message, meta); }
  error(message: string, meta?: Record<string, unknown>): void { this.log("error", message, meta); }
}
