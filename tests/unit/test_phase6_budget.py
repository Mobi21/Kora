"""Tests for kora_v2.autonomous.budget — Phase 6 budget enforcement."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from kora_v2.autonomous.budget import BudgetEnforcer
from kora_v2.autonomous.state import AutonomousState
from kora_v2.core.settings import AutonomousSettings, LLMSettings

# ── Helpers ───────────────────────────────────────────────────────────────


def _auto_settings(**kwargs) -> AutonomousSettings:
    """Build AutonomousSettings with optional overrides."""
    return AutonomousSettings(**kwargs)


def _llm_settings(**kwargs) -> LLMSettings:
    return LLMSettings(**kwargs)


def _make_state(**kwargs) -> AutonomousState:
    defaults = dict(
        session_id="s1",
        plan_id="p1",
        status="executing",
        started_at=datetime.now(UTC),
    )
    defaults.update(kwargs)
    return AutonomousState(**defaults)


def _enforcer(
    *,
    request_limit_per_hour: int | None = None,
    max_session_hours: float | None = None,
    per_session_cost_limit: float | None = None,
    context_window: int | None = None,
    warn_frac: float = 0.85,
    stop_frac: float = 1.0,
) -> BudgetEnforcer:
    """Build a BudgetEnforcer with fine-grained settings."""
    auto_kwargs: dict = {}
    if request_limit_per_hour is not None:
        auto_kwargs["request_limit_per_hour"] = request_limit_per_hour
    if max_session_hours is not None:
        auto_kwargs["max_session_hours"] = max_session_hours
    if per_session_cost_limit is not None:
        auto_kwargs["per_session_cost_limit"] = per_session_cost_limit

    auto = AutonomousSettings(**auto_kwargs)

    llm_kwargs: dict = {}
    if context_window is not None:
        llm_kwargs["context_window"] = context_window
    llm = LLMSettings(**llm_kwargs) if llm_kwargs else None

    return BudgetEnforcer(auto, llm, warn_frac, stop_frac)


# ── No limits → always ok ────────────────────────────────────────────────


class TestNoLimits:
    def test_no_limits_ok(self):
        enforcer = BudgetEnforcer(AutonomousSettings())
        state = _make_state()
        result = enforcer.check_before_step(state)
        assert result.ok is True
        assert result.hard_stop is False
        assert result.soft_warning is False


# ── Quota window (1-hour rate) ────────────────────────────────────────────


class TestQuotaWindow:
    def _enforcer_with_quota(self, limit: int = 100) -> BudgetEnforcer:
        auto = AutonomousSettings(request_limit_per_hour=limit)
        return BudgetEnforcer(auto)

    def test_below_warn_ok(self):
        enforcer = self._enforcer_with_quota(100)
        state = _make_state(request_window_1h=50)
        result = enforcer.check_before_step(state)
        assert result.ok is True
        assert not result.soft_warning

    def test_at_warn_threshold_soft_warning(self):
        enforcer = self._enforcer_with_quota(100)
        state = _make_state(request_window_1h=85)
        result = enforcer.check_before_step(state)
        assert result.ok is True
        assert result.soft_warning is True
        assert result.dimension == "quota"

    def test_at_hard_stop_threshold(self):
        enforcer = self._enforcer_with_quota(100)
        state = _make_state(request_window_1h=100)
        result = enforcer.check_before_step(state)
        assert result.ok is False
        assert result.hard_stop is True
        assert result.dimension == "quota"

    def test_over_limit_hard_stop(self):
        enforcer = self._enforcer_with_quota(100)
        state = _make_state(request_window_1h=150)
        result = enforcer.check_before_step(state)
        assert result.ok is False
        assert result.hard_stop is True

    def test_check_before_external_call_only_quota(self):
        """check_before_external_call must only look at the quota window."""
        auto = AutonomousSettings(
            request_limit_per_hour=100,
            max_session_hours=1,
            per_session_cost_limit=0.01,
        )
        enforcer = BudgetEnforcer(auto)
        # Elapsed > limit but quota fine → should pass
        state = _make_state(request_window_1h=50, elapsed_seconds=7200, cost_estimate=5.0)
        result = enforcer.check_before_external_call(state)
        assert result.ok is True


# ── Wall-time dimension ───────────────────────────────────────────────────


class TestWallTime:
    def test_below_wall_time_limit(self):
        auto = AutonomousSettings(max_session_hours=1)
        enforcer = BudgetEnforcer(auto)
        state = _make_state(elapsed_seconds=1800)  # 30 min
        result = enforcer.check_before_step(state)
        assert result.ok is True

    def test_at_wall_time_warn(self):
        auto = AutonomousSettings(max_session_hours=1)
        enforcer = BudgetEnforcer(auto, request_warning_threshold=0.85)
        state = _make_state(elapsed_seconds=3060)  # 51 min → 85%
        result = enforcer.check_before_step(state)
        assert result.soft_warning is True
        assert result.dimension == "time"

    def test_wall_time_hard_stop(self):
        auto = AutonomousSettings(max_session_hours=1)
        enforcer = BudgetEnforcer(auto)
        state = _make_state(elapsed_seconds=3600)
        result = enforcer.check_before_step(state)
        assert result.ok is False
        assert result.dimension == "time"


# ── Cost dimension ────────────────────────────────────────────────────────


class TestCostDimension:
    def test_cost_under_limit(self):
        auto = AutonomousSettings(per_session_cost_limit=1.0)
        enforcer = BudgetEnforcer(auto)
        state = _make_state(cost_estimate=0.5)
        result = enforcer.check_before_step(state)
        assert result.ok is True

    def test_cost_soft_warn(self):
        auto = AutonomousSettings(per_session_cost_limit=1.0)
        enforcer = BudgetEnforcer(auto, request_warning_threshold=0.85)
        state = _make_state(cost_estimate=0.85)
        result = enforcer.check_before_step(state)
        assert result.soft_warning is True
        assert result.dimension == "cost"

    def test_cost_hard_stop(self):
        auto = AutonomousSettings(per_session_cost_limit=1.0)
        enforcer = BudgetEnforcer(auto)
        state = _make_state(cost_estimate=1.0)
        result = enforcer.check_before_step(state)
        assert result.ok is False
        assert result.dimension == "cost"


# ── Token dimension ───────────────────────────────────────────────────────


class TestTokenDimension:
    def test_tokens_under_limit(self):
        auto = AutonomousSettings()
        llm = LLMSettings(context_window=100_000)
        enforcer = BudgetEnforcer(auto, llm)
        state = _make_state(token_estimate=50_000)
        result = enforcer.check_before_step(state)
        assert result.ok is True

    def test_tokens_soft_warn(self):
        auto = AutonomousSettings()
        llm = LLMSettings(context_window=100_000)
        enforcer = BudgetEnforcer(auto, llm, request_warning_threshold=0.85)
        state = _make_state(token_estimate=85_000)
        result = enforcer.check_before_step(state)
        assert result.soft_warning is True
        assert result.dimension == "token"

    def test_tokens_hard_stop(self):
        auto = AutonomousSettings()
        llm = LLMSettings(context_window=100_000)
        enforcer = BudgetEnforcer(auto, llm)
        state = _make_state(token_estimate=100_000)
        result = enforcer.check_before_step(state)
        assert result.ok is False
        assert result.dimension == "token"


# ── update_counters ───────────────────────────────────────────────────────


class TestUpdateCounters:
    def test_counters_accumulated(self):
        enforcer = BudgetEnforcer(AutonomousSettings())
        state = _make_state()
        updated = enforcer.update_counters(state, tokens_used=500, cost=0.01, requests=2)
        assert updated.token_estimate == 500
        assert updated.cost_estimate == pytest.approx(0.01)
        assert updated.request_count == 2
        assert updated.request_window_1h == 2

    def test_update_does_not_mutate_original(self):
        enforcer = BudgetEnforcer(AutonomousSettings())
        state = _make_state(request_count=10)
        enforcer.update_counters(state, tokens_used=100, cost=0.001)
        assert state.request_count == 10  # original unchanged

    def test_multiple_updates_accumulate(self):
        enforcer = BudgetEnforcer(AutonomousSettings())
        state = _make_state()
        state2 = enforcer.update_counters(state, tokens_used=200, cost=0.02)
        state3 = enforcer.update_counters(state2, tokens_used=300, cost=0.03)
        assert state3.token_estimate == 500
        assert state3.cost_estimate == pytest.approx(0.05)


# ── Priority order ────────────────────────────────────────────────────────


class TestPriorityOrder:
    def test_quota_checked_before_time(self):
        """Quota window fires first even when wall time is also over limit."""
        auto = AutonomousSettings(
            request_limit_per_hour=10,
            max_session_hours=1,
        )
        enforcer = BudgetEnforcer(auto)
        state = _make_state(request_window_1h=10, elapsed_seconds=7200)
        result = enforcer.check_before_step(state)
        assert result.dimension == "quota"

    def test_soft_warning_returned_immediately(self):
        """First dimension that triggers a soft warning is returned."""
        auto = AutonomousSettings(request_limit_per_hour=100, max_session_hours=2)
        enforcer = BudgetEnforcer(auto, request_warning_threshold=0.85)
        # quota at 90% → soft warning
        state = _make_state(request_window_1h=90, elapsed_seconds=100)
        result = enforcer.check_before_step(state)
        assert result.soft_warning is True
        assert result.dimension == "quota"
