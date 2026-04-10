"""Kora V2 — Autonomous budget enforcement.

``BudgetEnforcer`` checks five spending dimensions in priority order before
each step and before every external LLM call.  It returns a structured
``BudgetCheckResult`` rather than raising exceptions so callers can decide
how to respond (warn, pause, hard-stop).
"""

from __future__ import annotations

import structlog
from pydantic import BaseModel

from kora_v2.autonomous.state import AutonomousState
from kora_v2.core.settings import AutonomousSettings, LLMSettings

log = structlog.get_logger(__name__)

# ── Result model ──────────────────────────────────────────────────────────


class BudgetCheckResult(BaseModel):
    """Outcome of a single budget check across all five dimensions."""

    ok: bool
    hard_stop: bool = False
    soft_warning: bool = False
    reason: str = ""
    dimension: str = ""  # which axis triggered: quota/request/time/cost/token


# ── Enforcer ─────────────────────────────────────────────────────────────


class BudgetEnforcer:
    """Stateless budget checker.  Instantiate once per autonomous session.

    Args:
        autonomous: ``AutonomousSettings`` from ``get_settings().autonomous``.
        llm: ``LLMSettings`` from ``get_settings().llm`` — used for the
            context-window cap and per-session request estimate.
        request_warning_threshold: Fraction of each cap at which a soft
            warning is emitted (default 0.85).
        request_hard_stop_threshold: Fraction at which a hard stop fires
            (default 1.0 — i.e. at exactly the cap).
    """

    def __init__(
        self,
        autonomous: AutonomousSettings,
        llm: LLMSettings | None = None,
        request_warning_threshold: float = 0.85,
        request_hard_stop_threshold: float = 1.0,
    ) -> None:
        self._auto = autonomous
        self._llm = llm
        self._warn_frac = request_warning_threshold
        self._stop_frac = request_hard_stop_threshold

    # ── Public API ────────────────────────────────────────────────────

    def check_before_step(self, state: AutonomousState) -> BudgetCheckResult:
        """Check all five budget axes in priority order.

        Priority:
          1. provider quota window (1-hour rate limit)
          2. total request count vs estimated session limit
          3. wall-clock time vs ``max_session_hours``
          4. cost estimate vs ``per_session_cost_limit``
          5. token estimate vs context window
        """
        checks = [
            self._check_quota_window(state),
            self._check_request_count(state),
            self._check_wall_time(state),
            self._check_cost(state),
            self._check_tokens(state),
        ]
        # Hard stops from any axis take priority; soft warnings are collected
        # but never short-circuit lower-priority hard stops.
        first_warning: BudgetCheckResult | None = None
        for result in checks:
            if not result.ok:  # hard stop
                return result
            if result.soft_warning and first_warning is None:
                first_warning = result
        return first_warning or BudgetCheckResult(ok=True)

    def check_before_external_call(self, state: AutonomousState) -> BudgetCheckResult:
        """Lightweight check — only the provider quota window.

        Used before every LLM/API call to avoid triggering rate limits.
        """
        return self._check_quota_window(state)

    def update_counters(
        self,
        state: AutonomousState,
        tokens_used: int,
        cost: float,
        requests: int = 1,
    ) -> AutonomousState:
        """Return a new ``AutonomousState`` with counters incremented.

        Does *not* mutate the input object.
        """
        updated = state.model_copy(deep=True)
        updated.request_count += requests
        updated.request_window_1h += requests
        updated.token_estimate += tokens_used
        updated.cost_estimate += cost
        return updated

    # ── Private helpers ───────────────────────────────────────────────

    def _check_quota_window(self, state: AutonomousState) -> BudgetCheckResult:
        limit = getattr(self._auto, "request_limit_per_hour", None)
        if limit is None or limit <= 0:
            return BudgetCheckResult(ok=True)

        ratio = state.request_window_1h / limit
        return self._make_result(ratio, "quota", f"1-hour request window: {state.request_window_1h}/{limit}")

    def _check_request_count(self, state: AutonomousState) -> BudgetCheckResult:
        max_hours = getattr(self._auto, "max_session_hours", 0)
        # Estimate: one request every 2 minutes
        estimated_limit = int(max_hours * 60 / 2) if max_hours else 0
        explicit_limit = getattr(self._auto, "max_request_count", None)
        limit = explicit_limit if explicit_limit else estimated_limit
        if not limit or limit <= 0:
            return BudgetCheckResult(ok=True)

        ratio = state.request_count / limit
        return self._make_result(ratio, "request", f"total requests: {state.request_count}/{limit}")

    def _check_wall_time(self, state: AutonomousState) -> BudgetCheckResult:
        max_hours = getattr(self._auto, "max_session_hours", 0)
        if not max_hours or max_hours <= 0:
            return BudgetCheckResult(ok=True)

        limit_seconds = max_hours * 3600
        ratio = state.elapsed_seconds / limit_seconds
        return self._make_result(
            ratio,
            "time",
            f"elapsed {state.elapsed_seconds}s / {limit_seconds}s ({max_hours}h limit)",
        )

    def _check_cost(self, state: AutonomousState) -> BudgetCheckResult:
        cost_limit = getattr(self._auto, "per_session_cost_limit", None)
        if cost_limit is None or cost_limit <= 0:
            return BudgetCheckResult(ok=True)

        ratio = state.cost_estimate / cost_limit
        return self._make_result(
            ratio,
            "cost",
            f"cost ${state.cost_estimate:.4f} / ${cost_limit:.4f}",
        )

    def _check_tokens(self, state: AutonomousState) -> BudgetCheckResult:
        context_window = getattr(self._llm, "context_window", None) if self._llm else None
        if context_window is None or context_window <= 0:
            return BudgetCheckResult(ok=True)

        ratio = state.token_estimate / context_window
        return self._make_result(
            ratio,
            "token",
            f"tokens {state.token_estimate} / {context_window}",
        )

    def _make_result(self, ratio: float, dimension: str, detail: str) -> BudgetCheckResult:
        """Convert a usage-ratio to a BudgetCheckResult."""
        if ratio >= self._stop_frac:
            log.warning(
                "budget_hard_stop",
                dimension=dimension,
                ratio=ratio,
                detail=detail,
            )
            return BudgetCheckResult(
                ok=False,
                hard_stop=True,
                soft_warning=False,
                reason=f"Hard stop — {detail}",
                dimension=dimension,
            )
        if ratio >= self._warn_frac:
            log.info(
                "budget_soft_warning",
                dimension=dimension,
                ratio=ratio,
                detail=detail,
            )
            return BudgetCheckResult(
                ok=True,
                hard_stop=False,
                soft_warning=True,
                reason=f"Approaching limit — {detail}",
                dimension=dimension,
            )
        return BudgetCheckResult(ok=True)
