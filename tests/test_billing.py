"""Tests for billing service — plan limits and enforcement."""

import pytest
from app.models.schemas import Plan
from app.services.billing import get_limits, check_limit


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
