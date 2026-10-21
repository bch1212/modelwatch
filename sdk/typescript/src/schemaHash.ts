/**
 * Schema fingerprinting — hash the structure of an object, not the values.
 * Two responses with the same shape produce the same fingerprint.
 *
 * Kept aligned with sdk/modelwatch/schema.py and app/services/tool_schema.py.
 */

import { createHash } from "node:crypto";

export type Shape = string | { [k: string]: Shape } | Shape[];

export function fingerprint(obj: unknown): string {
  const shape = extractShape(obj);
  const canonical = JSON.stringify(sortKeys(shape));
  return createHash("sha256").update(canonical).digest("hex").slice(0, 16);
}

export function extractShape(obj: unknown): Shape {
  if (obj === null || obj === undefined) return "null";
  if (typeof obj === "boolean") return "bool";
  if (typeof obj === "number") return Number.isInteger(obj) ? "int" : "float";
  if (typeof obj === "string") return "str";
  if (Array.isArray(obj)) {
    if (obj.length === 0) return ["__empty__"];
    let merged = extractShape(obj[0]);
    for (let i = 1; i < obj.length; i++) {
      merged = mergeShapes(merged, extractShape(obj[i]));
    }
    return [merged];
  }
  if (typeof obj === "object") {
    const out: Record<string, Shape> = {};
    for (const k of Object.keys(obj as object).sort()) {
      out[k] = extractShape((obj as Record<string, unknown>)[k]);
    }
    return out;
  }
  return typeof obj;
}

function mergeShapes(a: Shape, b: Shape): Shape {
  if (shapeEqual(a, b)) return a;
  if (isObj(a) && isObj(b)) {
    const merged: Record<string, Shape> = {};
    const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
    for (const k of [...keys].sort()) {
      if (k in a && k in b) merged[k] = mergeShapes(a[k], b[k]);
      else if (k in a) merged[k] = ["optional", a[k]];
      else merged[k] = ["optional", b[k]];
    }
    return merged;
  }
  if (Array.isArray(a) && Array.isArray(b)) {
    if (a.length === 0) return b;
    if (b.length === 0) return a;
    return [mergeShapes(a[0], b[0])];
  }
  return ["union", [String(a), String(b)].sort()];
}

function isObj(s: Shape): s is { [k: string]: Shape } {
  return typeof s === "object" && !Array.isArray(s) && s !== null;
}

function shapeEqual(a: Shape, b: Shape): boolean {
  return JSON.stringify(sortKeys(a)) === JSON.stringify(sortKeys(b));
}

function sortKeys(s: Shape): Shape {
  if (isObj(s)) {
    const out: Record<string, Shape> = {};
    for (const k of Object.keys(s).sort()) out[k] = sortKeys(s[k]);
    return out;
  }
  if (Array.isArray(s)) return s.map(sortKeys);
  return s;
}
