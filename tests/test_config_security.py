"""Production key and browser-origin boundaries."""

import pytest
from cryptography.fernet import Fernet

from app.config import Settings, validate_runtime_security


def test_rejects_placeholder_encryption_key_at_startup():
    settings = Settings(_env_file=None, encryption_key="CHANGE_ME_GENERATE_WITH_Fernet.generate_key")
    with pytest.raises(ValueError, match="ENCRYPTION_KEY must be a valid Fernet key"):
        validate_runtime_security(settings)


def test_accepts_valid_fernet_key():
    settings = Settings(_env_file=None, encryption_key=Fernet.generate_key().decode())
    validate_runtime_security(settings)


@pytest.mark.asyncio
async def test_cors_allows_product_origins_but_not_arbitrary_sites(client):
    for origin in ("https://modelwatch.app", "https://abc123.modelwatch-web.pages.dev"):
        response = await client.options("/api/auth/signup", headers={
            "Origin": origin, "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        })
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == origin
    response = await client.options("/api/auth/signup", headers={
        "Origin": "https://untrusted.example", "Access-Control-Request-Method": "POST",
    })
    assert response.status_code == 400
    assert response.headers.get("access-control-allow-origin") is None
