"""Benchmarks collector (AT3).

Given a list of per-response metadata dicts (from ``send_message``) and a
full-state snapshot, produce a :class:`BenchmarkSummary` that captures
the numbers worth tracking across runs:

* Latency (p50 / p95) over assistant responses
* Token accounting and per-response means
* RequestLimiter usage by class + remaining-window fraction
* Compaction tier histogram
* Pipeline fires by name / trigger type and success/fail counts
* Notifications by tier / reason
* Memory lifecycle deltas (creation, consolidation, dedup, entities)
* Vault growth (notes / wikilinks / entity pages / MOC pages /
  working docs)
* Insight count (when persistence lands)
* Idle phase dwell time (seconds per phase, from system_state_log)

The JSON shape is *stable* — downstream tools trend on it. Adding fields
is OK; renaming / removing isn't (AT4 publishes the schema).

Missing state is tolerated everywhere: if ``projection.db`` is absent,
memory fields are zero rather than raising. ``collect_benchmarks`` also
accepts an ``initial_state`` for delta-based fields — when ``None``, the
absolute values are reported.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

# ── Summary dataclass ────────────────────────────────────────────────────

@dataclass
class BenchmarkSummary:
    """Serialisable benchmark row. Field order is the CSV column order."""

    # ── Latency ──
    response_latency_p50_ms: float = 0.0
    response_latency_p95_ms: float = 0.0
    response_count: int = 0

    # ── Tokens ──
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    tokens_per_response_mean: float = 0.0

    # ── Request budget ──
    requests_by_class: dict[str, int] = field(default_factory=dict)
    remaining_budget_fraction: float = 1.0

    # ── Compaction ──
    compaction_tier_counts: dict[str, int] = field(default_factory=dict)

    # ── Pipelines ──
    pipeline_fires_by_name: dict[str, int] = field(default_factory=dict)
    pipeline_fires_by_trigger_type: dict[str, int] = field(default_factory=dict)
    pipeline_success_count: int = 0
    pipeline_fail_count: int = 0

    # ── Notifications ──
    notifications_by_tier: dict[str, int] = field(default_factory=dict)
    notifications_by_reason: dict[str, int] = field(default_factory=dict)

    # ── Memory lifecycle ──
    memories_created: int = 0
    memories_consolidated: int = 0
    memories_dedup_merged: int = 0
    entities_created: int = 0
    entities_merged: int = 0

    # ── Vault ──
    vault_notes_total: int = 0
    vault_wikilinks_total: int = 0
    vault_entity_pages: int = 0
    vault_moc_pages: int = 0
    vault_working_docs_active: int = 0

    # ── Insights ──
    insights_persisted: int | None = None

    # ── Idle dwell (seconds per SystemStatePhase value) ──
    phase_dwell_seconds: dict[str, float] = field(default_factory=dict)


# ── Helpers ──────────────────────────────────────────────────────────────

def _percentile(values: list[float], pct: float) -> float:
    """Linear-interpolation percentile. Returns 0.0 for empty input."""
    if not values:
        return 0.0
    if len(values) == 1:
        return float(values[0])
    xs = sorted(values)
    k = (len(xs) - 1) * (pct / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(xs) - 1)
    if lo == hi:
        return float(xs[lo])
    return float(xs[lo] + (xs[hi] - xs[lo]) * (k - lo))


def _int(d: dict[str, Any] | None, key: str, default: int = 0) -> int:
    if not d or not isinstance(d, dict):
        return default
    val = d.get(key, default)
    try:
        return int(val or 0)
    except (TypeError, ValueError):
        return default


def _compute_phase_dwell(
    state: dict[str, Any],
) -> dict[str, float]:
    """Total dwell seconds per phase from ``system_state_log.recent_transitions``.

    Takes pairs of ``(transition_n, transition_n+1)``. The phase that
    transitioned *into* at ``transition_n`` dwells until ``transition_n+1``.
    Seconds are positive floats. If timestamps are unparseable, that
    pair is skipped.
    """
    orch = state.get("orchestration_state") or {}
    ssl = orch.get("system_state_log") or {}
    recent = ssl.get("recent_transitions") or []
    if not isinstance(recent, list) or len(recent) < 2:
        return {}

    # recent_transitions are ordered DESC by insert id — reverse to chronological.
    ordered = list(recent)
    # Only reverse if they look descending (first ``at`` > last ``at``).
    # Parse both timestamps to datetime before comparing to avoid
    # lexicographic ordering bugs when mixed offset formats are used
    # (e.g. ``+00:00`` vs ``Z``). Fall through to the existing behaviour
    # if either value is unparseable.
    try:
        first_at = ordered[0].get("at") if isinstance(ordered[0], dict) else None
        last_at = ordered[-1].get("at") if isinstance(ordered[-1], dict) else None
        if first_at and last_at:
            try:
                first_dt = datetime.fromisoformat(
                    str(first_at).replace("Z", "+00:00")
                )
                last_dt = datetime.fromisoformat(
                    str(last_at).replace("Z", "+00:00")
                )
                if first_dt > last_dt:
                    ordered.reverse()
            except (ValueError, TypeError):
                # Unparseable timestamps — fall back to string compare
                # rather than silently silently leaving order wrong.
                if str(first_at) > str(last_at):
                    ordered.reverse()
    except Exception:
        pass

    dwell: dict[str, float] = {}
    for i in range(len(ordered) - 1):
        cur = ordered[i]
        nxt = ordered[i + 1]
        if not (isinstance(cur, dict) and isinstance(nxt, dict)):
            continue
        to_phase = cur.get("to_phase") or cur.get("new_phase")
        t0 = cur.get("at") or cur.get("transitioned_at")
        t1 = nxt.get("at") or nxt.get("transitioned_at")
        if not (to_phase and t0 and t1):
            continue
        try:
            a = datetime.fromisoformat(str(t0).replace("Z", "+00:00"))
            b = datetime.fromisoformat(str(t1).replace("Z", "+00:00"))
            secs = (b - a).total_seconds()
        except Exception:
            continue
        if secs <= 0:
            continue
        key = str(to_phase).lower()
        dwell[key] = dwell.get(key, 0.0) + secs
    return dwell


def _subtract_dicts(
    a: dict[str, int] | None, b: dict[str, int] | None,
) -> dict[str, int]:
    """Return ``a - b`` for dicts of counts, keeping only positive deltas."""
    a = a or {}
    b = b or {}
    out: dict[str, int] = {}
    for k in a:
        diff = int(a.get(k, 0) or 0) - int(b.get(k, 0) or 0)
        if diff > 0:
            out[k] = diff
    return out


async def collect_benchmarks(
    response_metadata: list[dict[str, Any]],
    current_state: dict[str, Any],
    initial_state: dict[str, Any] | None = None,
    *,
    request_budget_capacity: int = 1000,
) -> BenchmarkSummary:
    """Assemble a :class:`BenchmarkSummary` from the inputs.

    ``response_metadata`` should be a list of per-turn dicts with keys:
    ``latency_ms``, ``token_count`` (total tokens for the turn),
    ``prompt_tokens``, ``completion_tokens`` (optional), ``compaction_tier``.

    ``current_state`` is a :meth:`HarnessServer._snapshot_full_state`
    result. ``initial_state`` is optional and enables delta-mode for
    memory/vault fields. When absent, the absolute totals from
    ``current_state`` are reported.

    ``request_budget_capacity`` defaults to 1000. The live limiter
    capacity varies by config; pass the actual value when known.

    Missing fields default to zero — this call never raises on
    partial / empty state.
    """
    summary = BenchmarkSummary()

    # ── Latency + tokens ──
    #
    # A meta represents either a real assistant turn (``role='assistant'``
    # or ``is_response=True``) or a synthetic compaction-event entry used
    # only to contribute to the compaction tier histogram. The two must
    # never be conflated — response_count in particular counts *real
    # turns only* so it tracks conversation volume, not compaction noise.
    latencies: list[float] = []
    prompt_total = 0
    completion_total = 0
    total_tokens = 0
    response_count = 0
    compaction_counts: dict[str, int] = {"none": 0, "soft": 0, "hard": 0}

    for meta in response_metadata:
        if not isinstance(meta, dict):
            continue

        # A meta is a real response if it's explicitly flagged
        # (``is_response=True``) or if ``role`` is ``assistant``.
        # Everything else (e.g. synthetic compaction-event entries) is
        # counted only for the compaction-tier histogram.
        is_response = bool(
            meta.get("is_response")
            or meta.get("role") == "assistant"
        )

        if is_response:
            response_count += 1
            lat = meta.get("latency_ms")
            if isinstance(lat, (int, float)) and lat >= 0:
                latencies.append(float(lat))

            pt = meta.get("prompt_tokens")
            ct = meta.get("completion_tokens")
            tt = meta.get("token_count") or meta.get("total_tokens")
            if isinstance(pt, int):
                prompt_total += pt
            if isinstance(ct, int):
                completion_total += ct
            if isinstance(tt, int):
                total_tokens += tt

        # compaction_tier is counted for every meta — both real turns
        # (where most are "none") and synthetic events (which always
        # carry "soft"/"hard"). This produces an accurate histogram of
        # what actually happened at the model layer.
        tier = meta.get("compaction_tier") or "none"
        tier_key = str(tier).lower()
        compaction_counts[tier_key] = compaction_counts.get(tier_key, 0) + 1

    summary.response_count = response_count
    summary.response_latency_p50_ms = round(_percentile(latencies, 50.0), 3)
    summary.response_latency_p95_ms = round(_percentile(latencies, 95.0), 3)
    summary.total_prompt_tokens = prompt_total
    summary.total_completion_tokens = completion_total
    summary.tokens_per_response_mean = round(
        (total_tokens / response_count) if response_count else 0.0, 2
    )
    summary.compaction_tier_counts = {
        k: int(v) for k, v in compaction_counts.items() if v or k == "none"
    }

    # ── Request budget ──
    orch = current_state.get("orchestration_state") or {}
    rl = orch.get("request_limiter") or {}
    by_class = rl.get("by_class") or {}
    if isinstance(by_class, dict):
        summary.requests_by_class = {
            str(k): int(v or 0) for k, v in by_class.items()
        }
    in_window = _int(rl, "in_window", 0)
    cap = max(request_budget_capacity, 1)
    used_fraction = min(in_window / cap, 1.0)
    summary.remaining_budget_fraction = round(max(1.0 - used_fraction, 0.0), 4)

    # ── Pipelines ──
    pi = orch.get("pipeline_instances") or {}
    if isinstance(pi.get("by_name"), dict):
        summary.pipeline_fires_by_name = {
            str(k): int(v or 0) for k, v in pi["by_name"].items()
        }
    by_state = pi.get("by_state") or {}
    if isinstance(by_state, dict):
        summary.pipeline_success_count = int(by_state.get("completed", 0) or 0)
        summary.pipeline_fail_count = int(
            (by_state.get("failed", 0) or 0) + (by_state.get("cancelled", 0) or 0)
        )

    # Trigger-type histogram: pull trigger names from trigger_state.last_fires.
    ts = orch.get("trigger_state") or {}
    last_fires = ts.get("last_fires") or []
    tt_counts: dict[str, int] = {}
    for row in last_fires:
        if not isinstance(row, dict):
            continue
        name = row.get("trigger_name") or ""
        # Infer the broad category from the trigger id prefix — mirrors
        # the naming convention used in core_pipelines.py.
        key = "other"
        lowered = str(name).lower()
        if lowered.startswith(("event_", "event:")) or "event" in lowered:
            key = "event"
        elif lowered.startswith("interval") or "interval" in lowered:
            key = "interval"
        elif "time_of_day" in lowered:
            key = "time_of_day"
        elif "session_end" in lowered:
            key = "session_end"
        elif "user_action" in lowered or "action" in lowered:
            key = "user_action"
        elif "sequence" in lowered:
            key = "sequence_complete"
        tt_counts[key] = tt_counts.get(key, 0) + 1
    summary.pipeline_fires_by_trigger_type = tt_counts

    # ── Notifications ──
    pro = current_state.get("proactive_state") or {}
    notifs = pro.get("notifications") or {}
    if isinstance(notifs.get("by_tier"), dict):
        summary.notifications_by_tier = {
            str(k): int(v or 0) for k, v in notifs["by_tier"].items()
        }
    if isinstance(notifs.get("by_reason"), dict):
        summary.notifications_by_reason = {
            str(k): int(v or 0) for k, v in notifs["by_reason"].items()
        }

    # ── Memory lifecycle ──
    mem = current_state.get("memory_lifecycle") or {}
    init_mem = (initial_state or {}).get("memory_lifecycle") or {}

    def _status_count(
        m: dict[str, Any], key: str, status: str,
    ) -> int:
        table = m.get(key) or {}
        bs = table.get("by_status") or {}
        if isinstance(bs, dict):
            return int(bs.get(status, 0) or 0)
        return 0

    current_total = _int(mem.get("memories"), "total", 0)
    initial_total = _int(init_mem.get("memories"), "total", 0)
    summary.memories_created = max(current_total - initial_total, 0)

    summary.memories_consolidated = _int(
        mem.get("memories"), "with_consolidated_into", 0
    )

    # Soft-deleted (dedup) count from status; operational soft-delete
    # status is "deleted" or "soft_deleted" depending on migration.
    dedup_current = _status_count(mem, "memories", "deleted") + _status_count(
        mem, "memories", "soft_deleted"
    )
    dedup_initial = _status_count(init_mem, "memories", "deleted") + _status_count(
        init_mem, "memories", "soft_deleted"
    )
    summary.memories_dedup_merged = max(dedup_current - dedup_initial, 0)

    ent_cur = _int(mem.get("entities"), "total", 0)
    ent_ini = _int(init_mem.get("entities"), "total", 0)
    summary.entities_created = max(ent_cur - ent_ini, 0)
    summary.entities_merged = _int(mem.get("memories"), "with_merged_from", 0)

    # ── Vault ──
    vault = current_state.get("vault_state") or {}
    counts = vault.get("counts") or {}
    summary.vault_notes_total = int(counts.get("total_notes", 0) or 0)
    dens = vault.get("wikilink_density") or {}
    summary.vault_wikilinks_total = int(dens.get("total_wikilinks", 0) or 0)
    summary.vault_entity_pages = int(
        (counts.get("entities_people", 0) or 0)
        + (counts.get("entities_places", 0) or 0)
        + (counts.get("entities_projects", 0) or 0)
    )
    summary.vault_moc_pages = int(counts.get("moc_pages", 0) or 0)
    wd = vault.get("working_docs") or []
    summary.vault_working_docs_active = len(
        [w for w in wd if isinstance(w, dict) and w.get("status") == "in_progress"]
    )

    # ── Insights ──
    ins = pro.get("insights") or {}
    if ins.get("persisted"):
        summary.insights_persisted = _int(ins, "total_if_persisted", 0)
    else:
        summary.insights_persisted = None

    # ── Phase dwell ──
    summary.phase_dwell_seconds = {
        k: round(v, 3) for k, v in _compute_phase_dwell(current_state).items()
    }

    return summary


# ── Serialization ────────────────────────────────────────────────────────

def benchmarks_to_json(bench: BenchmarkSummary) -> dict[str, Any]:
    """JSON-serialisable dict. Stable key set — AT4 publishes the schema."""
    return asdict(bench)


def benchmarks_to_csv_row(bench: BenchmarkSummary) -> dict[str, Any]:
    """Flat-dict representation for appending to a CSV.

    Dict-typed fields are collapsed with ``|`` into the column so each
    benchmark row stays one CSV line. Missing values become empty
    strings; numbers stay numeric.
    """

    def _flatten_counts(d: dict[str, int]) -> str:
        if not d:
            return ""
        return ";".join(f"{k}={v}" for k, v in sorted(d.items()))

    row: dict[str, Any] = {
        "response_latency_p50_ms": bench.response_latency_p50_ms,
        "response_latency_p95_ms": bench.response_latency_p95_ms,
        "response_count": bench.response_count,
        "total_prompt_tokens": bench.total_prompt_tokens,
        "total_completion_tokens": bench.total_completion_tokens,
        "tokens_per_response_mean": bench.tokens_per_response_mean,
        "requests_by_class": _flatten_counts(bench.requests_by_class),
        "remaining_budget_fraction": bench.remaining_budget_fraction,
        "compaction_tier_counts": _flatten_counts(bench.compaction_tier_counts),
        "pipeline_fires_by_name": _flatten_counts(bench.pipeline_fires_by_name),
        "pipeline_fires_by_trigger_type": _flatten_counts(
            bench.pipeline_fires_by_trigger_type
        ),
        "pipeline_success_count": bench.pipeline_success_count,
        "pipeline_fail_count": bench.pipeline_fail_count,
        "notifications_by_tier": _flatten_counts(bench.notifications_by_tier),
        "notifications_by_reason": _flatten_counts(bench.notifications_by_reason),
        "memories_created": bench.memories_created,
        "memories_consolidated": bench.memories_consolidated,
        "memories_dedup_merged": bench.memories_dedup_merged,
        "entities_created": bench.entities_created,
        "entities_merged": bench.entities_merged,
        "vault_notes_total": bench.vault_notes_total,
        "vault_wikilinks_total": bench.vault_wikilinks_total,
        "vault_entity_pages": bench.vault_entity_pages,
        "vault_moc_pages": bench.vault_moc_pages,
        "vault_working_docs_active": bench.vault_working_docs_active,
        "insights_persisted": (
            bench.insights_persisted if bench.insights_persisted is not None else ""
        ),
        "phase_dwell_seconds": _flatten_counts(
            {k: int(round(v)) for k, v in bench.phase_dwell_seconds.items()}
        ),
    }
    return row


# Column order for the central CSV. Callers appending must use the same
# order to keep rows aligned.
CSV_COLUMNS: tuple[str, ...] = (
    "response_latency_p50_ms",
    "response_latency_p95_ms",
    "response_count",
    "total_prompt_tokens",
    "total_completion_tokens",
    "tokens_per_response_mean",
    "requests_by_class",
    "remaining_budget_fraction",
    "compaction_tier_counts",
    "pipeline_fires_by_name",
    "pipeline_fires_by_trigger_type",
    "pipeline_success_count",
    "pipeline_fail_count",
    "notifications_by_tier",
    "notifications_by_reason",
    "memories_created",
    "memories_consolidated",
    "memories_dedup_merged",
    "entities_created",
    "entities_merged",
    "vault_notes_total",
    "vault_wikilinks_total",
    "vault_entity_pages",
    "vault_moc_pages",
    "vault_working_docs_active",
    "insights_persisted",
    "phase_dwell_seconds",
)
