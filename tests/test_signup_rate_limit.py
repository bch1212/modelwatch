"""Signup creation is capped durably rather than trusting spoofable proxy headers."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.models.schemas import Workspace


@pytest.mark.asyncio
async def test_signup_global_hourly_cap_returns_429_without_creating_or_emailing(
    client, db_session, monkeypatch,
):
    monkeypatch.setattr(get_settings(), "signup_hourly_limit", 2)
    from app.routers import auth
    sends = []
    monkeypatch.setattr(auth, "_send_api_key_email", lambda *args: sends.append(args) or False)

    for index in range(2):
        response = await client.post("/api/auth/signup", json={"email": f"new{index}@example.com"})
        assert response.status_code == 201
    blocked = await client.post("/api/auth/signup", json={"email": "blocked@example.com"},
                                headers={"x-forwarded-for": "198.51.100.7"})
    assert blocked.status_code == 429
    assert blocked.headers["retry-after"] == "3600"
    assert len(sends) == 2
    rows = (await db_session.execute(select(Workspace))).scalars().all()
    assert {row.email for row in rows} == {"new0@example.com", "new1@example.com"}


@pytest.mark.asyncio
async def test_signup_old_workspaces_do_not_exhaust_rolling_window(client, db_session, monkeypatch):
    monkeypatch.setattr(get_settings(), "signup_hourly_limit", 1)
    old = Workspace(email="old@example.com", api_key="old-test-key", name="Old",
                    created_at=datetime.now(timezone.utc) - timedelta(hours=2))
    db_session.add(old)
    await db_session.commit()
    response = await client.post("/api/auth/signup", json={"email": "fresh@example.com"})
    assert response.status_code == 201


@pytest.mark.asyncio
async def test_duplicate_email_still_returns_409_below_limit(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "signup_hourly_limit", 2)
    first = await client.post("/api/auth/signup", json={"email": "duplicate@example.com"})
    assert first.status_code == 201
    duplicate = await client.post("/api/auth/signup", json={"email": "duplicate@example.com"})
    assert duplicate.status_code == 409
