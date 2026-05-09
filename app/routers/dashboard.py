"""Dashboard aggregation endpoints — health overview + drift trends."""

from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.middleware.auth import get_current_workspace
from app.models.database import get_db
from app.models.schemas import Workspace, Endpoint, Spec, Run, DriftEvent
from app.models.api_models import SpecHealth, DriftTrend, DriftEventOut

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/health", response_model=list[SpecHealth])
async def spec_health_overview(
    workspace: Workspace = Depends(get_current_workspace),
    db: AsyncSession = Depends(get_db),
):
    """Get health status for every spec in the workspace."""
    result = await db.execute(
        select(Spec, Endpoint)
        .join(Endpoint)
        .where(Endpoint.workspace_id == workspace.id, Spec.is_active.is_(True))
        .order_by(Spec.name)
    )
    rows = result.all()

    health_list = []
    for spec, endpoint in rows:
        # Get most recent run
        last_run_result = await db.execute(
            select(Run)
            .where(Run.spec_id == spec.id)
            .order_by(Run.created_at.desc())
            .limit(1)
        )
        last_run = last_run_result.scalar_one_or_none()

        if not last_run or last_run.drift_score is None:
            status = "green"
            score = None
        elif last_run.drift_score < 0.05:
            status = "green"
            score = last_run.drift_score
        elif last_run.drift_score < 0.35:
            status = "yellow"
            score = last_run.drift_score
        else:
            status = "red"
            score = last_run.drift_score

        health_list.append(SpecHealth(
            spec_id=spec.id,
            spec_name=spec.name,
            endpoint_name=endpoint.name,
            status=status,
            last_drift_score=score,
            last_run_at=last_run.created_at if last_run else None,
        ))

    return health_list


@router.get("/drift-trends", response_model=list[DriftTrend])
async def drift_trends(
    days: int = Query(default=7, ge=1, le=90),
    spec_id: UUID | None = None,
    workspace: Workspace = Depends(get_current_workspace),
    db: AsyncSession = Depends(get_db),
):
    """Get drift score over time for all specs (or a specific one)."""
    since = datetime.now(timezone.utc) - timedelta(days=days)

    query = (
        select(Run.created_at, Run.drift_score, Run.spec_id)
        .join(Spec)
        .join(Endpoint)
        .where(
            Endpoint.workspace_id == workspace.id,
            Run.created_at >= since,
            Run.drift_score.isnot(None),
        )
    )
    if spec_id:
        query = query.where(Run.spec_id == spec_id)

    query = query.order_by(Run.created_at)
    result = await db.execute(query)

    return [
        DriftTrend(date=row.created_at, drift_score=row.drift_score, spec_id=row.spec_id)
        for row in result.all()
    ]


@router.get("/recent-events", response_model=list[DriftEventOut])
async def recent_drift_events(
    limit: int = Query(default=20, ge=1, le=100),
    workspace: Workspace = Depends(get_current_workspace),
    db: AsyncSession = Depends(get_db),
):
    """Get the most recent drift events across all specs."""
    result = await db.execute(
        select(DriftEvent)
        .join(Spec)
        .join(Endpoint)
        .where(Endpoint.workspace_id == workspace.id)
        .order_by(DriftEvent.created_at.desc())
        .limit(limit)
    )
    return result.scalars().all()
