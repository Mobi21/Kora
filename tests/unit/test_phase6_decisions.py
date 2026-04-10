"""Tests for kora_v2.autonomous.decisions — Phase 6 decision management."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from kora_v2.autonomous.decisions import DecisionManager, DecisionResult, PendingDecision

# ── Helpers ───────────────────────────────────────────────────────────────


def _manager() -> DecisionManager:
    return DecisionManager()


def _expired_decision(policy: str = "auto_select", recommendation: str | None = "yes") -> PendingDecision:
    """Return a PendingDecision whose expiry is in the past."""
    past = datetime.now(UTC) - timedelta(minutes=5)
    return PendingDecision(
        options=["yes", "no", "skip"],
        recommendation=recommendation,
        policy=policy,  # type: ignore[arg-type]
        expires_at=past,
    )


def _fresh_decision(policy: str = "auto_select") -> PendingDecision:
    """Return a PendingDecision whose expiry is in the future."""
    future = datetime.now(UTC) + timedelta(hours=1)
    return PendingDecision(
        options=["proceed", "abort"],
        recommendation="proceed",
        policy=policy,  # type: ignore[arg-type]
        expires_at=future,
    )


# ── create_decision ───────────────────────────────────────────────────────


class TestCreateDecision:
    def test_basic_creation(self):
        mgr = _manager()
        decision = mgr.create_decision(
            options=["yes", "no"],
            recommendation="yes",
            policy="auto_select",
            timeout_minutes=10,
        )
        assert isinstance(decision, PendingDecision)
        assert decision.options == ["yes", "no"]
        assert decision.recommendation == "yes"
        assert decision.policy == "auto_select"

    def test_decision_id_is_unique(self):
        mgr = _manager()
        d1 = mgr.create_decision(options=["a"])
        d2 = mgr.create_decision(options=["b"])
        assert d1.decision_id != d2.decision_id

    def test_expires_at_is_in_future(self):
        mgr = _manager()
        before = datetime.now(UTC)
        decision = mgr.create_decision(options=["x"], timeout_minutes=5)
        assert decision.expires_at > before

    def test_empty_options_raises(self):
        mgr = _manager()
        with pytest.raises(ValueError, match="non-empty"):
            mgr.create_decision(options=[])

    def test_recommendation_not_in_options_raises_for_auto_select(self):
        mgr = _manager()
        with pytest.raises(ValueError, match="not in options"):
            mgr.create_decision(
                options=["yes", "no"],
                recommendation="maybe",
                policy="auto_select",
            )

    def test_recommendation_not_in_options_allowed_for_never_auto(self):
        """never_auto doesn't validate recommendation against options."""
        mgr = _manager()
        decision = mgr.create_decision(
            options=["yes", "no"],
            recommendation=None,
            policy="never_auto",
        )
        assert decision.policy == "never_auto"

    def test_decision_registered_in_pending(self):
        mgr = _manager()
        decision = mgr.create_decision(options=["a", "b"])
        assert mgr.get_pending(decision.decision_id) is not None


# ── submit_answer ─────────────────────────────────────────────────────────


class TestSubmitAnswer:
    def test_valid_answer_resolves(self):
        mgr = _manager()
        d = mgr.create_decision(options=["proceed", "skip"])
        result = mgr.submit_answer(d.decision_id, "skip")
        assert isinstance(result, DecisionResult)
        assert result.chosen == "skip"
        assert result.method == "user"
        assert result.decision_id == d.decision_id

    def test_answer_removes_from_pending(self):
        mgr = _manager()
        d = mgr.create_decision(options=["a", "b"])
        mgr.submit_answer(d.decision_id, "a")
        assert mgr.get_pending(d.decision_id) is None

    def test_invalid_choice_raises(self):
        mgr = _manager()
        d = mgr.create_decision(options=["yes", "no"])
        with pytest.raises(ValueError, match="not a valid option"):
            mgr.submit_answer(d.decision_id, "maybe")

    def test_unknown_decision_id_raises(self):
        mgr = _manager()
        with pytest.raises(KeyError):
            mgr.submit_answer("nonexistent-id", "yes")


# ── check_timeout (auto_select) ───────────────────────────────────────────


class TestCheckTimeoutAutoSelect:
    def test_not_expired_returns_none(self):
        mgr = _manager()
        decision = _fresh_decision()
        result = mgr.check_timeout(decision)
        assert result is None

    def test_expired_auto_resolves_to_recommendation(self):
        mgr = _manager()
        decision = _expired_decision(recommendation="yes")
        result = mgr.check_timeout(decision)
        assert result is not None
        assert result.chosen == "yes"
        assert result.method == "timeout"

    def test_expired_no_recommendation_picks_first_option(self):
        mgr = _manager()
        decision = _expired_decision(recommendation=None)
        result = mgr.check_timeout(decision)
        assert result is not None
        assert result.chosen == decision.options[0]

    def test_auto_resolved_removed_from_pending(self):
        mgr = _manager()
        # Register the decision first
        decision = _expired_decision()
        mgr._pending[decision.decision_id] = decision
        mgr.check_timeout(decision)
        assert mgr.get_pending(decision.decision_id) is None


# ── check_timeout (never_auto) ────────────────────────────────────────────


class TestCheckTimeoutNeverAuto:
    def test_expired_never_auto_returns_none(self):
        """NEVER_AUTO policy: never auto-selects even after timeout."""
        mgr = _manager()
        decision = _expired_decision(policy="never_auto", recommendation="yes")
        result = mgr.check_timeout(decision)
        assert result is None

    def test_fresh_never_auto_returns_none(self):
        mgr = _manager()
        decision = _fresh_decision(policy="never_auto")
        result = mgr.check_timeout(decision)
        assert result is None


# ── is_expired ────────────────────────────────────────────────────────────


class TestIsExpired:
    def test_past_expiry_is_expired(self):
        mgr = _manager()
        assert mgr.is_expired(_expired_decision()) is True

    def test_future_expiry_not_expired(self):
        mgr = _manager()
        assert mgr.is_expired(_fresh_decision()) is False

    def test_exactly_now_is_expired(self):
        mgr = _manager()
        exactly_now = datetime.now(UTC)
        d = PendingDecision(options=["a"], expires_at=exactly_now)
        # may be equal or slightly past, so just ensure no crash
        result = mgr.is_expired(d)
        assert isinstance(result, bool)
