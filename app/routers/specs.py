"""Spec CRUD + manual run trigger."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.middleware.auth import get_current_workspace
from app.models.database import get_db
from app.models.schemas import Workspace, Endpoint, Spec, Run, DriftEvent
from app.models.api_models import (
    SpecCreate, SpecUpdate, SpecOut, RunOut, RunTrigger, DriftEventOut,
)
from app.services.billing import get_limits, check_limit
from app.services.drift_detector import run_spec
from app.services import alerts

router = APIRouter(prefix="/api/specs", tags=["specs"])


async def _get_endpoint_for_workspace(
    endpoint_id: UUID, workspace: Workspace, db: AsyncSession
) -> Endpoint:
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


@router.post("", response_model=SpecOut, status_code=201)
async def create_spec(
    body: SpecCreate,
    endpoint_id: UUID,
    workspace: Workspace = Depends(get_current_workspace),
    db: AsyncSession = Depends(get_db),
):
    await _get_endpoint_for_workspace(endpoint_id, workspace, db)

    # Check spec limit across all endpoints
    spec_count = await db.scalar(
        select(func.count(Spec.id))
        .join(Endpoint)
        .where(Endpoint.workspace_id == workspace.id)
    )
    limits = get_limits(workspace.plan)
    try:
        check_limit(spec_count, limits["specs"], "Specs")
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    spec = Spec(
        endpoint_id=endpoint_id,
        name=body.name,
        description=body.description,
        input_text=body.input_text,
        expected_format=body.expected_format,
        expected_json_schema=body.expected_json_schema,
        expected_contains=body.expected_contains,
        expected_not_contains=body.expected_not_contains,
        min_length=body.min_length,
        max_length=body.max_length,
        semantic_threshold=body.semantic_threshold,
        schedule=body.schedule,
    )
    db.add(spec)
    await db.flush()
    return SpecOut.from_orm_with_baseline(spec)


@router.get("", response_model=list[SpecOut])
async def list_specs(
    endpoint_id: UUID | None = None,
    workspace: Workspace = Depends(get_current_workspace),
    db: AsyncSession = Depends(get_db),
):
    query = select(Spec).join(Endpoint).where(Endpoint.workspace_id == workspace.id)
    if endpoint_id:
        query = query.where(Spec.endpoint_id == endpoint_id)
    result = await db.execute(query.order_by(Spec.created_at.desc()))
    return [SpecOut.from_orm_with_baseline(s) for s in result.scalars().all()]


@router.get("/{spec_id}", response_model=SpecOut)
async def get_spec(
    spec_id: UUID,
    workspace: Workspace = Depends(get_current_workspace),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Spec).join(Endpoint)
        .where(Spec.id == spec_id, Endpoint.workspace_id == workspace.id)
    )
    spec = result.scalar_one_or_none()
    if not spec:
        raise HTTPException(status_code=404, detail="Spec not found")
    return SpecOut.from_orm_with_baseline(spec)


@router.patch("/{spec_id}", response_model=SpecOut)
async def update_spec(
    spec_id: UUID,
    body: SpecUpdate,
    workspace: Workspace = Depends(get_current_workspace),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Spec).join(Endpoint)
        .where(Spec.id == spec_id, Endpoint.workspace_id == workspace.id)
    )
    spec = result.scalar_one_or_none()
    if not spec:
        raise HTTPException(status_code=404, detail="Spec not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(spec, field, value)
    return SpecOut.from_orm_with_baseline(spec)


@router.delete("/{spec_id}", status_code=204)
async def delete_spec(
    spec_id: UUID,
    workspace: Workspace = Depends(get_current_workspace),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Spec).join(Endpoint)
        .where(Spec.id == spec_id, Endpoint.workspace_id == workspace.id)
    )
    spec = result.scalar_one_or_none()
    if not spec:
        raise HTTPException(status_code=404, detail="Spec not found")
    await db.delete(spec)


# ── Manual Run ───────────────────────────────────────────────────────

@router.post("/{spec_id}/run", response_model=RunOut, status_code=201)
async def trigger_run(
    spec_id: UUID,
    workspace: Workspace = Depends(get_current_workspace),
    db: AsyncSession = Depends(get_db),
):
    """Manually trigger a run for a spec."""
    # Check run limit
    limits = get_limits(workspace.plan)
    try:
        check_limit(workspace.runs_this_month, limits["runs_per_month"], "Monthly runs")
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    result = await db.execute(
        select(Spec).join(Endpoint)
        .where(Spec.id == spec_id, Endpoint.workspace_id == workspace.id)
    )
    spec = result.scalar_one_or_none()
    if not spec:
        raise HTTPException(status_code=404, detail="Spec not found")

    run = await run_spec(db, spec)
    workspace.runs_this_month += 1

    # If drift event was created, send alerts
    if run.drift_score and run.drift_score > 0.05:
        drift_result = await db.execute(
            select(DriftEvent).where(DriftEvent.run_id == run.id)
        )
        event = drift_result.scalar_one_or_none()
        if event:
            endpoint = await db.get(Endpoint, spec.endpoint_id)
            alert_result = await alerts.dispatch_alerts(
                alert_email=workspace.alert_email,
                slack_webhook_url=workspace.slack_webhook_url,
                spec_name=spec.name,
                endpoint_name=endpoint.name if endpoint else "unknown",
                severity=event.severity.value,
                drift_score=event.drift_score,
                summary=event.summary or "",
            )
            event.email_sent = alert_result["email_sent"]
            event.slack_sent = alert_result["slack_sent"]

    return run


# ── Run history ──────────────────────────────────────────────────────

@router.get("/{spec_id}/runs", response_model=list[RunOut])
async def list_runs(
    spec_id: UUID,
    limit: int = 50,
    workspace: Workspace = Depends(get_current_workspace),
    db: AsyncSession = Depends(get_db),
):
    # Verify ownership
    result = await db.execute(
        select(Spec).join(Endpoint)
        .where(Spec.id == spec_id, Endpoint.workspace_id == workspace.id)
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Spec not found")

    result = await db.execute(
        select(Run).where(Run.spec_id == spec_id)
        .order_by(Run.created_at.desc()).limit(limit)
    )
    return result.scalars().all()


# ── Drift events ─────────────────────────────────────────────────────

@router.get("/{spec_id}/drift-events", response_model=list[DriftEventOut])
async def list_drift_events(
    spec_id: UUID,
    limit: int = 50,
    workspace: Workspace = Depends(get_current_workspace),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Spec).join(Endpoint)
        .where(Spec.id == spec_id, Endpoint.workspace_id == workspace.id)
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Spec not found")

    result = await db.execute(
        select(DriftEvent).where(DriftEvent.spec_id == spec_id)
        .order_by(DriftEvent.created_at.desc()).limit(limit)
    )
    return result.scalars().all()


# ── Reset baseline ──────────────────────────────────────────────────

@router.post("/{spec_id}/reset-baseline", response_model=SpecOut)
async def reset_baseline(
    spec_id: UUID,
    workspace: Workspace = Depends(get_current_workspace),
    db: AsyncSession = Depends(get_db),
):
    """Clear baseline so the next run becomes the new baseline."""
    result = await db.execute(
        select(Spec).join(Endpoint)
        .where(Spec.id == spec_id, Endpoint.workspace_id == workspace.id)
    )
    spec = result.scalar_one_or_none()
    if not spec:
        raise HTTPException(status_code=404, detail="Spec not found")

    spec.baseline_output = None
    spec.baseline_embedding = None
    spec.baseline_run_id = None
    spec.baseline_set_at = None
    return SpecOut.from_orm_with_baseline(spec)
