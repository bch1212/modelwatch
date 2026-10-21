"""Schema drift detection for monitored tool calls.

Compare a successful call's shape fingerprint to the dominant shape of that
tool and agent over the last 24 hours. Alert when the new shape is not that
dominant shape and the dominant shape has at least five successful calls.
The same new fingerprint is reported at most once per hour.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.schemas import ToolCall, ToolSchemaDrift, Workspace
from app.services.alerts import dispatch_tool_schema_alert
from app.services.tool_schema import fingerprint_of_shape, sanitize_shape_tree, sanitize_tags, shape_diff

logger = logging.getLogger(__name__)

BASELINE_WINDOW = timedelta(hours=24)
DEDUP_WINDOW = timedelta(hours=1)
MIN_BASELINE_COUNT = 5
_SHAPE_RE = re.compile(r"^[0-9a-f]{16}$")


def normalize_shape(client_shape: Optional[str], shape_tree: Any) -> tuple[Optional[str], Any]:
    """Prefer a hash of a valid shape tree. Otherwise keep a 16-hex fingerprint."""
    tree = sanitize_shape_tree(shape_tree)
    if tree is not None:
        return fingerprint_of_shape(tree), tree
    if client_shape and _SHAPE_RE.fullmatch(client_shape):
        return client_shape, None
    return None, None


def _summarize(diff: Optional[dict]) -> str:
    if not diff:
        return "Response shape changed from the 24-hour baseline."
    parts: list[str] = []
    for key in ("added", "removed", "changed"):
        items = diff.get(key) or []
        if items:
            parts.append(f"{key}: {', '.join(str(item) for item in items[:5])}")
    text = "; ".join(parts) if parts else "Response shape changed from the 24-hour baseline."
    return text[:1000]


async def calls_this_month(db: AsyncSession, workspace_id) -> int:
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    count = await db.scalar(
        select(func.count())
        .select_from(ToolCall)
        .where(ToolCall.workspace_id == workspace_id, ToolCall.called_at >= month_start)
    )
    return int(count or 0)


async def check_for_drift(
    *,
    workspace: Workspace,
    tool_name: str,
    agent_id: str,
    new_shape: str,
    new_shape_tree: Optional[Any],
    db: AsyncSession,
) -> Optional[ToolSchemaDrift]:
    """Return a new ToolSchemaDrift when this shape is not the 24h mode."""
    since = datetime.now(timezone.utc) - BASELINE_WINDOW
    rows = (
        await db.execute(
            select(ToolCall.output_shape, func.count())
            .where(
                ToolCall.workspace_id == workspace.id,
                ToolCall.tool_name == tool_name,
                ToolCall.agent_id == agent_id,
                ToolCall.success.is_(True),
                ToolCall.output_shape.is_not(None),
                ToolCall.called_at >= since,
            )
            .group_by(ToolCall.output_shape)
        )
    ).all()
    if not rows:
        return None

    top_count = max(int(count) for _, count in rows)
    if top_count < MIN_BASELINE_COUNT:
        return None
    leaders = sorted(shape for shape, count in rows if int(count) == top_count and shape)
    if not leaders or new_shape in leaders:
        return None
    baseline_shape = leaders[0]

    dedup_since = datetime.now(timezone.utc) - DEDUP_WINDOW
    already = await db.scalar(
        select(func.count())
        .select_from(ToolSchemaDrift)
        .where(
            ToolSchemaDrift.workspace_id == workspace.id,
            ToolSchemaDrift.tool_name == tool_name,
            ToolSchemaDrift.agent_id == agent_id,
            ToolSchemaDrift.new_shape == new_shape,
            ToolSchemaDrift.detected_at >= dedup_since,
        )
    )
    if already:
        return None

    baseline_tree = await db.scalar(
        select(ToolCall.output_shape_tree)
        .where(
            ToolCall.workspace_id == workspace.id,
            ToolCall.tool_name == tool_name,
            ToolCall.agent_id == agent_id,
            ToolCall.output_shape == baseline_shape,
            ToolCall.output_shape_tree.is_not(None),
        )
        .order_by(ToolCall.called_at.desc())
        .limit(1)
    )
    diff_obj = None
    if baseline_tree is not None and new_shape_tree is not None:
        try:
            diff_obj = shape_diff(baseline_tree, new_shape_tree)
        except Exception:
            logger.debug("tool shape diff failed", exc_info=True)

    summary = _summarize(diff_obj)
    event = ToolSchemaDrift(
        workspace_id=workspace.id,
        tool_name=tool_name,
        agent_id=agent_id,
        baseline_shape=baseline_shape,
        new_shape=new_shape,
        diff=diff_obj,
        summary=summary,
        detected_at=datetime.now(timezone.utc),
    )
    db.add(event)
    await db.flush()

    try:
        alert_result = await dispatch_tool_schema_alert(
            alert_email=workspace.alert_email or workspace.email,
            slack_webhook_url=workspace.slack_webhook_url,
            tool_name=tool_name,
            agent_id=agent_id,
            summary=summary,
        )
        event.email_sent = alert_result["email_sent"]
        event.slack_sent = alert_result["slack_sent"]
    except Exception:
        logger.warning("tool schema drift alert failed", exc_info=True)
    return event


def prepare_tags(tags: Any) -> dict:
    return sanitize_tags(tags)
