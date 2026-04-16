"""Benchmarks dashboard rendering (AT4).

Covers the polished AT3 Benchmarks section in
``tests/acceptance/_report.py::_render_benchmarks_dashboard``:

* Rendered when at least one ``<snapshot>.benchmarks.json`` sidecar
  exists alongside snapshots
* Omitted gracefully when no sidecar exists
* Latency p50 / p95 surfaced with their numeric values
* Pipeline fires, memory lifecycle, and vault tables present
* Trend mini-table appears when more than one sidecar is on disk
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.acceptance._report import _render_benchmarks_dashboard


def _bench_sidecar(
    snap_dir: Path, name: str, payload: dict[str, Any]
) -> Path:
    path = snap_dir / f"{name}.benchmarks.json"
    path.write_text(json.dumps(payload))
    return path


def _full_payload() -> dict[str, Any]:
    return {
        "response_count": 12,
        "response_latency_p50_ms": 432.5,
        "response_latency_p95_ms": 1180.0,
        "total_prompt_tokens": 38400,
        "total_completion_tokens": 11000,
        "tokens_per_response_mean": 4116.7,
        "requests_by_class": {
            "CONVERSATION": 8,
            "BACKGROUND": 17,
            "NOTIFICATION": 3,
        },
        "remaining_budget_fraction": 0.7321,
        "compaction_tier_counts": {"none": 9, "soft": 2, "hard": 1},
        "pipeline_fires_by_name": {
            "post_session_memory": 2,
            "post_memory_vault": 1,
            "proactive_pattern_scan": 4,
        },
        "pipeline_fires_by_trigger_type": {
            "event": 5,
            "interval": 1,
            "session_end": 1,
        },
        "pipeline_success_count": 6,
        "pipeline_fail_count": 1,
        "notifications_by_tier": {"templated": 3, "llm": 1},
        "notifications_by_reason": {"pattern_nudge": 2, "wake_up": 1},
        "memories_created": 14,
        "memories_consolidated": 3,
        "memories_dedup_merged": 2,
        "entities_created": 5,
        "entities_merged": 1,
        "vault_notes_total": 42,
        "vault_wikilinks_total": 88,
        "vault_entity_pages": 7,
        "vault_moc_pages": 3,
        "vault_working_docs_active": 2,
        "phase_dwell_seconds": {
            "active_idle": 120.0,
            "deep_idle": 1200.0,
        },
    }


def test_report_includes_benchmarks_section_when_sidecar_exists(
    tmp_path: Path,
) -> None:
    snap = tmp_path / "snapshots"
    snap.mkdir()
    _bench_sidecar(snap, "day1_end", _full_payload())

    out = "\n".join(_render_benchmarks_dashboard(snap))
    assert "## Benchmarks" in out
    assert "day1_end" in out


def test_report_omits_benchmarks_section_when_no_sidecar(
    tmp_path: Path,
) -> None:
    snap = tmp_path / "snapshots"
    snap.mkdir()

    out = _render_benchmarks_dashboard(snap)
    assert out == [], (
        f"expected empty list when no sidecar exists, got {out!r}"
    )


def test_render_benchmarks_dashboard_handles_missing_snapshots_dir(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "does_not_exist"
    result = _render_benchmarks_dashboard(missing)
    assert result == []


def test_report_renders_latency_p50_p95_correctly(
    tmp_path: Path,
) -> None:
    snap = tmp_path / "snapshots"
    snap.mkdir()
    _bench_sidecar(snap, "snap_one", _full_payload())

    out = "\n".join(_render_benchmarks_dashboard(snap))
    # Header table present
    assert "### Latency" in out
    # Both percentiles surfaced with their literal numeric values
    assert "432.5" in out
    assert "1180.0" in out
    # Response count present
    assert "| Responses | 12 |" in out


def test_report_renders_pipeline_fires_table(tmp_path: Path) -> None:
    snap = tmp_path / "snapshots"
    snap.mkdir()
    _bench_sidecar(snap, "snap_two", _full_payload())

    out = "\n".join(_render_benchmarks_dashboard(snap))
    assert "### Pipelines" in out
    # Top-fires row should be sorted DESC by count — proactive_pattern_scan=4
    # comes first, then post_session_memory=2, then post_memory_vault=1.
    pipelines_idx = out.find("### Pipelines")
    pipelines_section = out[pipelines_idx:pipelines_idx + 600]
    assert "proactive_pattern_scan" in pipelines_section
    assert "post_session_memory" in pipelines_section
    assert "post_memory_vault" in pipelines_section
    # success / fail summary
    assert "success=6" in out
    assert "fail=1" in out


def test_report_renders_memory_lifecycle_table(tmp_path: Path) -> None:
    snap = tmp_path / "snapshots"
    snap.mkdir()
    _bench_sidecar(snap, "snap_mem", _full_payload())

    out = "\n".join(_render_benchmarks_dashboard(snap))
    assert "### Memory Lifecycle" in out
    # Five-column table — header includes all five lifecycle metrics.
    assert "Memories created" in out
    assert "Consolidated" in out
    assert "Dedup-merged" in out
    assert "Entities created" in out
    assert "Entities merged" in out
    # Body row carries the actual counts.
    assert "| 14 | 3 | 2 | 5 | 1 |" in out


def test_report_renders_vault_artifacts_table(tmp_path: Path) -> None:
    snap = tmp_path / "snapshots"
    snap.mkdir()
    _bench_sidecar(snap, "snap_vault", _full_payload())

    out = "\n".join(_render_benchmarks_dashboard(snap))
    assert "### Vault" in out
    assert "Notes" in out
    assert "Wikilinks" in out
    assert "Entity pages" in out
    assert "MOC pages" in out
    assert "Active working docs" in out
    # Body row carries the actual counts.
    assert "| 42 | 88 | 7 | 3 | 2 |" in out


def test_report_trend_section_with_multiple_sidecars(
    tmp_path: Path,
) -> None:
    snap = tmp_path / "snapshots"
    snap.mkdir()

    first = _full_payload()
    first["response_latency_p50_ms"] = 300.0
    first["vault_notes_total"] = 10
    first_path = _bench_sidecar(snap, "day1_start", first)

    second = _full_payload()
    second["response_latency_p50_ms"] = 500.0
    second["vault_notes_total"] = 50
    second_path = _bench_sidecar(snap, "day3_final", second)

    # Bump mtime so the order is deterministic and ``day3_final`` is the
    # newest (the one rendered as "latest").
    import os
    older = first_path.stat().st_mtime
    os.utime(second_path, (older + 10, older + 10))

    out = "\n".join(_render_benchmarks_dashboard(snap))
    # Latest header references the most-recent sidecar.
    assert "day3_final" in out
    # Trend section surfaces both rows.
    assert "### Trend across snapshots" in out
    assert "day1_start" in out
    # Both p50 values appear in the trend row column.
    assert "300.0" in out
    assert "500.0" in out
    # Both vault-note totals appear.
    assert "| 10 |" in out
    assert "| 50 |" in out


@pytest.mark.parametrize("missing_section", [
    "compaction_tier_counts",
    "pipeline_fires_by_name",
    "notifications_by_tier",
    "phase_dwell_seconds",
    "requests_by_class",
])
def test_report_renders_when_optional_sections_absent(
    tmp_path: Path, missing_section: str,
) -> None:
    """Optional sections drop out cleanly when the underlying data is empty."""
    snap = tmp_path / "snapshots"
    snap.mkdir()
    payload = _full_payload()
    payload[missing_section] = {}
    _bench_sidecar(snap, "partial", payload)

    out = "\n".join(_render_benchmarks_dashboard(snap))
    # Required sections always render.
    assert "### Latency" in out
    assert "### Memory Lifecycle" in out
    assert "### Vault" in out
    # The section keyed by ``missing_section`` must NOT appear when its
    # underlying data is empty — this is the consistency contract the
    # other optional sections honour.
    _section_header_for_key = {
        "compaction_tier_counts": "### Compaction",
        "pipeline_fires_by_name": "### Pipelines",
        "notifications_by_tier": "### Notifications",
        "phase_dwell_seconds": "### Phase Dwell Time",
        "requests_by_class": "### Request Budget",
    }
    omitted_header = _section_header_for_key[missing_section]
    # ``### Notifications`` may still appear when ``notifications_by_reason``
    # is non-empty even if ``notifications_by_tier`` is empty — guard the
    # assertion only for sections with a single keyed source.
    if missing_section != "notifications_by_tier":
        assert omitted_header not in out, (
            f"Section {omitted_header!r} should be omitted when "
            f"{missing_section!r} is empty"
        )
