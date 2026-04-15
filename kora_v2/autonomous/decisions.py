"""Kora V2 — Compatibility shim for ``kora_v2.runtime.orchestration.decisions``.

Per spec §17.7a, the ``DecisionManager`` primitive was moved to the
orchestration package so non-autonomous pipelines can reuse it. This
module re-exports the public names so existing import sites
(``from kora_v2.autonomous.decisions import DecisionManager``) keep
working for one release. The cutover in Slice 7.5c deletes this shim.
"""

from __future__ import annotations

from kora_v2.runtime.orchestration.decisions import (
    DecisionManager,
    DecisionResult,
    PendingDecision,
)

__all__ = ["DecisionManager", "DecisionResult", "PendingDecision"]
