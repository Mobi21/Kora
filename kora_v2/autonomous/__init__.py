"""Kora V2 — Autonomous execution subsystem.

Phase 7.5c: the standalone autonomous loop was retired in favour of
the orchestration layer. This subpackage is now a library of the
preserved autonomous primitives — state machine, topic overlap
detection, and the 12-node LangGraph — consumed by
:mod:`kora_v2.autonomous.pipeline_factory`, which builds the
``user_autonomous_task`` / ``user_routine_task`` orchestration
pipelines. There is no longer a separate autonomous dispatcher: the
orchestration engine is the single scheduler and
``_autonomous_step_fn`` is the per-tick driver.

Budget enforcement moved to
:mod:`kora_v2.runtime.orchestration.autonomous_budget` (spec §17.7c),
and checkpoint persistence is now owned by
:class:`kora_v2.runtime.orchestration.checkpointing.CheckpointStore` —
the legacy ``budget.py`` and ``checkpoint.py`` modules were deleted as
part of the cutover.
"""
