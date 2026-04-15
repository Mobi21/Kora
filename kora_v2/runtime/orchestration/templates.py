"""Pipeline templates — stub for slice 7.5a.

Slice 7.5c adds a catalogue of pre-built pipeline declarations
(``DailyPlanning``, ``MorningCheckIn``, …). This stub exists so
callers can import the module today. The single :func:`demo_tick`
pipeline defined below is used by the slice 7.5a integration tests
to exercise the dispatch loop end-to-end.
"""

from __future__ import annotations

from kora_v2.runtime.orchestration.pipeline import (
    FailurePolicy,
    InterruptionPolicy,
    Pipeline,
    PipelineStage,
)


def demo_tick_pipeline() -> Pipeline:
    """A tiny single-stage pipeline used in integration tests."""
    return Pipeline(
        name="demo_tick",
        description="Exercise the dispatcher loop with a single background tick.",
        stages=[
            PipelineStage(
                name="tick",
                task_preset="bounded_background",
                goal_template="Emit a single heartbeat for {{instance_id}}",
            )
        ],
        triggers=[],
        interruption_policy=InterruptionPolicy.RUN_TO_COMPLETION,
        failure_policy=FailurePolicy.FAIL_PIPELINE,
    )
