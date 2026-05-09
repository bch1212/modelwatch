# @modelwatch/mcp

MCP server for [ModelWatch](https://modelwatch.app) — continuous behavioral
drift monitoring for LLM-powered applications.

Lets Claude Desktop / Claude Code (or any MCP-compatible client) create drift
specs, run them, and surface drift events without leaving the chat.

## Install

```bash
npm install -g @modelwatch/mcp
```

## Configure (Claude Desktop)

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "modelwatch": {
      "command": "npx",
      "args": ["-y", "@modelwatch/mcp"],
      "env": {
        "MODELWATCH_API_KEY": "mw_PASTE_FROM_EMAIL"
      }
    }
  }
}
```

Get a free key at <https://modelwatch.app/#signup> (5 specs, 500 runs/mo, no
card). Then restart Claude Desktop.

## Tools

| Tool | What it does |
|---|---|
| `list_endpoints` | Show monitored LLM endpoints |
| `create_endpoint` | Add an endpoint (provider + model) |
| `list_specs` | Show behavioral specs |
| `create_spec` | Add a spec (prompt + frequency + severity threshold) |
| `run_spec` | Run a spec on demand; baseline if first run |
| `reset_baseline` | Re-baseline after an intentional model swap |
| `get_drift_events` | Recent drift events across the workspace |
| `get_spec_history` | Run history for one spec |
| `get_health` | Workspace KPIs (plan, spec count, runs this month) |

## Example session

> **You:** Create a refusal canary against the OpenAI endpoint, asking about
> chest pain. Run it now and tell me the baseline.

Claude calls `list_endpoints` → `create_spec` → `run_spec` and reports the
score. The same spec then runs daily forever and emails you when drift is
detected.

## Environment variables

| Var | Required | Default |
|---|---|---|
| `MODELWATCH_API_KEY` | Yes | — |
| `MODELWATCH_API_BASE` | No | `https://api.modelwatch.app` |

Self-hosted? Point `MODELWATCH_API_BASE` at your Railway URL.

## Source

<https://github.com/bch1212/modelwatch> — MIT licensed.
