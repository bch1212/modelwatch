/**
 * monitor() — wrap a function so every call is recorded by ModelWatch.
 * Monitoring failures are swallowed. The wrapped function's own exceptions
 * are re-raised unchanged.
 */

import { extractShape, fingerprint } from "./schemaHash.ts";
import { getReporter } from "./reporter.ts";

export interface MonitorOptions {
  toolName?: string;
  agentId?: string;
  tags?: Record<string, unknown>;
  captureShape?: boolean;
}

export function monitor<T extends (...args: any[]) => any>(
  fn: T,
  opts: MonitorOptions = {},
): T {
  const name = opts.toolName ?? fn.name ?? "anonymous";
  const agentId = opts.agentId ?? "default";
  const tags = opts.tags ?? {};
  const captureShape = opts.captureShape ?? true;

  const wrapped = function (this: unknown, ...args: any[]) {
    const start = performance.now();
    let result: unknown;
    try {
      result = fn.apply(this, args);
      if (result instanceof Promise) {
        return result.then(
          (value) => {
            recordSafe(name, agentId, start, value, null, tags, captureShape);
            return value;
          },
          (err: unknown) => {
            recordSafe(name, agentId, start, undefined, formatError(err), tags, captureShape);
            throw err;
          },
        );
      }
      recordSafe(name, agentId, start, result, null, tags, captureShape);
      return result;
    } catch (err) {
      recordSafe(name, agentId, start, undefined, formatError(err), tags, captureShape);
      throw err;
    }
  } as T;

  Object.defineProperty(wrapped, "name", { value: name, configurable: true });
  return wrapped;
}

function formatError(err: unknown): string {
  const text = err instanceof Error ? `${err.name}: ${err.message}` : String(err);
  return text.slice(0, 500);
}

function recordSafe(
  toolName: string,
  agentId: string,
  start: number,
  result: unknown,
  error: string | null,
  tags: Record<string, unknown>,
  captureShape: boolean,
): void {
  try {
    const latencyMs = Math.round(performance.now() - start);
    let shape: string | null = null;
    let shapeTree: unknown = null;
    if (captureShape && result !== undefined && result !== null && error === null) {
      try {
        shapeTree = extractShape(result);
        shape = fingerprint(result);
      } catch {
        // Shape extraction must not affect the caller.
      }
    }
    getReporter().record({
      tool_name: toolName,
      agent_id: agentId,
      latency_ms: latencyMs,
      success: error === null,
      error,
      output_shape: shape,
      output_shape_tree: shapeTree,
      tags,
      called_at: new Date().toISOString(),
    });
  } catch {
    // Last-resort guard.
  }
}
