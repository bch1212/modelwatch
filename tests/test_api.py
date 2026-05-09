"""Integration tests for API endpoints."""

import pytest


@pytest.mark.asyncio
class TestHealthAndRoot:
    async def test_health(self, client):
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    async def test_root(self, client):
        resp = await client.get("/")
        assert resp.status_code == 200
        assert resp.json()["service"] == "ModelWatch"


@pytest.mark.asyncio
class TestSignup:
    async def test_signup_creates_workspace(self, client):
        resp = await client.post("/api/auth/signup", json={
            "email": "alice@example.com",
            "workspace_name": "Alice's WS",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["email"] == "alice@example.com"
        assert data["api_key"].startswith("mw_")

    async def test_signup_duplicate_email_409(self, client):
        await client.post("/api/auth/signup", json={"email": "dup@x.com"})
        resp = await client.post("/api/auth/signup", json={"email": "dup@x.com"})
        assert resp.status_code == 409

    async def test_invalid_email_422(self, client):
        resp = await client.post("/api/auth/signup", json={"email": "not-an-email"})
        assert resp.status_code == 422


@pytest.mark.asyncio
class TestAuth:
    async def test_no_auth_header_401(self, client):
        resp = await client.get("/api/workspaces/me")
        assert resp.status_code == 401

    async def test_bad_token_format_401(self, client):
        client.headers["Authorization"] = "Bearer not-a-modelwatch-key"
        resp = await client.get("/api/workspaces/me")
        assert resp.status_code == 401

    async def test_valid_key_200(self, auth_client):
        resp = await auth_client.get("/api/workspaces/me")
        assert resp.status_code == 200
        assert resp.json()["email"] == "test@example.com"

    async def test_rotate_key(self, auth_client):
        # Get current key works
        resp = await auth_client.get("/api/workspaces/me")
        assert resp.status_code == 200
        # Rotate
        resp = await auth_client.post("/api/auth/rotate-key")
        assert resp.status_code == 200
        new_key = resp.json()["api_key"]
        assert new_key.startswith("mw_")
        # Old key should fail
        resp = await auth_client.get("/api/workspaces/me")
        assert resp.status_code == 401
        # New key should work
        auth_client.headers["Authorization"] = f"Bearer {new_key}"
        resp = await auth_client.get("/api/workspaces/me")
        assert resp.status_code == 200


@pytest.mark.asyncio
class TestWorkspace:
    async def test_get_my_workspace(self, auth_client):
        resp = await auth_client.get("/api/workspaces/me")
        assert resp.status_code == 200
        assert resp.json()["name"] == "Test WS"
        assert resp.json()["plan"] == "free"

    async def test_update_workspace(self, auth_client):
        resp = await auth_client.patch("/api/workspaces/me", json={
            "name": "Renamed",
            "slack_webhook_url": "https://hooks.slack.com/test",
        })
        assert resp.status_code == 200
        assert resp.json()["name"] == "Renamed"


@pytest.mark.asyncio
class TestApiKeys:
    async def test_add_and_list_provider_keys(self, auth_client):
        resp = await auth_client.post("/api/workspaces/me/api-keys", json={
            "provider": "openai",
            "api_key": "sk-test-12345",
            "label": "primary",
        })
        assert resp.status_code == 201
        # The OpenAI key should NOT appear in plaintext
        assert "sk-test-12345" not in str(resp.json())

        resp = await auth_client.get("/api/workspaces/me/api-keys")
        assert resp.status_code == 200
        assert len(resp.json()) == 1


@pytest.mark.asyncio
class TestEndpoints:
    async def _seed_key(self, c):
        await c.post("/api/workspaces/me/api-keys", json={
            "provider": "openai", "api_key": "sk-test",
        })

    async def test_create_endpoint(self, auth_client):
        await self._seed_key(auth_client)
        resp = await auth_client.post("/api/endpoints", json={
            "name": "GPT-4o Production",
            "provider": "openai",
            "model": "gpt-4o",
            "system_prompt": "You are a helpful assistant.",
            "temperature": 0.0,
        })
        assert resp.status_code == 201
        assert resp.json()["model"] == "gpt-4o"

    async def test_endpoint_limit_enforcement(self, auth_client):
        """Free plan allows 1 endpoint."""
        await self._seed_key(auth_client)
        await auth_client.post("/api/endpoints", json={
            "name": "EP1", "provider": "openai", "model": "gpt-4o",
        })
        resp = await auth_client.post("/api/endpoints", json={
            "name": "EP2", "provider": "openai", "model": "gpt-4o-mini",
        })
        assert resp.status_code == 403
        assert "limit reached" in resp.json()["detail"]


@pytest.mark.asyncio
class TestSpecs:
    async def _seed_endpoint(self, c):
        await c.post("/api/workspaces/me/api-keys", json={
            "provider": "openai", "api_key": "sk-test",
        })
        resp = await c.post("/api/endpoints", json={
            "name": "EP", "provider": "openai", "model": "gpt-4o",
        })
        return resp.json()["id"]

    async def test_create_spec(self, auth_client):
        ep_id = await self._seed_endpoint(auth_client)
        resp = await auth_client.post(f"/api/specs?endpoint_id={ep_id}", json={
            "name": "Capital test",
            "input_text": "What is the capital of France?",
            "expected_contains": ["Paris"],
        })
        assert resp.status_code == 201
        assert resp.json()["has_baseline"] is False

    async def test_reset_baseline(self, auth_client):
        ep_id = await self._seed_endpoint(auth_client)
        resp = await auth_client.post(f"/api/specs?endpoint_id={ep_id}", json={
            "name": "S", "input_text": "T",
        })
        spec_id = resp.json()["id"]
        resp = await auth_client.post(f"/api/specs/{spec_id}/reset-baseline")
        assert resp.status_code == 200


@pytest.mark.asyncio
class TestDashboard:
    async def test_health_overview_empty(self, auth_client):
        resp = await auth_client.get("/api/dashboard/health")
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_drift_trends_empty(self, auth_client):
        resp = await auth_client.get("/api/dashboard/drift-trends")
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_recent_events_empty(self, auth_client):
        resp = await auth_client.get("/api/dashboard/recent-events")
        assert resp.status_code == 200
        assert resp.json() == []
