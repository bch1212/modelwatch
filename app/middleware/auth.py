"""API-key authentication — autonomous, no third-party dashboard required.

Signup flow:
  POST /api/signup {email} → server creates Workspace + API key, emails it via SendGrid.
  User stores the key (mw_<base64>) and sends as Authorization: Bearer mw_<key>.

This avoids Clerk's manual dashboard setup (JWT templates, allowed origins, etc.)
and keeps the product fully self-serve per the autonomous-only requirement.
"""

import logging
import secrets

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import get_db
from app.models.schemas import Workspace

logger = logging.getLogger(__name__)
security = HTTPBearer(auto_error=False)

API_KEY_PREFIX = "mw_"


def generate_api_key() -> str:
    """Generate a new API key with the mw_ prefix."""
    return f"{API_KEY_PREFIX}{secrets.token_urlsafe(32)}"


async def get_current_workspace(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> Workspace:
    """Resolve the workspace from the Bearer token."""
    if not credentials or not credentials.credentials:
        raise HTTPException(status_code=401, detail="Missing API key")

    token = credentials.credentials
    if not token.startswith(API_KEY_PREFIX):
        raise HTTPException(status_code=401, detail="Invalid API key format")

    result = await db.execute(
        select(Workspace).where(Workspace.api_key == token)
    )
    workspace = result.scalar_one_or_none()
    if not workspace:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return workspace
