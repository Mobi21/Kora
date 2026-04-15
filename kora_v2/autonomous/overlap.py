"""Kora V2 — Compatibility shim for ``kora_v2.runtime.orchestration.overlap``.

Per spec §17.7a, the topic-overlap scoring function was moved to the
orchestration package so any pipeline can reuse it. This module
re-exports the *public* names so existing import sites
(``from kora_v2.autonomous.overlap import check_topic_overlap``) keep
working for one release. The cutover in Slice 7.5c deletes this shim.

Intentionally **no private helpers** are re-exported. Callers that need
``_tokenize`` / ``_cosine`` / ``_lexical_jaccard`` / ``_classify`` must
import them directly from ``kora_v2.runtime.orchestration.overlap`` —
the shim is a public-surface bridge, not a backdoor onto the module's
internals.
"""

from __future__ import annotations

from kora_v2.runtime.orchestration.overlap import (
    OverlapResult,
    check_topic_overlap,
)

__all__ = [
    "OverlapResult",
    "check_topic_overlap",
]
