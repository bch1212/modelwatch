"""Tool ingest and schema-drift detection on the ModelWatch API."""

import pytest
from sqlalchemy import select

from app.models.schemas import ToolCall
from modelwatch.schema import extract_shape, fingerprint


def _call(obj=None, *, tool="search", agent="agent-1", success=True, **extra):
    body = {
        "tool_name": tool,
        "agent_id": agent,
        "latency_ms": extra.pop("latency_ms", 15),
        "success": success,
        "tags": {"region": "us"},
    }
    if obj is not None:
        body["output_shape"] = fingerprint(obj)
        body["output_shape_tree"] = extract_shape(obj)
    body.update(extra)
    return body


async def _ingest(client, *bodies):
    resp = await client.post("/api/tools/ingest", json=list(bodies))
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_ingest_requires_workspace_key(client):
    resp = await client.post("/api/tools/ingest", json=[_call({"id": 1})])
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_ingest_and_list_without_provider_key(auth_client):
    data = await _ingest(auth_client, _call({"id": 1}, tool="search_web"))
    assert data == {"ingested": 1, "drift_events_created": 0}

    listed = await auth_client.get("/api/tools")
    assert listed.status_code == 200
    rows = listed.json()
    assert rows[0]["tool_name"] == "search_web"
    assert rows[0]["total_calls"] == 1
    assert rows[0]["successful"] == 1

    recent = await auth_client.get("/api/tools/search_web/calls")
    assert recent.status_code == 200
    assert recent.json()[0]["output_shape"] == fingerprint({"id": 1})
    assert "output_shape_tree" not in recent.json()[0]


@pytest.mark.asyncio
async def test_schema_drift_after_five_baseline_calls(auth_client, monkeypatch):
    alerts = []

    async def fake_alert(**kwargs):
        alerts.append(kwargs)
        return {"email_sent": False, "slack_sent": False}

    monkeypatch.setattr("app.services.tool_drift.dispatch_tool_schema_alert", fake_alert)

    baseline = {"id": 1}
    drifted = {"id": 1, "name": "x"}
    for _ in range(5):
        await _ingest(auth_client, _call(baseline))
    first = await _ingest(auth_client, _call(drifted))
    assert first["drift_events_created"] == 1

    again = await _ingest(auth_client, _call(drifted))
    assert again["drift_events_created"] == 0
    assert len(alerts) == 1
    assert alerts[0]["alert_email"] == "test@example.com"
    assert alerts[0]["tool_name"] == "search"
    assert "name" in alerts[0]["summary"]
    assert "hunter2" not in alerts[0]["summary"]

    events = await auth_client.get("/api/tools/schema-drift")
    assert events.status_code == 200
    body = events.json()
    assert len(body) == 1
    assert body[0]["baseline_shape"] == fingerprint(baseline)
    assert body[0]["new_shape"] == fingerprint(drifted)
    assert any("name" in item for item in body[0]["diff"]["added"])


@pytest.mark.asyncio
async def test_no_drift_before_five_observations_or_on_failure(auth_client):
    for _ in range(4):
        await _ingest(auth_client, _call({"id": 1}, tool="young"))
    early = await _ingest(auth_client, _call({"id": "abc"}, tool="young"))
    assert early["drift_events_created"] == 0

    for _ in range(5):
        await _ingest(auth_client, _call({"id": 1}, tool="flaky", success=False))
    failed = await _ingest(auth_client, _call({"id": "abc"}, tool="flaky"))
    assert failed["drift_events_created"] == 0


@pytest.mark.asyncio
async def test_drift_is_scoped_to_agent_and_workspace(client, auth_client):
    for _ in range(5):
        await _ingest(auth_client, _call({"id": 1}, agent="alpha"))
    other_agent = await _ingest(auth_client, _call({"email": "a@b.com"}, agent="beta"))
    assert other_agent["drift_events_created"] == 0

    other = await client.post("/api/auth/signup", json={
        "email": "other@example.com",
        "workspace_name": "Other",
    })
    assert other.status_code == 201
    auth_client.headers["Authorization"] = f"Bearer {other.json()['api_key']}"
    hidden = await auth_client.get("/api/tools/schema-drift")
    assert hidden.status_code == 200
    assert hidden.json() == []
    listed = await auth_client.get("/api/tools")
    assert listed.json() == []


@pytest.mark.asyncio
async def test_dominant_shape_does_not_keep_alerting(auth_client):
    for _ in range(5):
        await _ingest(auth_client, _call({"id": 1}, tool="stable"))
    changed = await _ingest(auth_client, _call({"id": "abc"}, tool="stable"))
    assert changed["drift_events_created"] == 1
    for _ in range(6):
        await _ingest(auth_client, _call({"id": "abc"}, tool="stable"))
    # The new shape is now the mode, so a further call is not drift.
    # Dedup would also suppress the same fingerprint inside an hour; force the
    # mode check by using a third shape that should alert against the new mode.
    third = await _ingest(auth_client, _call({"id": 1, "extra": True}, tool="stable"))
    assert third["drift_events_created"] == 1


@pytest.mark.asyncio
async def test_tool_call_quota(auth_client, monkeypatch):
    from app.routers import tools as tools_router

    real = tools_router.get_limits
    monkeypatch.setattr(
        tools_router,
        "get_limits",
        lambda plan: {**real(plan), "tool_calls_per_month": 2},
    )
    body = {"tool_name": "quota", "latency_ms": 1, "success": False}
    assert (await auth_client.post("/api/tools/ingest", json=[body, body])).status_code == 200
    blocked = await auth_client.post("/api/tools/ingest", json=[body])
    assert blocked.status_code == 403
    assert "Tool calls" in blocked.json()["detail"]


@pytest.mark.asyncio
async def test_oversized_shape_tree_is_dropped(auth_client, db_session):
    resp = await auth_client.post("/api/tools/ingest", json=[{
        "tool_name": "noisy",
        "latency_ms": 3,
        "success": True,
        "output_shape": "abcdefabcdefabcd",
        "output_shape_tree": {"note": "p" * 500},
        "called_at": "2001-01-01T00:00:00Z",
    }])
    assert resp.status_code == 200
    assert resp.json()["drift_events_created"] == 0
    row = (await db_session.execute(select(ToolCall).where(ToolCall.tool_name == "noisy"))).scalar_one()
    assert row.output_shape_tree is None
    assert row.output_shape == "abcdefabcdefabcd"
    assert row.called_at.year != 2001


@pytest.mark.asyncio
async def test_batch_over_1000_is_rejected(auth_client):
    body = {"tool_name": "bulk", "latency_ms": 1, "success": True}
    resp = await auth_client.post("/api/tools/ingest", json=[body] * 1001)
    assert resp.status_code == 413
