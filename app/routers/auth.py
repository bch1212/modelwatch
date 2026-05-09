"""Self-serve signup — creates workspace, generates API key, emails it."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.middleware.auth import generate_api_key
from app.models.database import get_db
from app.models.schemas import Workspace

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])


class SignupRequest(BaseModel):
    email: EmailStr
    workspace_name: str = "My Workspace"


class SignupResponse(BaseModel):
    workspace_id: str
    email: str
    message: str
    api_key: str | None = None  # only returned in dev/no-sendgrid mode


def _send_api_key_email(to_email: str, api_key: str, workspace_name: str) -> bool:
    """Email the API key to the new user via SendGrid."""
    settings = get_settings()
    if not settings.sendgrid_api_key:
        logger.warning("SendGrid not configured — returning key in response (dev mode)")
        return False

    html = f"""
    <div style="font-family: -apple-system, sans-serif; max-width: 600px;">
      <h2>Welcome to ModelWatch</h2>
      <p>Your workspace <strong>{workspace_name}</strong> is ready.</p>
      <p>Your API key (keep this secret):</p>
      <pre style="background:#f4f4f5; padding:12px; border-radius:6px; word-break:break-all;">{api_key}</pre>
      <p>Use it in the Authorization header:</p>
      <pre style="background:#f4f4f5; padding:12px; border-radius:6px;">Authorization: Bearer {api_key}</pre>
      <p>Next steps:</p>
      <ol>
        <li>Add an LLM provider API key: POST /api/workspaces/me/api-keys</li>
        <li>Create an endpoint to monitor: POST /api/endpoints</li>
        <li>Define a behavioral spec: POST /api/specs?endpoint_id=...</li>
      </ol>
      <p>Docs: <a href="https://modelwatch.app/docs">modelwatch.app/docs</a></p>
    </div>
    """
    msg = Mail(
        from_email=settings.sendgrid_from_email,
        to_emails=to_email,
        subject="[ModelWatch] Your API key",
        html_content=html,
    )
    try:
        SendGridAPIClient(settings.sendgrid_api_key).send(msg)
        return True
    except Exception as exc:
        logger.error(f"SendGrid error: {exc}")
        return False


@router.post("/signup", response_model=SignupResponse, status_code=201)
async def signup(body: SignupRequest, db: AsyncSession = Depends(get_db)):
    """Self-serve signup. Creates workspace + API key, emails the key."""
    # Idempotent — return existing workspace if email exists
    result = await db.execute(
        select(Workspace).where(Workspace.email == body.email)
    )
    existing = result.scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=409,
            detail="An account with that email already exists. Check your inbox.",
        )

    api_key = generate_api_key()
    workspace = Workspace(
        email=body.email,
        api_key=api_key,
        name=body.workspace_name,
        alert_email=body.email,
    )
    db.add(workspace)
    await db.flush()

    sent = _send_api_key_email(body.email, api_key, body.workspace_name)

    return SignupResponse(
        workspace_id=str(workspace.id),
        email=body.email,
        message=(
            "Workspace created. Check your email for your API key."
            if sent
            else "Workspace created. SendGrid not configured — key returned in this response."
        ),
        api_key=None if sent else api_key,
    )


@router.post("/rotate-key", response_model=SignupResponse)
async def rotate_api_key(
    workspace=Depends(__import__("app.middleware.auth", fromlist=["get_current_workspace"]).get_current_workspace),
    db: AsyncSession = Depends(get_db),
):
    """Rotate the workspace API key. Old key is invalidated immediately."""
    new_key = generate_api_key()
    workspace.api_key = new_key
    sent = _send_api_key_email(workspace.email, new_key, workspace.name)
    return SignupResponse(
        workspace_id=str(workspace.id),
        email=workspace.email,
        message="API key rotated. New key emailed." if sent else "API key rotated.",
        api_key=None if sent else new_key,
    )
