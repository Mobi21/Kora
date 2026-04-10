"""Kora V2 — Decision management for autonomous pauses.

When an autonomous plan reaches a branch point that requires human
judgment, the executor creates a ``PendingDecision`` and parks the
session in ``waiting_on_user`` status.  ``DecisionManager`` tracks
these pending decisions and resolves them either via an explicit user
answer or (when policy allows) by auto-selecting the recommendation
after the timeout expires.

Policies:
  ``auto_select``  — auto-resolve to *recommendation* on timeout.
  ``never_auto``   — block indefinitely until the user responds; the
                     timeout is tracked but never triggers resolution.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

import structlog
from pydantic import BaseModel, Field

log = structlog.get_logger(__name__)

# ── Models ────────────────────────────────────────────────────────────────


class PendingDecision(BaseModel):
    """A decision waiting for user or auto-resolution."""

    decision_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    options: list[str]
    recommendation: str | None = None
    policy: Literal["auto_select", "never_auto"] = "auto_select"
    expires_at: datetime
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DecisionResult(BaseModel):
    """The resolved outcome of a ``PendingDecision``."""

    decision_id: str
    chosen: str
    method: Literal["user", "auto_select", "timeout"]
    decided_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ── Manager ───────────────────────────────────────────────────────────────


class DecisionManager:
    """Lifecycle manager for ``PendingDecision`` objects.

    Typical flow::

        decision = manager.create_decision(
            options=["proceed", "skip", "abort"],
            recommendation="proceed",
            policy="auto_select",
            timeout_minutes=10,
        )
        # ... later, on user input:
        result = manager.submit_answer(decision.decision_id, chosen="skip")
        # ... or, during a periodic check:
        result = manager.check_timeout(decision)  # None if not yet expired
    """

    def __init__(self) -> None:
        self._pending: dict[str, PendingDecision] = {}

    # ── Public API ────────────────────────────────────────────────────

    def create_decision(
        self,
        options: list[str],
        recommendation: str | None = None,
        policy: Literal["auto_select", "never_auto"] = "auto_select",
        timeout_minutes: int = 10,
    ) -> PendingDecision:
        """Create and register a new pending decision.

        Args:
            options: Non-empty list of valid choices.
            recommendation: The choice Kora recommends (must be in
                *options* when ``policy == "auto_select"``).
            policy: Resolution policy on timeout.
            timeout_minutes: How many minutes before the decision
                expires (used for ``auto_select`` auto-resolution and
                for tracking with ``never_auto``).

        Returns:
            The newly created ``PendingDecision``.

        Raises:
            ValueError: If *options* is empty, or if *recommendation*
                is provided but not in *options* for ``auto_select``
                policy.
        """
        if not options:
            raise ValueError("options must be a non-empty list")
        if policy == "auto_select" and recommendation is not None:
            if recommendation not in options:
                raise ValueError(
                    f"recommendation {recommendation!r} is not in options {options!r}"
                )

        now = datetime.now(UTC)
        decision = PendingDecision(
            options=options,
            recommendation=recommendation,
            policy=policy,
            expires_at=now + timedelta(minutes=timeout_minutes),
            created_at=now,
        )
        self._pending[decision.decision_id] = decision
        log.info(
            "decision_created",
            decision_id=decision.decision_id,
            policy=policy,
            options=options,
            recommendation=recommendation,
            timeout_minutes=timeout_minutes,
        )
        return decision

    def submit_answer(self, decision_id: str, chosen: str) -> DecisionResult:
        """Record a user-provided answer for a pending decision.

        Args:
            decision_id: ID of the ``PendingDecision`` to resolve.
            chosen: The option the user selected.

        Returns:
            A ``DecisionResult`` with ``method="user"``.

        Raises:
            KeyError: If *decision_id* is not found.
            ValueError: If *chosen* is not in the decision's options.
        """
        decision = self._pending.get(decision_id)
        if decision is None:
            raise KeyError(f"No pending decision with id={decision_id!r}")
        if chosen not in decision.options:
            raise ValueError(
                f"{chosen!r} is not a valid option {decision.options!r}"
            )
        result = DecisionResult(
            decision_id=decision_id,
            chosen=chosen,
            method="user",
        )
        del self._pending[decision_id]
        log.info("decision_resolved", decision_id=decision_id, chosen=chosen, method="user")
        return result

    def check_timeout(self, decision: PendingDecision) -> DecisionResult | None:
        """Check whether *decision* has expired and can be auto-resolved.

        For ``auto_select`` policy: if expired and a recommendation exists,
        auto-resolves to the recommendation.  If no recommendation, picks
        the first option.

        For ``never_auto`` policy: always returns ``None`` regardless of
        expiry — the decision blocks indefinitely.

        Args:
            decision: The ``PendingDecision`` to evaluate.

        Returns:
            A ``DecisionResult`` if auto-resolved, otherwise ``None``.
        """
        if decision.policy == "never_auto":
            return None

        if not self.is_expired(decision):
            return None

        # Auto-select: prefer recommendation, else first option
        chosen = decision.recommendation if decision.recommendation else decision.options[0]
        result = DecisionResult(
            decision_id=decision.decision_id,
            chosen=chosen,
            method="timeout",
        )
        # Remove from pending if still tracked
        self._pending.pop(decision.decision_id, None)
        log.info(
            "decision_timeout_auto_resolved",
            decision_id=decision.decision_id,
            chosen=chosen,
        )
        return result

    def is_expired(self, decision: PendingDecision) -> bool:
        """Return ``True`` if *decision* has passed its expiry time."""
        return datetime.now(UTC) >= decision.expires_at

    def get_pending(self, decision_id: str) -> PendingDecision | None:
        """Look up a pending decision by ID (``None`` if not found)."""
        return self._pending.get(decision_id)
