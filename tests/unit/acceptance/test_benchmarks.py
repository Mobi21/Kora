"""Benchmarks collector (AT3).

Validates that :func:`collect_benchmarks` pulls the right fields from a
full-state snapshot, computes latency percentiles correctly, produces a
flat CSV row with the published column order, and tolerates partial /
missing state.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from tests.acceptance.scenario.benchmarks import (
    CSV_COLUMNS,
    BenchmarkSummary,
    benchmarks_to_csv_row,
    benchmarks_to_json,
    collect_benchmarks,
)


def _state_with_pipelines() -> dict[str, Any]:
    return {
        "orchestration_state": {
            "available": True,
            "pipeline_instances": {
                "total": 3,
                "by_state": {"completed": 2, "failed": 1},
                "by_name": {"post_session_memory": 2, "post_memory_vault": 1},
                "recent": [],
            },
            "worker_tasks": {"total": 0, "active_count": 0},
            "work_ledger": {"total": 5, "by_event_type": {}, "recent": []},
            "trigger_state": {
                "total_triggers_tracked": 2,
                "last_fires": [
                    {"trigger_name": "event_insight_available",
                     "pipeline_name": "proactive_pattern_scan"},
                    {"trigger_name": "interval_deep_idle",
                     "pipeline_name": "session_bridge_pruning"},
                    {"trigger_name": "session_end_triage",
                     "pipeline_name": "post_session_memory"},
                ],
            },
            "system_state_log": {
                "transitions_total": 2,
                "by_phase": {"active_idle": 1, "deep_idle": 1},
                "current_phase": "deep_idle",
                "recent_transitions": [
                    {"to_phase": "active_idle",
                     "at": "2026-04-15T10:00:00+00:00"},
                    {"to_phase": "deep_idle",
                     "at": "2026-04-15T10:05:00+00:00"},
                    {"to_phase": "conversation",
                     "at": "2026-04-15T10:15:00+00:00"},
                ],
            },
            "request_limiter": {
                "total_requests_logged": 100,
                "by_class": {
                    "CONVERSATION": 50,
                    "BACKGROUND": 50,
                },
                "window_seconds": 18000,
                "in_window": 200,
            },
            "open_decisions": {"total": 0, "by_status": {}, "recent": []},
            "runtime_pipelines": {"total": 0},
        },
        "memory_lifecycle": {
            "memories": {
                "total": 8, "by_status": {"active": 6, "soft_deleted": 2},
                "with_consolidated_into": 3, "with_merged_from": 1,
            },
            "user_model_facts": {"total": 4},
            "entities": {"total": 5},
        },
        "vault_state": {
            "exists": True,
            "counts": {
                "total_notes": 50,
                "entities_people": 3, "entities_places": 1, "entities_projects": 2,
                "moc_pages": 4,
            },
            "working_docs": [
                {"path": "/a.md", "status": "in_progress"},
                {"path": "/b.md", "status": "done"},
            ],
            "wikilink_density": {
                "notes_with_wikilinks": 20, "total_wikilinks": 75,
            },
        },
        "proactive_state": {
            "notifications": {
                "total": 6,
                "by_tier": {"templated": 2, "llm": 4},
                "by_reason": {"delivered": 6},
            },
            "reminders": {"total": 0, "by_status": {}},
            "insights": {"persisted": False, "total_if_persisted": None},
        },
    }


def _initial_state() -> dict[str, Any]:
    """Before-snapshot: nothing yet."""
    return {
        "memory_lifecycle": {
            "memories": {"total": 2, "by_status": {"active": 2}},
            "entities": {"total": 1},
        },
    }


# ── Core: collect_benchmarks produces expected fields ────────────────────

def test_collect_benchmarks_from_state() -> None:
    metas = [
        {"role": "assistant", "latency_ms": 100, "prompt_tokens": 50,
         "completion_tokens": 20, "token_count": 70, "compaction_tier": "none"},
        {"role": "assistant", "latency_ms": 200, "prompt_tokens": 80,
         "completion_tokens": 30, "token_count": 110, "compaction_tier": "soft"},
        {"role": "assistant", "latency_ms": 150, "prompt_tokens": 60,
         "completion_tokens": 25, "token_count": 85, "compaction_tier": "none"},
    ]
    summary = asyncio.run(
        collect_benchmarks(metas, _state_with_pipelines(), _initial_state())
    )

    assert summary.response_count == 3
    assert summary.total_prompt_tokens == 190
    assert summary.total_completion_tokens == 75
    assert summary.tokens_per_response_mean > 0
    assert summary.compaction_tier_counts.get("soft") == 1
    assert summary.compaction_tier_counts.get("none") == 2

    # Request budget: 200 in-window / 1000 default capacity -> 0.8 remaining.
    assert 0 <= summary.remaining_budget_fraction <= 1
    assert summary.remaining_budget_fraction == 0.8

    # Pipelines
    assert summary.pipeline_fires_by_name["post_session_memory"] == 2
    assert summary.pipeline_success_count == 2
    assert summary.pipeline_fail_count == 1

    # Notifications
    assert summary.notifications_by_tier["templated"] == 2
    assert summary.notifications_by_tier["llm"] == 4

    # Memory deltas: current=8, initial=2 -> created=6.
    assert summary.memories_created == 6
    assert summary.memories_consolidated == 3
    assert summary.memories_dedup_merged == 2
    # Entities: current=5, initial=1 -> created=4.
    assert summary.entities_created == 4

    # Vault
    assert summary.vault_notes_total == 50
    assert summary.vault_wikilinks_total == 75
    assert summary.vault_entity_pages == 6  # people+places+projects
    assert summary.vault_moc_pages == 4
    assert summary.vault_working_docs_active == 1  # only one is in_progress

    # Phase dwell: 5 minutes active_idle (10:00->10:05), 10 min deep_idle.
    assert "active_idle" in summary.phase_dwell_seconds
    assert summary.phase_dwell_seconds["active_idle"] == 300
    assert summary.phase_dwell_seconds["deep_idle"] == 600


# ── JSON / CSV schemas ───────────────────────────────────────────────────

def test_benchmarks_to_json_schema() -> None:
    summary = BenchmarkSummary(
        response_latency_p50_ms=100.0,
        response_latency_p95_ms=150.0,
        response_count=5,
    )
    payload = benchmarks_to_json(summary)

    # Every dataclass field must round-trip into the JSON.
    expected_keys = {
        "response_latency_p50_ms", "response_latency_p95_ms", "response_count",
        "total_prompt_tokens", "total_completion_tokens",
        "tokens_per_response_mean",
        "requests_by_class", "remaining_budget_fraction",
        "compaction_tier_counts",
        "pipeline_fires_by_name", "pipeline_fires_by_trigger_type",
        "pipeline_success_count", "pipeline_fail_count",
        "notifications_by_tier", "notifications_by_reason",
        "memories_created", "memories_consolidated", "memories_dedup_merged",
        "entities_created", "entities_merged",
        "vault_notes_total", "vault_wikilinks_total",
        "vault_entity_pages", "vault_moc_pages", "vault_working_docs_active",
        "insights_persisted",
        "phase_dwell_seconds",
    }
    assert expected_keys <= set(payload.keys())


def test_benchmarks_to_csv_row_flat_dict() -> None:
    summary = BenchmarkSummary(
        response_count=2,
        requests_by_class={"CONVERSATION": 5, "BACKGROUND": 10},
        pipeline_fires_by_name={"pX": 3, "pY": 1},
        phase_dwell_seconds={"deep_idle": 120.5, "active_idle": 30.25},
        insights_persisted=None,
    )
    row = benchmarks_to_csv_row(summary)

    # All columns present in CSV_COLUMNS are in the row.
    for col in CSV_COLUMNS:
        assert col in row

    # Dicts are flattened with "k=v" pairs joined by ";".
    assert row["requests_by_class"] == "BACKGROUND=10;CONVERSATION=5"
    assert row["pipeline_fires_by_name"] == "pX=3;pY=1"
    assert "deep_idle" in row["phase_dwell_seconds"]

    # insights_persisted None → empty string (cannot be None in CSV).
    assert row["insights_persisted"] == ""


# ── Percentile math ──────────────────────────────────────────────────────

def test_latency_percentiles_computed_correctly() -> None:
    """Known latencies produce the expected p50 / p95."""
    metas = [
        {"role": "assistant", "latency_ms": v}
        for v in (10, 20, 30, 40, 50, 60, 70, 80, 90, 100)
    ]
    state = _state_with_pipelines()
    summary = asyncio.run(collect_benchmarks(metas, state))

    # With 10 samples, linear-interp p50 ≈ 55.0, p95 ≈ 95.5.
    assert summary.response_latency_p50_ms == 55.0
    assert abs(summary.response_latency_p95_ms - 95.5) < 0.01


def test_latency_percentiles_with_single_sample() -> None:
    metas = [{"role": "assistant", "latency_ms": 42}]
    summary = asyncio.run(collect_benchmarks(metas, _state_with_pipelines()))
    assert summary.response_latency_p50_ms == 42.0
    assert summary.response_latency_p95_ms == 42.0


def test_latency_percentiles_empty() -> None:
    summary = asyncio.run(collect_benchmarks([], _state_with_pipelines()))
    assert summary.response_count == 0
    assert summary.response_latency_p50_ms == 0.0
    assert summary.response_latency_p95_ms == 0.0


# ── Request budget calculation ───────────────────────────────────────────

def test_request_budget_calculation() -> None:
    state = _state_with_pipelines()
    # in_window = 200 by default
    summary = asyncio.run(
        collect_benchmarks([], state, request_budget_capacity=400)
    )
    # 200/400 used -> 0.5 remaining
    assert summary.remaining_budget_fraction == 0.5

    summary = asyncio.run(
        collect_benchmarks([], state, request_budget_capacity=100)
    )
    # 200/100 > 1 capped → remaining clamped to 0
    assert summary.remaining_budget_fraction == 0.0


# ── Missing / partial state tolerance ────────────────────────────────────

def test_collect_benchmarks_handles_empty_state() -> None:
    """Empty snapshot produces a zeroed summary without raising."""
    summary = asyncio.run(collect_benchmarks([], {}))
    assert summary.response_count == 0
    assert summary.total_prompt_tokens == 0
    assert summary.pipeline_fires_by_name == {}
    assert summary.vault_notes_total == 0
    assert summary.insights_persisted is None


def test_collect_benchmarks_handles_missing_memory_lifecycle() -> None:
    state = {
        "orchestration_state": {"available": True},
        # memory_lifecycle missing entirely
    }
    summary = asyncio.run(collect_benchmarks([], state))
    assert summary.memories_created == 0
    assert summary.entities_created == 0


# ── Response count / compaction histogram separation ────────────────────

def test_response_count_excludes_synthetic_compaction_metas() -> None:
    """Synthetic compaction-event entries must NOT inflate response_count.

    Real assistant turns carry ``role='assistant'`` (or ``is_response=True``).
    Compaction events are logged as separate metas with only
    ``compaction_tier`` set — they contribute to the compaction histogram
    but not to ``response_count`` or ``tokens_per_response_mean``.
    """
    metas = [
        # Two real assistant turns, no compaction.
        {"role": "assistant", "latency_ms": 100, "token_count": 50,
         "compaction_tier": "none"},
        {"role": "assistant", "latency_ms": 200, "token_count": 80,
         "compaction_tier": "none"},
        # Synthetic compaction events — NOT real turns.
        {"compaction_tier": "soft", "token_count": 500},
        {"compaction_tier": "hard", "token_count": 1200},
    ]
    summary = asyncio.run(collect_benchmarks(metas, {}))

    # Only the two real turns count as responses.
    assert summary.response_count == 2
    # Latency / token means also use the real count as denominator.
    assert summary.response_latency_p50_ms == 150.0
    # tokens_per_response_mean = (50 + 80) / 2 = 65.0 — synthetic
    # compaction token_counts (500, 1200) are NOT folded in.
    assert summary.tokens_per_response_mean == 65.0

    # Compaction histogram counts every meta — two "none" (the real
    # turns) plus one "soft" and one "hard" from the synthetic events.
    assert summary.compaction_tier_counts.get("none") == 2
    assert summary.compaction_tier_counts.get("soft") == 1
    assert summary.compaction_tier_counts.get("hard") == 1


def test_compaction_tier_histogram_mixed_responses() -> None:
    """Assistant messages with various compaction_tier values are counted correctly.

    This is the explicit coverage for AT3 issue 7 — verifying that the
    compaction_tier histogram reflects the per-response tiers rather than
    over-counting "none" when the tier is missing from message state.
    """
    metas = [
        {"role": "assistant", "latency_ms": 10, "compaction_tier": "none"},
        {"role": "assistant", "latency_ms": 20, "compaction_tier": "none"},
        {"role": "assistant", "latency_ms": 30, "compaction_tier": "soft"},
        {"role": "assistant", "latency_ms": 40, "compaction_tier": "hard"},
        {"role": "assistant", "latency_ms": 50, "compaction_tier": "soft"},
    ]
    summary = asyncio.run(collect_benchmarks(metas, {}))

    assert summary.response_count == 5
    assert summary.compaction_tier_counts == {
        "none": 2, "soft": 2, "hard": 1,
    }


def test_response_count_honors_is_response_flag() -> None:
    """``is_response=True`` also marks a meta as a real turn."""
    metas = [
        {"is_response": True, "latency_ms": 100, "token_count": 50},
        {"is_response": True, "latency_ms": 200, "token_count": 80},
        # No flag, no role — treated as synthetic, skipped from count.
        {"compaction_tier": "soft", "token_count": 500},
    ]
    summary = asyncio.run(collect_benchmarks(metas, {}))
    assert summary.response_count == 2
    assert summary.total_prompt_tokens == 0  # not provided


# ── Timestamp ordering robustness ────────────────────────────────────────

def test_append_benchmarks_csv_single_header_under_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Concurrent _append_benchmarks_csv calls must not double the header.

    Drives _append_benchmarks_csv from two threads at once and asserts
    the resulting file contains exactly one header row. Without the
    fcntl.LOCK_EX acquisition the racing writers could both observe an
    empty file, both write the header, and produce a malformed CSV.
    """
    import threading

    from tests.acceptance import _harness_server as hs_mod
    from tests.acceptance._harness_server import HarnessServer
    from tests.acceptance.scenario.benchmarks import CSV_COLUMNS

    # Redirect PROJECT_ROOT so the CSV lands in tmp_path/data/acceptance.
    monkeypatch.setattr(hs_mod, "PROJECT_ROOT", tmp_path)

    row = {col: "v" for col in CSV_COLUMNS}

    def _write(name: str) -> None:
        HarnessServer._append_benchmarks_csv(name, row)

    threads = [
        threading.Thread(target=_write, args=(f"snap-{i}",))
        for i in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    csv_path = tmp_path / "data" / "acceptance" / "benchmarks.csv"
    assert csv_path.exists()
    lines = csv_path.read_text().splitlines()
    # Exactly one header row, plus one row per snapshot writer.
    header_lines = [ln for ln in lines if ln.startswith("snapshot,captured_at")]
    assert len(header_lines) == 1, (
        f"expected exactly one CSV header, found {len(header_lines)}: {lines}"
    )
    # And 4 data rows corresponding to the 4 snapshot writes.
    data_rows = [ln for ln in lines if ln.startswith("snap-")]
    assert len(data_rows) == 4


def test_phase_dwell_handles_mixed_timezone_formats() -> None:
    """_compute_phase_dwell must parse ``Z`` and ``+00:00`` offsets correctly.

    Lexicographic compare of ``2026-04-15T10:05:00Z`` vs
    ``2026-04-15T10:00:00+00:00`` is ambiguous (``Z`` sorts after ``+``
    in ASCII) and can mis-decide the reverse operation. The parsed-dt
    path must produce a stable, correct order.
    """
    # DESC-ordered transitions with mixed ``Z`` / ``+00:00`` suffixes.
    state = {
        "orchestration_state": {
            "system_state_log": {
                "recent_transitions": [
                    # DESC by time — second entry is earliest.
                    {"to_phase": "deep_idle", "at": "2026-04-15T10:10:00Z"},
                    {"to_phase": "active_idle",
                     "at": "2026-04-15T10:05:00+00:00"},
                    {"to_phase": "conversation",
                     "at": "2026-04-15T10:00:00Z"},
                ]
            }
        }
    }
    summary = asyncio.run(collect_benchmarks([], state))
    # After reversal to chronological order:
    #   conversation @10:00 -> active_idle @10:05 -> deep_idle @10:10
    # -> conversation dwells 300s, active_idle dwells 300s.
    assert summary.phase_dwell_seconds.get("conversation") == 300
    assert summary.phase_dwell_seconds.get("active_idle") == 300
