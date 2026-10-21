"""Tool monitoring — ingest decorator telemetry and read schema drift."""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.middleware.auth import get_current_workspace
from app.models.api_models import (
    ToolCallIn,
    ToolCallOut,
    ToolIngestResult,
    ToolSchemaDriftOut,
    ToolSummaryOut,
)
from app.models.database import get_db
from app.models.schemas import ToolCall, ToolSchemaDrift, Workspace
from app.services.billing import check_limit, get_limits
from app.services.tool_drift import (
    calls_this_month,
    check_for_drift,
    normalize_shape,
    prepare_tags,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/tools", tags=["tools"])

_MAX_BATCH = 1000


@router.post("/ingest", response_model=ToolIngestResult)
async def ingest_tool_calls(
    payload: list[ToolCallIn],
    workspace: Workspace = Depends(get_current_workspace),
    db: AsyncSession = Depends(get_db),
):
    """Record tool calls from the ModelWatch decorator and detect schema drift.

    The server timestamps each call. Client `called_at` is ignored so a clock
    cannot rewrite the baseline window.
    """
    if len(payload) > _MAX_BATCH:
        raise HTTPException(status_code=413, detail=f"batch too large (max {_MAX_BATCH})")
    if not payload:
        return ToolIngestResult(ingested=0, drift_events_created=0)

    limits = get_limits(workspace.plan)
    used = await calls_this_month(db, workspace.id)
    try:
        check_limit(used + len(payload) - 1, limits["tool_calls_per_month"], "Tool calls")
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    drift_created = 0
    now = datetime.now(timezone.utc)
    for call in payload:
        output_shape, shape_tree = normalize_shape(call.output_shape, call.output_shape_tree)
        record = ToolCall(
            workspace_id=workspace.id,
            tool_name=call.tool_name[:200],
            agent_id=(call.agent_id or "default")[:100],
            latency_ms=call.latency_ms,
            success=call.success,
            error=call.error,
            output_shape=output_shape,
            output_shape_tree=shape_tree,
            tags=prepare_tags(call.tags),
            called_at=now,
        )
        db.add(record)
        await db.flush()

        if call.success and output_shape:
            try:
                event = await check_for_drift(
                    workspace=workspace,
                    tool_name=record.tool_name,
                    agent_id=record.agent_id,
                    new_shape=output_shape,
                    new_shape_tree=shape_tree,
                    db=db,
                )
            except Exception:
                logger.warning("tool schema drift check failed", exc_info=True)
                event = None
            if event is not None:
                drift_created += 1

    return ToolIngestResult(ingested=len(payload), drift_events_created=drift_created)


@router.get("", response_model=list[ToolSummaryOut])
async def list_tools(
    workspace: Workspace = Depends(get_current_workspace),
    db: AsyncSession = Depends(get_db),
):
    """Roll up tool calls from the last 24 hours."""
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    successful = func.sum(case((ToolCall.success.is_(True), 1), else_=0))
    stmt = (
        select(
            ToolCall.tool_name,
            func.count().label("total_calls"),
            successful.label("successful"),
            func.avg(ToolCall.latency_ms).label("avg_latency_ms"),
            func.max(ToolCall.called_at).label("last_seen"),
        )
        .where(ToolCall.workspace_id == workspace.id, ToolCall.called_at >= since)
        .group_by(ToolCall.tool_name)
        .order_by(func.count().desc())
    )
    rows = (await db.execute(stmt)).all()
    return [
        ToolSummaryOut(
            tool_name=row.tool_name,
            total_calls=int(row.total_calls or 0),
            successful=int(row.successful or 0),
            avg_latency_ms=float(row.avg_latency_ms or 0),
            last_seen=row.last_seen,
        )
        for row in rows
    ]


@router.get("/schema-drift", response_model=list[ToolSchemaDriftOut])
async def list_schema_drift(
    limit: int = Query(default=50, ge=1, le=200),
    workspace: Workspace = Depends(get_current_workspace),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ToolSchemaDrift)
        .where(ToolSchemaDrift.workspace_id == workspace.id)
        .order_by(ToolSchemaDrift.detected_at.desc())
        .limit(limit)
    )
    return result.scalars().all()


@router.get("/{tool_name}/calls", response_model=list[ToolCallOut])
async def recent_calls(
    tool_name: str,
    limit: int = Query(default=50, ge=1, le=200),
    workspace: Workspace = Depends(get_current_workspace),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ToolCall)
        .where(ToolCall.workspace_id == workspace.id, ToolCall.tool_name == tool_name)
        .order_by(ToolCall.called_at.desc())
        .limit(limit)
    )
    return result.scalars().all()
