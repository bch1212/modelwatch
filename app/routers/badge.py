"""README badge endpoint — shields.io-compatible SVG badge."""

from uuid import UUID

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Depends

from app.models.database import get_db
from app.models.schemas import Workspace, Endpoint, Spec, Run

router = APIRouter(prefix="/api/badge", tags=["badge"])

BADGE_TEMPLATE = """<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="20">
  <linearGradient id="b" x2="0" y2="100%">
    <stop offset="0" stop-color="#bbb" stop-opacity=".1"/>
    <stop offset="1" stop-opacity=".1"/>
  </linearGradient>
  <clipPath id="a"><rect width="{width}" height="20" rx="3" fill="#fff"/></clipPath>
  <g clip-path="url(#a)">
    <rect width="90" height="20" fill="#555"/>
    <rect x="90" width="{right_width}" height="20" fill="{color}"/>
    <rect width="{width}" height="20" fill="url(#b)"/>
  </g>
  <g fill="#fff" text-anchor="middle" font-family="DejaVu Sans,Verdana,sans-serif" font-size="11">
    <text x="45" y="15" fill="#010101" fill-opacity=".3">ModelWatch</text>
    <text x="45" y="14">ModelWatch</text>
    <text x="{text_x}" y="15" fill="#010101" fill-opacity=".3">{label}</text>
    <text x="{text_x}" y="14">{label}</text>
  </g>
</svg>"""


@router.get("/{workspace_id}.svg")
async def get_badge(
    workspace_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """Return a shields.io-style SVG badge showing monitoring status."""
    workspace = await db.get(Workspace, workspace_id)
    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")

    # Get worst recent drift score across all specs
    result = await db.execute(
        select(Run.drift_score)
        .join(Spec)
        .join(Endpoint)
        .where(Endpoint.workspace_id == workspace_id, Run.drift_score.isnot(None))
        .order_by(Run.created_at.desc())
        .limit(20)
    )
    scores = [r[0] for r in result.all()]

    if not scores:
        label = "no data"
        color = "#9f9f9f"
    else:
        max_score = max(scores)
        if max_score < 0.05:
            label = "stable"
            color = "#4c1"
        elif max_score < 0.35:
            label = "minor drift"
            color = "#dfb317"
        else:
            label = "drift detected"
            color = "#e05d44"

    right_width = len(label) * 7 + 10
    width = 90 + right_width
    text_x = 90 + right_width // 2

    svg = BADGE_TEMPLATE.format(
        width=width,
        right_width=right_width,
        color=color,
        label=label,
        text_x=text_x,
    )
    return Response(
        content=svg,
        media_type="image/svg+xml",
        headers={"Cache-Control": "no-cache, max-age=300"},
    )
