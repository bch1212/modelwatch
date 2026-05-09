"""APScheduler integration — runs all active specs on their configured schedule.

Schedules:
  - hourly  → every 60 min
  - daily   → every 24h at 06:00 UTC
  - weekly  → every Monday at 06:00 UTC
  - on_trigger → manual only (not scheduled)

Also resets monthly run counters on the 1st of each month.
"""

import asyncio
import logging
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select

from app.models.database import get_session_factory
from app.models.schemas import (
    Spec, Endpoint, Workspace, DriftEvent,
    SpecSchedule, RunStatus,
)
from app.services.drift_detector import run_spec
from app.services import alerts
from app.services.billing import get_limits

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()


async def _run_scheduled_specs(schedule: SpecSchedule):
    """Execute all active specs with the given schedule."""
    logger.info(f"Starting scheduled run for {schedule.value} specs")

    async with get_session_factory()() as db:
        result = await db.execute(
            select(Spec)
            .join(Endpoint)
            .where(Spec.is_active.is_(True), Endpoint.is_active.is_(True))
            .where(Spec.schedule == schedule)
        )
        specs = result.scalars().all()
        logger.info(f"Found {len(specs)} {schedule.value} specs to run")

        for spec in specs:
            try:
                # Load endpoint + workspace for limit checking
                endpoint = await db.get(Endpoint, spec.endpoint_id)
                if not endpoint:
                    continue
                workspace = await db.get(Workspace, endpoint.workspace_id)
                if not workspace:
                    continue

                # Check run limit
                limits = get_limits(workspace.plan)
                if workspace.runs_this_month >= limits["runs_per_month"]:
                    logger.warning(
                        f"Workspace {workspace.id} hit run limit, skipping spec {spec.id}"
                    )
                    continue

                run = await run_spec(db, spec)
                workspace.runs_this_month += 1

                # Alert on drift
                if run.drift_score and run.drift_score > 0.05:
                    drift_result = await db.execute(
                        select(DriftEvent).where(DriftEvent.run_id == run.id)
                    )
                    event = drift_result.scalar_one_or_none()
                    if event:
                        alert_result = await alerts.dispatch_alerts(
                            alert_email=workspace.alert_email,
                            slack_webhook_url=workspace.slack_webhook_url,
                            spec_name=spec.name,
                            endpoint_name=endpoint.name,
                            severity=event.severity.value,
                            drift_score=event.drift_score,
                            summary=event.summary or "",
                        )
                        event.email_sent = alert_result["email_sent"]
                        event.slack_sent = alert_result["slack_sent"]

                await db.commit()
                logger.info(
                    f"Spec {spec.name}: drift_score={run.drift_score:.4f}"
                    if run.drift_score is not None else f"Spec {spec.name}: baseline set"
                )

            except Exception as exc:
                logger.exception(f"Error running spec {spec.id}: {exc}")
                await db.rollback()


async def _reset_monthly_counters():
    """Reset runs_this_month on the 1st of each month."""
    logger.info("Resetting monthly run counters")
    async with get_session_factory()() as db:
        result = await db.execute(select(Workspace))
        for workspace in result.scalars().all():
            workspace.runs_this_month = 0
            workspace.runs_reset_at = datetime.now(timezone.utc)
        await db.commit()


def start_scheduler():
    """Add jobs and start the APScheduler."""
    # Hourly specs — every hour at :00
    scheduler.add_job(
        _run_scheduled_specs,
        trigger=IntervalTrigger(hours=1),
        args=[SpecSchedule.hourly],
        id="run_hourly_specs",
        replace_existing=True,
    )

    # Daily specs — 06:00 UTC
    scheduler.add_job(
        _run_scheduled_specs,
        trigger=CronTrigger(hour=6, minute=0),
        args=[SpecSchedule.daily],
        id="run_daily_specs",
        replace_existing=True,
    )

    # Weekly specs — Monday 06:00 UTC
    scheduler.add_job(
        _run_scheduled_specs,
        trigger=CronTrigger(day_of_week="mon", hour=6, minute=0),
        args=[SpecSchedule.weekly],
        id="run_weekly_specs",
        replace_existing=True,
    )

    # Monthly counter reset — 1st of month 00:05 UTC
    scheduler.add_job(
        _reset_monthly_counters,
        trigger=CronTrigger(day=1, hour=0, minute=5),
        id="reset_monthly_counters",
        replace_existing=True,
    )

    scheduler.start()
    logger.info("APScheduler started with hourly/daily/weekly jobs")


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("APScheduler stopped")
