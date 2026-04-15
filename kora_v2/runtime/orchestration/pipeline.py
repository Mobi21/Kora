"""Pipeline, PipelineStage, and PipelineInstance primitives — spec §3.5/§3.6.

A :class:`Pipeline` is the *declaration* of a multi-stage workflow: what
stages exist, what task preset each stage uses, and how failures or
interruptions are handled. The engine takes a :class:`Pipeline` plus a
trigger firing and produces a :class:`PipelineInstance`, which is the
*run-time record* of that declaration in flight.

A single Pipeline can be instantiated many times (one per trigger firing
or ad-hoc dispatch). Instances are persisted to
``pipeline_instances``; the in-memory :class:`Pipeline` object lives
in the :mod:`registry`.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from kora_v2.runtime.orchestration.worker_task import WorkerTaskPreset

if TYPE_CHECKING:
    from kora_v2.runtime.orchestration.triggers import Trigger


class InterruptionPolicy(StrEnum):
    """What to do when the system phase changes mid-pipeline.

    * ``PAUSE_ON_CONVERSATION`` — long-running pipelines freeze the moment
      the user returns and resume when the system returns to idle.
    * ``RUN_TO_COMPLETION`` — bounded background pipelines keep going even
      if a conversation starts.
    * ``ABORT_IMMEDIATELY`` — lowest-priority jobs die the moment the
      system leaves their allowed phase.
    """

    PAUSE_ON_CONVERSATION = "pause_on_conversation"
    RUN_TO_COMPLETION = "run_to_completion"
    ABORT_IMMEDIATELY = "abort_immediately"


class FailurePolicy(StrEnum):
    """What to do when a worker task inside a pipeline fails."""

    FAIL_PIPELINE = "fail_pipeline"
    CONTINUE_NEXT_STAGE = "continue_next_stage"
    RETRY_STAGE = "retry_stage"


class PipelineInstanceState(StrEnum):
    """Lifecycle states for a :class:`PipelineInstance` row."""

    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class PipelineStage:
    """A single stage of a :class:`Pipeline`.

    Stages are ordered (the dispatcher preserves declaration order when
    computing the ready set) and each stage produces exactly one worker
    task at dispatch time. ``depends_on`` references other stage names
    in the same pipeline and forms the stage-level DAG.
    """

    name: str
    task_preset: WorkerTaskPreset
    goal_template: str
    depends_on: list[str] = field(default_factory=list)
    tool_scope: list[str] = field(default_factory=list)
    system_prompt_ref: str = ""
    retry_count: int = 0
    required_phase: str | None = None


@dataclass
class Pipeline:
    """Declarative definition of a pipeline — stored in the registry.

    The engine uses :meth:`validate` at registration time to catch
    obvious shape errors (duplicate stage names, unknown dependencies,
    cycles). Runtime semantics — what each stage actually *does* — live
    in the associated step function attached to each :class:`WorkerTask`
    when it is dispatched.
    """

    name: str
    description: str
    stages: list[PipelineStage]
    triggers: list[Trigger] = field(default_factory=list)
    interruption_policy: InterruptionPolicy = InterruptionPolicy.PAUSE_ON_CONVERSATION
    failure_policy: FailurePolicy = FailurePolicy.FAIL_PIPELINE
    intent_duration: str = "indefinite"
    max_concurrent_instances: int = 1

    def validate(self) -> None:
        """Raise ``ValueError`` on a malformed pipeline definition.

        Checks:
            * at least one stage
            * unique stage names
            * every ``depends_on`` references a known stage
            * no cycles in the stage DAG
        """
        if not self.stages:
            raise ValueError(f"Pipeline {self.name!r} has no stages")

        names = [stage.name for stage in self.stages]
        if len(names) != len(set(names)):
            dupes = sorted({n for n in names if names.count(n) > 1})
            raise ValueError(
                f"Pipeline {self.name!r} has duplicate stage names: {dupes}"
            )

        name_set = set(names)
        for stage in self.stages:
            for dep in stage.depends_on:
                if dep not in name_set:
                    raise ValueError(
                        f"Pipeline {self.name!r} stage {stage.name!r} "
                        f"depends on unknown stage {dep!r}"
                    )

        _assert_acyclic(self.stages)

    def stage(self, name: str) -> PipelineStage:
        for stage in self.stages:
            if stage.name == name:
                return stage
        raise KeyError(f"Pipeline {self.name!r} has no stage {name!r}")


def _assert_acyclic(stages: Iterable[PipelineStage]) -> None:
    """Tarjan-style cycle detection over stage dependency edges."""
    graph: dict[str, list[str]] = {s.name: list(s.depends_on) for s in stages}
    WHITE, GREY, BLACK = 0, 1, 2
    color: dict[str, int] = dict.fromkeys(graph, WHITE)

    def visit(node: str, stack: list[str]) -> None:
        color[node] = GREY
        stack.append(node)
        for neighbour in graph.get(node, ()):
            if color[neighbour] == GREY:
                cycle = stack[stack.index(neighbour):] + [neighbour]
                raise ValueError(f"Pipeline stage cycle: {' -> '.join(cycle)}")
            if color[neighbour] == WHITE:
                visit(neighbour, stack)
        stack.pop()
        color[node] = BLACK

    for node in graph:
        if color[node] == WHITE:
            visit(node, [])


@dataclass
class PipelineInstance:
    """A running (or historical) instance of a :class:`Pipeline`.

    Persisted to the ``pipeline_instances`` table. The dispatcher uses
    the ``state`` field to decide whether to tick the instance on the
    next dispatch pass.
    """

    id: str
    pipeline_name: str
    working_doc_path: str
    goal: str
    state: PipelineInstanceState = PipelineInstanceState.PENDING
    parent_session_id: str | None = None
    parent_task_id: str | None = None
    intent_duration: str = "indefinite"
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    completion_reason: str | None = None

    def touch(self) -> None:
        self.updated_at = datetime.now(UTC)
