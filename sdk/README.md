# ModelWatch tool monitoring

Wrap an agent tool with one decorator. ModelWatch records latency, success, and a fingerprint of the response **shape**. Values stay in your process. When the shape stops matching the last 24 hours of calls, ModelWatch sends the same email and Slack alerts as behavioral spec drift.

The API is `https://api.modelwatch.app`. Use the `mw_` workspace key from [modelwatch.app](https://modelwatch.app/#signup).

```bash
pip install -e sdk          # from a checkout
# or, once published: pip install modelwatch-sdk
export MODELWATCH_API_KEY=mw_...
```

```python
from modelwatch import monitor

@monitor(tool_name="search_web", agent_id="researcher")
async def search_web(query: str) -> dict:
    ...
```

`MODELWATCH_API_BASE` overrides the API origin (default `https://api.modelwatch.app`). If `MODELWATCH_API_KEY` is unset, the decorator is a no-op and does not send telemetry.

## What gets sent

Each call posts to `POST /api/tools/ingest` with `Authorization: Bearer mw_...`:

- tool name, agent id, latency, success, a short error string
- a 16-character SHA-256 of the response **structure**
- the structure itself (type names only: `str`, `int`, `bool`, nested keys)

A response `{"secret": "hunter2", "n": 1}` is stored as `{"n": "int", "secret": "str"}`.

## MCP servers

```python
from mcp.server.fastmcp import FastMCP
from modelwatch import wrap_mcp_server

mcp = FastMCP("my-server")

@mcp.tool()
async def search(q: str) -> dict: ...

wrap_mcp_server(mcp, agent_id="my-mcp-server")
```

## Configuration

```python
from modelwatch import configure

configure(
    api_key="mw_...",                         # or MODELWATCH_API_KEY
    endpoint="https://api.modelwatch.app",    # or MODELWATCH_API_BASE
    batch_size=50,
    flush_interval=5.0,
    enabled=True,
)
```

Passing `api_key=None` or an endpoint that is not `http://` or `https://` disables sending.

## TypeScript

The same decorator lives in `sdk/typescript` (`modelwatch-sdk` on npm, when published):

```ts
import { monitor } from "modelwatch-sdk";

const searchWeb = monitor(async function searchWeb(query: string) {
  return { results: [] as string[] };
}, { toolName: "search_web", agentId: "researcher" });
```

Set `MODELWATCH_API_KEY`. The default API base is `https://api.modelwatch.app`.

## Schema drift rule

For each successful call, the API compares its fingerprint with the most common fingerprint for that tool and agent over the last 24 hours.

- The leading shape needs at least 5 successful calls before it counts as a baseline.
- A call that still has that leading shape does not alert.
- The same new fingerprint alerts at most once per hour.
- Alerts use the workspace email (or `alert_email`) and the Slack webhook already stored on the workspace.

Monthly tool-call quotas, separate from spec runs: Free 5,000 · Pro 100,000 · Team 1,000,000 · Enterprise unlimited.

## Attribution

The decorator and shape fingerprint were adapted from ToolPulse (MIT). They now report to ModelWatch. `TOOLPULSE_API_KEY` and `api.toolpulse.io` are not read and are not called.
