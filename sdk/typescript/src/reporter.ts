/**
 * Batch reporter. Never blocks the caller and never throws.
 * With no API key, or with a non-http(s) base URL, nothing is sent.
 */

export interface ReporterConfig {
  apiKey?: string;
  endpoint?: string;
  batchSize?: number;
  flushIntervalMs?: number;
  enabled?: boolean;
}

export interface ToolCallRecord {
  tool_name: string;
  agent_id: string;
  latency_ms: number;
  success: boolean;
  error: string | null;
  output_shape: string | null;
  output_shape_tree: unknown;
  tags: Record<string, unknown>;
  called_at: string;
}

export const DEFAULT_ENDPOINT = "https://api.modelwatch.app";
export const INGEST_PATH = "/api/tools/ingest";

const DEFAULTS: Required<ReporterConfig> = {
  apiKey: process.env.MODELWATCH_API_KEY ?? "",
  endpoint: process.env.MODELWATCH_API_BASE ?? DEFAULT_ENDPOINT,
  batchSize: 50,
  flushIntervalMs: 5000,
  enabled: true,
};

let cfg: Required<ReporterConfig> = { ...DEFAULTS };

export function configure(overrides: ReporterConfig): void {
  cfg = {
    ...cfg,
    ...overrides,
    apiKey: overrides.apiKey === undefined ? cfg.apiKey : overrides.apiKey ?? "",
    endpoint: overrides.endpoint === undefined ? cfg.endpoint : overrides.endpoint ?? "",
  };
}

export function ingestUrl(endpoint: string): string | null {
  const trimmed = endpoint.trim().replace(/\/$/, "");
  if (!trimmed.startsWith("https://") && !trimmed.startsWith("http://")) return null;
  return `${trimmed}${INGEST_PATH}`;
}

export function ingestHeaders(apiKey: string): Record<string, string> {
  return {
    "Content-Type": "application/json",
    Authorization: `Bearer ${apiKey}`,
  };
}

class Reporter {
  private queue: ToolCallRecord[] = [];
  private timer: ReturnType<typeof setInterval> | null = null;
  private static _instance: Reporter | null = null;

  static instance(): Reporter {
    if (!this._instance) this._instance = new Reporter();
    return this._instance;
  }

  private constructor() {
    this.timer = setInterval(() => void this.flush(), cfg.flushIntervalMs);
    if (typeof this.timer === "object" && this.timer && "unref" in this.timer) {
      this.timer.unref();
    }
    if (typeof process !== "undefined" && process.on) {
      process.on("beforeExit", () => void this.flush());
    }
  }

  record(rec: ToolCallRecord): void {
    if (!cfg.enabled || !cfg.apiKey || !ingestUrl(cfg.endpoint)) return;
    this.queue.push(rec);
    if (this.queue.length >= cfg.batchSize) void this.flush();
  }

  async flush(): Promise<void> {
    if (this.queue.length === 0) return;
    const url = ingestUrl(cfg.endpoint);
    const apiKey = cfg.apiKey;
    if (!url || !apiKey) return;
    const batch = this.queue.splice(0, this.queue.length);
    try {
      const res = await fetch(url, {
        method: "POST",
        headers: ingestHeaders(apiKey),
        body: JSON.stringify(batch),
      });
      if (!res.ok && process.env.MODELWATCH_DEBUG) {
        console.warn(`[modelwatch] tool ingest non-2xx: ${res.status}`);
      }
    } catch (err) {
      if (process.env.MODELWATCH_DEBUG) {
        console.warn("[modelwatch] tool ingest failed:", err instanceof Error ? err.name : "error");
      }
    }
  }
}

export function getReporter(): Reporter {
  return Reporter.instance();
}
