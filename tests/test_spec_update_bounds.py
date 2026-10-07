"""PATCH must validate effective bounds against the existing spec."""

import pytest


@pytest.mark.asyncio
async def test_patch_rejects_bounds_that_conflict_with_stored_value(auth_client):
    endpoint = await auth_client.post("/api/endpoints", json={
        "name": "Test model", "provider": "openai", "model": "test-model",
    })
    assert endpoint.status_code == 201, endpoint.text
    endpoint_id = endpoint.json()["id"]
    created = await auth_client.post(f"/api/specs?endpoint_id={endpoint_id}", json={
        "name": "Length canary", "input_text": "test input", "min_length": 100,
    })
    assert created.status_code == 201, created.text
    spec_id = created.json()["id"]

    rejected = await auth_client.patch(f"/api/specs/{spec_id}", json={"max_length": 10})
    assert rejected.status_code == 422, rejected.text
    current = await auth_client.get(f"/api/specs/{spec_id}")
    assert current.status_code == 200
    assert current.json()["min_length"] == 100
    assert current.json()["max_length"] is None
