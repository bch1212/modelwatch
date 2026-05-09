"""Endpoint CRUD — LLM endpoints to monitor."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.middleware.auth import get_current_workspace
from app.models.database import get_db
from app.models.schemas import Workspace, Endpoint
from app.models.api_models import EndpointCreate, EndpointUpdate, EndpointOut
from app.services.billing import get_limits, check_limit

router = APIRouter(prefix="/api/endpoints", tags=["endpoints"])


@router.post("", response_model=EndpointOut, status_code=201)
async def create_endpoint(
    body: EndpointCreate,
    workspace: Workspace = Depends(get_current_workspace),
    db: AsyncSession = Depends(get_db),
):
    # Check endpoint limit
    count = await db.scalar(
        select(func.count(Endpoint.id)).where(
            Endpoint.workspace_id == workspace.id
        )
    )
    limits = get_limits(workspace.plan)
    try:
        check_limit(count, limits["endpoints"], "Endpoints")
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    endpoint = Endpoint(
        workspace_id=workspace.id,
        name=body.name,
        provider=body.provider,
        model=body.model,
        system_prompt=body.system_prompt,
        temperature=body.temperature,
        max_tokens=body.max_tokens,
        extra_params=body.extra_params,
    )
    db.add(endpoint)
    await db.flush()
    return endpoint


@router.get("", response_model=list[EndpointOut])
async def list_endpoints(
    workspace: Workspace = Depends(get_current_workspace),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Endpoint).where(Endpoint.workspace_id == workspace.id)
        .order_by(Endpoint.created_at.desc())
    )
    return result.scalars().all()


@router.get("/{endpoint_id}", response_model=EndpointOut)
async def get_endpoint(
    endpoint_id: UUID,
    workspace: Workspace = Depends(get_current_workspace),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Endpoint).where(
            Endpoint.id == endpoint_id,
            Endpoint.workspace_id == workspace.id,
        )
    )
    ep = result.scalar_one_or_none()
    if not ep:
        raise HTTPException(status_code=404, detail="Endpoint not found")
    return ep


@router.patch("/{endpoint_id}", response_model=EndpointOut)
async def update_endpoint(
    endpoint_id: UUID,
    body: EndpointUpdate,
    workspace: Workspace = Depends(get_current_workspace),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Endpoint).where(
            Endpoint.id == endpoint_id,
            Endpoint.workspace_id == workspace.id,
        )
    )
    ep = result.scalar_one_or_none()
    if not ep:
        raise HTTPException(status_code=404, detail="Endpoint not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(ep, field, value)
    return ep


@router.delete("/{endpoint_id}", status_code=204)
async def delete_endpoint(
    endpoint_id: UUID,
    workspace: Workspace = Depends(get_current_workspace),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Endpoint).where(
            Endpoint.id == endpoint_id,
            Endpoint.workspace_id == workspace.id,
        )
    )
    ep = result.scalar_one_or_none()
    if not ep:
        raise HTTPException(status_code=404, detail="Endpoint not found")
    await db.delete(ep)
