"""Unit tests for the Slice 7.5b TemplateRegistry (spec §10.2)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from kora_v2.runtime.orchestration.templates import (
    DEFAULT_TEMPLATES,
    TemplatePriority,
    TemplateRegistry,
)


@pytest.fixture
def registry(tmp_path: Path) -> TemplateRegistry:
    root = tmp_path / "templates"
    return TemplateRegistry(root)


def test_ensure_defaults_writes_yaml(registry: TemplateRegistry) -> None:
    assert not registry.path.exists()
    registry.ensure_defaults()
    assert registry.path.exists()
    raw = yaml.safe_load(registry.path.read_text())
    assert "rate_limit_paused" in raw
    assert "task_completed" in raw
    assert "pipeline_completed" in raw
    assert "pipeline_failed" in raw


def test_ensure_defaults_is_idempotent(registry: TemplateRegistry) -> None:
    registry.ensure_defaults()
    original = registry.path.read_text()
    registry.ensure_defaults()
    assert registry.path.read_text() == original


def test_get_returns_loaded_template(registry: TemplateRegistry) -> None:
    registry.ensure_defaults()
    tpl = registry.get("rate_limit_paused")
    assert tpl is not None
    assert tpl.priority is TemplatePriority.HIGH
    assert "{minutes}" in tpl.text


def test_render_substitutes_kwargs(registry: TemplateRegistry) -> None:
    registry.ensure_defaults()
    rendered = registry.render("rate_limit_paused", minutes=12)
    assert "12" in rendered.text
    assert rendered.priority is TemplatePriority.HIGH
    assert rendered.template_id == "rate_limit_paused"


def test_render_missing_var_falls_back_to_placeholder(
    registry: TemplateRegistry,
) -> None:
    registry.ensure_defaults()
    # Call render without required `goal` / `summary` variables — should
    # leave them as literal placeholders, not raise.
    rendered = registry.render("task_completed")
    assert "{goal}" in rendered.text
    assert "{summary}" in rendered.text


def test_render_unknown_template_raises(registry: TemplateRegistry) -> None:
    with pytest.raises(KeyError):
        registry.render("not_a_real_template")


def test_render_priority_override(registry: TemplateRegistry) -> None:
    registry.ensure_defaults()
    rendered = registry.render(
        "task_progress",
        priority_override=TemplatePriority.HIGH,
        goal="X",
        marker="step",
        percent=50,
    )
    assert rendered.priority is TemplatePriority.HIGH


def test_pipeline_terminal_templates_render_without_llm(
    registry: TemplateRegistry,
) -> None:
    registry.ensure_defaults()
    completed = registry.render(
        "pipeline_completed",
        pipeline_name="proactive_research",
        goal="local-first reminder tools",
    )
    failed = registry.render(
        "pipeline_failed",
        pipeline_name="proactive_research",
        reason="timeout",
        goal="local-first reminder tools",
    )

    assert completed.text == (
        "proactive_research finished. local-first reminder tools"
    )
    assert failed.text == (
        "proactive_research ran into trouble: timeout. "
        "local-first reminder tools"
    )


def test_ids_returns_sorted_default_ids(registry: TemplateRegistry) -> None:
    registry.ensure_defaults()
    ids = registry.ids()
    assert ids == sorted(ids)
    for tid in DEFAULT_TEMPLATES:
        assert tid in ids


def test_reload_if_changed_picks_up_edits(registry: TemplateRegistry) -> None:
    registry.ensure_defaults()
    registry.reload_if_changed()

    # Rewrite the YAML with a single user-edited entry.
    raw = yaml.safe_load(registry.path.read_text()) or {}
    raw["task_started"] = {
        "text": "Kicking off {goal} — will update soon.",
        "priority": "medium",
        "bypass_dnd": False,
    }
    registry.path.write_text(yaml.safe_dump(raw, sort_keys=False))

    # Bump mtime forward so the reload fires.
    import os
    import time as _time

    future = _time.time() + 10
    os.utime(registry.path, (future, future))

    assert registry.reload_if_changed() is True
    rendered = registry.render("task_started", goal="a thing")
    assert rendered.text.startswith("Kicking off a thing")


def test_reset_to_defaults_reverts_edits(registry: TemplateRegistry) -> None:
    registry.ensure_defaults()
    registry.path.write_text(
        yaml.safe_dump(
            {"task_started": {"text": "REPLACED", "priority": "low"}},
            sort_keys=False,
        )
    )
    registry.reset_to_defaults()
    tpl = registry.get("task_started")
    assert tpl is not None
    assert "REPLACED" not in tpl.text


def test_missing_default_entry_is_patched_on_load(
    registry: TemplateRegistry,
) -> None:
    registry.path.parent.mkdir(parents=True, exist_ok=True)
    registry.path.write_text(
        yaml.safe_dump(
            {"custom_only": {"text": "hi", "priority": "low"}},
            sort_keys=False,
        )
    )
    registry.reload_if_changed()
    # User removed all defaults — registry backfills them so the
    # orchestration layer always has its critical templates.
    assert registry.get("rate_limit_paused") is not None
    assert registry.get("custom_only") is not None
