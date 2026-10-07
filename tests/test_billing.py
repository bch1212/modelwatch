"""Tests for billing service — plan limits and enforcement."""

import pytest
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.models.database import Base
from app.models.schemas import Plan, Workspace
from app.services.billing import get_limits, check_limit, reserve_run


class TestPlanLimits:
    def test_free_limits(self):
        limits = get_limits(Plan.free)
        assert limits["specs"] == 5
        assert limits["runs_per_month"] == 500
        assert limits["endpoints"] == 1

    def test_pro_limits(self):
        limits = get_limits(Plan.pro)
        assert limits["specs"] == 50
        assert limits["runs_per_month"] == 10_000
        assert limits["endpoints"] == 5

    def test_team_limits(self):
        limits = get_limits(Plan.team)
        assert limits["runs_per_month"] == 100_000

    def test_check_limit_ok(self):
        check_limit(3, 5, "Specs")  # should not raise

    def test_check_limit_exceeded(self):
        with pytest.raises(ValueError, match="limit reached"):
            check_limit(5, 5, "Specs")

    def test_check_limit_over(self):
        with pytest.raises(ValueError, match="limit reached"):
            check_limit(10, 5, "Specs")


@pytest.mark.asyncio
async def test_concurrent_run_reservations_stop_at_limit(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'runs.sqlite'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as db:
        workspace = Workspace(email="quota@example.test", api_key="mw_synthetic", name="Test", plan=Plan.free, runs_this_month=0)
        db.add(workspace)
        await db.commit()
        workspace_id = workspace.id

    async def attempt():
        async with factory() as db:
            workspace = await db.get(Workspace, workspace_id)
            claimed = await reserve_run(db, workspace, 1)
            await db.commit()
            return claimed

    try:
        assert sorted(await asyncio.gather(attempt(), attempt(), attempt())) == [False, False, True]
        async with factory() as db:
            assert (await db.get(Workspace, workspace_id)).runs_this_month == 1
    finally:
        await engine.dispose()
