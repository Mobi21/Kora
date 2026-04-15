"""Pipeline + PipelineStage unit tests."""

from __future__ import annotations

import pytest

from kora_v2.runtime.orchestration.pipeline import Pipeline, PipelineStage


def _stage(name: str, depends_on: list[str] | None = None) -> PipelineStage:
    return PipelineStage(
        name=name,
        task_preset="bounded_background",
        goal_template=f"run {name}",
        depends_on=depends_on or [],
    )


def test_minimal_pipeline_validates() -> None:
    p = Pipeline(
        name="p1",
        description="one",
        stages=[_stage("only")],
    )
    p.validate()  # should not raise


def test_empty_pipeline_rejected() -> None:
    p = Pipeline(name="empty", description="nothing", stages=[])
    with pytest.raises(ValueError, match="no stages"):
        p.validate()


def test_duplicate_stage_names_rejected() -> None:
    p = Pipeline(
        name="dup",
        description="",
        stages=[_stage("a"), _stage("a")],
    )
    with pytest.raises(ValueError, match="duplicate stage names"):
        p.validate()


def test_unknown_dependency_rejected() -> None:
    p = Pipeline(
        name="unknown",
        description="",
        stages=[_stage("a", depends_on=["missing"])],
    )
    with pytest.raises(ValueError, match="unknown stage"):
        p.validate()


def test_cycle_detection() -> None:
    p = Pipeline(
        name="cycle",
        description="",
        stages=[
            _stage("a", depends_on=["b"]),
            _stage("b", depends_on=["a"]),
        ],
    )
    with pytest.raises(ValueError, match="cycle"):
        p.validate()


def test_linear_dag_is_valid() -> None:
    p = Pipeline(
        name="linear",
        description="",
        stages=[
            _stage("a"),
            _stage("b", depends_on=["a"]),
            _stage("c", depends_on=["b"]),
        ],
    )
    p.validate()
    assert p.stage("b").depends_on == ["a"]


def test_diamond_dag_is_valid() -> None:
    p = Pipeline(
        name="diamond",
        description="",
        stages=[
            _stage("top"),
            _stage("left", depends_on=["top"]),
            _stage("right", depends_on=["top"]),
            _stage("bottom", depends_on=["left", "right"]),
        ],
    )
    p.validate()
