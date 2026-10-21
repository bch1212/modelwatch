# ModelWatch

Continuous behavioral drift monitoring for LLM-powered applications. Define a spec (input and expected behavior), ModelWatch replays it on a schedule against your model endpoint, and alerts when the output drifts from the baseline.

Customers bring their own OpenAI or Anthropic key. Auth is a workspace key minted at `POST /api/auth/signup` (`Authorization: Bearer mw_...`).

- API: `https://api.modelwatch.app`
- App: `https://modelwatch.app`
- HTTP reference: [`frontend/docs.html`](frontend/docs.html)

```bash
pip install -r requirements.txt
pytest tests/ -q
```

## Tool monitoring

ModelWatch also watches the tools an agent calls. A decorator in your process records latency, success, and a fingerprint of the response shape. The API compares that fingerprint with the dominant shape from the last 24 hours and alerts on schema drift. Response values are not uploaded.

```bash
pip install -e sdk
export MODELWATCH_API_KEY=mw_...
```

```python
from modelwatch import monitor

@monitor(tool_name="search_web", agent_id="researcher")
async def search_web(query: str) -> dict:
    ...
```

Calls go to `https://api.modelwatch.app/api/tools/ingest`. Read recent calls at `GET /api/tools` and schema changes at `GET /api/tools/schema-drift`. The same `mw_` key and the same email/Slack settings are used. Full SDK notes, including the TypeScript wrapper, are in [`sdk/README.md`](sdk/README.md).

Quotas for tool calls are separate from spec runs: Free 5,000/month, Pro 100,000, Team 1,000,000, Enterprise unlimited.

This feature replaces a standalone ToolPulse deployment for the same buyer. Point clients at ModelWatch (`MODELWATCH_API_KEY`, `https://api.modelwatch.app`). The decorator does not call `api.toolpulse.io`.
