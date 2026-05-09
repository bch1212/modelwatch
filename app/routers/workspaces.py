"""Workspace management + API key (LLM provider keys, not auth keys)."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.middleware.auth import get_current_workspace
from app.models.database import get_db
from app.models.schemas import Workspace, ApiKey
from app.models.api_models import (
    WorkspaceUpdate, WorkspaceOut,
    ApiKeyCreate, ApiKeyOut,
)
from app.services.encryption import encrypt

router = APIRouter(prefix="/api/workspaces", tags=["workspaces"])


@router.get("/me", response_model=WorkspaceOut)
async def get_my_workspace(workspace: Workspace = Depends(get_current_workspace)):
    return workspace


@router.patch("/me", response_model=WorkspaceOut)
async def update_my_workspace(
    body: WorkspaceUpdate,
    workspace: Workspace = Depends(get_current_workspace),
):
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(workspace, field, value)
    return workspace


# ── LLM provider API keys (e.g. OpenAI, Anthropic) ──────────────────

@router.post("/me/api-keys", response_model=ApiKeyOut, status_code=201)
async def add_api_key(
    body: ApiKeyCreate,
    workspace: Workspace = Depends(get_current_workspace),
    db: AsyncSession = Depends(get_db),
):
    key = ApiKey(
        workspace_id=workspace.id,
        provider=body.provider,
        encrypted_key=encrypt(body.api_key),
        label=body.label,
    )
    db.add(key)
    await db.flush()
    return key


@router.get("/me/api-keys", response_model=list[ApiKeyOut])
async def list_api_keys(
    workspace: Workspace = Depends(get_current_workspace),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ApiKey).where(ApiKey.workspace_id == workspace.id)
    )
    return result.scalars().all()


@router.delete("/me/api-keys/{key_id}", status_code=204)
async def delete_api_key(
    key_id: UUID,
    workspace: Workspace = Depends(get_current_workspace),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ApiKey).where(
            ApiKey.id == key_id,
            ApiKey.workspace_id == workspace.id,
        )
    )
    key = result.scalar_one_or_none()
    if not key:
        raise HTTPException(status_code=404, detail="API key not found")
    await db.delete(key)
