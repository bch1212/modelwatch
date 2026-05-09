#!/usr/bin/env node
// ModelWatch MCP server — wraps the ModelWatch REST API as MCP tools.
//
// Tools:
//   - list_endpoints      List monitored LLM endpoints in the workspace.
//   - create_endpoint     Add an endpoint to monitor.
//   - list_specs          List behavioral specs (drift checks).
//   - create_spec         Create a spec; first run sets the baseline.
//   - run_spec            Run a spec on demand and get the drift score.
//   - reset_baseline      Re-baseline a spec (use after intentional model change).
//   - get_drift_events    Fetch recent drift events for the workspace.
//   - get_spec_history    Pull the run history for a single spec.
//   - get_health          Workspace KPIs — plan, spec count, runs this month.
//
// Configuration (env):
//   MODELWATCH_API_KEY    Required. mw_… key from https://modelwatch.app
//   MODELWATCH_API_BASE   Optional. Defaults to https://api.modelwatch.app
//
// Transport: stdio.

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
  ErrorCode,
  McpError,
} from "@modelcontextprotocol/sdk/types.js";

const API_BASE =
  process.env.MODELWATCH_API_BASE || "https://api.modelwatch.app";
const API_KEY = process.env.MODELWATCH_API_KEY || "";

const PKG_VERSION = "0.1.0";

type Provider = "openai" | "anthropic";
type Frequency = "hourly" | "daily" | "weekly";
type Threshold = "low" | "medium" | "high" | "critical";

const TOOL_DEFINITIONS = [
  {
    name: "list_endpoints",
    description:
      "List the LLM endpoints currently monitored in this workspace. " +
      "Returns id, name, provider, model, base_url, created_at for each.",
    inputSchema: { type: "object", properties: {} },
  },
  {
    name: "create_endpoint",
    description:
      "Register an LLM endpoint to monitor. The workspace must already have " +
      "a stored API key for the provider (use the dashboard to add one). " +
      "Returns the new endpoint's id.",
    inputSchema: {
      type: "object",
      properties: {
        name: { type: "string", description: "Human-readable label, e.g. 'GPT-4o mini prod'." },
        provider: { type: "string", enum: ["openai", "anthropic"] },
        model: { type: "string", description: "Model identifier, e.g. 'gpt-4o-mini' or 'claude-sonnet-4-6'." },
        base_url: {
          type: "string",
          description: "Optional. Override base URL for OpenAI-compatible endpoints (vLLM, LiteLLM, Together).",
        },
      },
      required: ["name", "provider", "model"],
    },
  },
  {
    name: "list_specs",
    description:
      "List behavioral specs in the workspace. A spec is a stored prompt + " +
      "expectation that ModelWatch replays on a schedule and diffs against a " +
      "baseline. Returns id, name, prompt, frequency, threshold, last_severity, " +
      "and the parent endpoint_id.",
    inputSchema: {
      type: "object",
      properties: {
        endpoint_id: { type: "string", description: "Optional. Filter by endpoint." },
      },
    },
  },
  {
    name: "create_spec",
    description:
      "Create a behavioral spec. The first run after creation sets the " +
      "baseline output; subsequent scheduled runs are scored against that " +
      "baseline across 5 axes (semantic, format, refusal, length, contains). " +
      "An alert is sent when the drift score crosses the threshold.",
    inputSchema: {
      type: "object",
      properties: {
        endpoint_id: { type: "string", description: "Endpoint to monitor (from list_endpoints)." },
        name: { type: "string", description: "Spec label, e.g. 'Refusal canary' or 'JSON schema check'." },
        prompt: { type: "string", description: "The exact prompt to send to the model." },
        frequency: {
          type: "string",
          enum: ["hourly", "daily", "weekly"],
          description: "How often to run the spec.",
          default: "daily",
        },
        threshold: {
          type: "string",
          enum: ["low", "medium", "high", "critical"],
          description:
            "Severity at which to fire an alert. Buckets: low ≥0.05, medium ≥0.15, high ≥0.35, critical ≥0.6.",
          default: "medium",
        },
      },
      required: ["endpoint_id", "name", "prompt"],
    },
  },
  {
    name: "run_spec",
    description:
      "Run a spec on demand and return the drift score immediately. Use this " +
      "to (1) set the baseline manually right after create_spec, or (2) sanity-" +
      "check a spec without waiting for the next scheduled run. Returns the " +
      "drift score, severity bucket, per-axis scores, and the drift_event_id " +
      "if one was created.",
    inputSchema: {
      type: "object",
      properties: {
        spec_id: { type: "string", description: "From list_specs." },
      },
      required: ["spec_id"],
    },
  },
  {
    name: "reset_baseline",
    description:
      "Clear a spec's baseline. The next run will record a new baseline " +
      "instead of being diffed against the old one. Use this after you've " +
      "intentionally changed your prompt template, model version, or the " +
      "behavior you expect — otherwise every future run will look like drift.",
    inputSchema: {
      type: "object",
      properties: { spec_id: { type: "string" } },
      required: ["spec_id"],
    },
  },
  {
    name: "get_drift_events",
    description:
      "Fetch recent drift events across the workspace, newest first. Each " +
      "event has spec_id, spec_name, severity, drift_score, axes breakdown, " +
      "baseline_output, current_output, and detected_at. Use this for " +
      "weekly review or to drive an automation.",
    inputSchema: {
      type: "object",
      properties: {
        limit: { type: "integer", description: "Max events to return (default 20, max 100).", default: 20 },
        spec_id: { type: "string", description: "Optional. Filter to one spec." },
      },
    },
  },
  {
    name: "get_spec_history",
    description:
      "Get the run history for a single spec. Returns each run's drift score, " +
      "severity, axes breakdown, and timestamp — useful for trending charts " +
      "and reasoning about when behavior shifted.",
    inputSchema: {
      type: "object",
      properties: {
        spec_id: { type: "string" },
        limit: { type: "integer", default: 50 },
      },
      required: ["spec_id"],
    },
  },
  {
    name: "get_health",
    description:
      "Workspace KPIs: plan, spec count, runs this month, plan limits, " +
      "active drift events. Useful as a daily status check.",
    inputSchema: { type: "object", properties: {} },
  },
];

// ---------------------------------------------------------------------------
// HTTP helpers
// ---------------------------------------------------------------------------
async function callApi(
  method: string,
  path: string,
  body?: unknown,
): Promise<any> {
  if (!API_KEY) {
    throw new McpError(
      ErrorCode.InvalidRequest,
      "MODELWATCH_API_KEY not set. Get a free key: https://modelwatch.app/#signup",
    );
  }
  const headers: Record<string, string> = {
    "content-type": "application/json",
    "user-agent": `modelwatch-mcp/${PKG_VERSION}`,
    authorization: "Bearer " + API_KEY,
  };
  const r = await fetch(API_BASE + path, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  let json: any = null;
  const ct = r.headers.get("content-type") || "";
  if (ct.includes("application/json")) {
    try { json = await r.json(); } catch { /* fallthrough */ }
  } else {
    try { json = await r.text(); } catch { /* fallthrough */ }
  }
  if (!r.ok) {
    const detail = (json && (json.detail || json.message)) || `HTTP ${r.status}`;
    if (r.status === 401 || r.status === 403) {
      throw new McpError(
        ErrorCode.InvalidRequest,
        `Auth error (${r.status}): ${detail}. Check MODELWATCH_API_KEY.`,
      );
    }
    if (r.status === 402 || r.status === 429) {
      throw new McpError(
        ErrorCode.InvalidRequest,
        `Plan limit hit (${r.status}): ${detail}. Upgrade at https://modelwatch.app/#pricing`,
      );
    }
    throw new McpError(ErrorCode.InternalError, `${r.status}: ${detail}`);
  }
  return json;
}

function unwrap<T = string>(v: unknown, label: string): T {
  if (v === undefined || v === null || v === "") {
    throw new McpError(ErrorCode.InvalidParams, `${label} is required.`);
  }
  return v as T;
}

// ---------------------------------------------------------------------------
// Tool handlers
// ---------------------------------------------------------------------------
async function handleListEndpoints() {
  return callApi("GET", "/api/endpoints");
}

async function handleCreateEndpoint(args: any) {
  const body: any = {
    name: unwrap<string>(args.name, "name"),
    provider: unwrap<Provider>(args.provider, "provider"),
    model: unwrap<string>(args.model, "model"),
  };
  if (args.base_url) body.base_url = args.base_url;
  return callApi("POST", "/api/endpoints", body);
}

async function handleListSpecs(args: any) {
  const qs = args.endpoint_id ? `?endpoint_id=${encodeURIComponent(args.endpoint_id)}` : "";
  return callApi("GET", "/api/specs" + qs);
}

async function handleCreateSpec(args: any) {
  return callApi("POST", "/api/specs", {
    endpoint_id: unwrap<string>(args.endpoint_id, "endpoint_id"),
    name: unwrap<string>(args.name, "name"),
    prompt: unwrap<string>(args.prompt, "prompt"),
    frequency: (args.frequency as Frequency) || "daily",
    threshold: (args.threshold as Threshold) || "medium",
  });
}

async function handleRunSpec(args: any) {
  const id = unwrap<string>(args.spec_id, "spec_id");
  return callApi("POST", `/api/specs/${encodeURIComponent(id)}/run`);
}

async function handleResetBaseline(args: any) {
  const id = unwrap<string>(args.spec_id, "spec_id");
  return callApi("POST", `/api/specs/${encodeURIComponent(id)}/reset-baseline`);
}

async function handleDriftEvents(args: any) {
  const limit = Math.min(Math.max(args.limit || 20, 1), 100);
  if (args.spec_id) {
    return callApi("GET", `/api/specs/${encodeURIComponent(args.spec_id)}/drift-events?limit=${limit}`);
  }
  return callApi("GET", `/api/dashboard/recent-events?limit=${limit}`);
}

async function handleSpecHistory(args: any) {
  const id = unwrap<string>(args.spec_id, "spec_id");
  const limit = Math.min(Math.max(args.limit || 50, 1), 200);
  return callApi("GET", `/api/specs/${encodeURIComponent(id)}/runs?limit=${limit}`);
}

async function handleHealth() {
  return callApi("GET", "/api/dashboard/health");
}

// ---------------------------------------------------------------------------
// Server boot
// ---------------------------------------------------------------------------
const server = new Server(
  { name: "modelwatch-mcp", version: PKG_VERSION },
  { capabilities: { tools: {} } },
);

server.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: TOOL_DEFINITIONS,
}));

server.setRequestHandler(CallToolRequestSchema, async (req) => {
  const name = req.params.name;
  const args = (req.params.arguments ?? {}) as any;
  let result: unknown;
  try {
    switch (name) {
      case "list_endpoints":   result = await handleListEndpoints(); break;
      case "create_endpoint":  result = await handleCreateEndpoint(args); break;
      case "list_specs":       result = await handleListSpecs(args); break;
      case "create_spec":      result = await handleCreateSpec(args); break;
      case "run_spec":         result = await handleRunSpec(args); break;
      case "reset_baseline":   result = await handleResetBaseline(args); break;
      case "get_drift_events": result = await handleDriftEvents(args); break;
      case "get_spec_history": result = await handleSpecHistory(args); break;
      case "get_health":       result = await handleHealth(); break;
      default:
        throw new McpError(ErrorCode.MethodNotFound, `Unknown tool: ${name}`);
    }
  } catch (e: any) {
    if (e instanceof McpError) throw e;
    throw new McpError(
      ErrorCode.InternalError,
      `${name} failed: ${e?.message ?? String(e)}`,
    );
  }
  return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] };
});

async function main() {
  if (!API_KEY) {
    process.stderr.write(
      "[modelwatch-mcp] MODELWATCH_API_KEY not set — every tool will return an auth error. " +
      "Get a free key: https://modelwatch.app/#signup\n",
    );
  }
  const transport = new StdioServerTransport();
  await server.connect(transport);
}

main().catch((e) => {
  process.stderr.write(`[modelwatch-mcp] fatal: ${e?.stack || e}\n`);
  process.exit(1);
});
