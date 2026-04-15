"""Unit tests for the Slice 7.5b WorkingDocStore (spec §17.3)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from kora_v2.runtime.orchestration.working_doc import (
    WorkingDocStore,
    WorkingDocUpdate,
    parse_plan_items,
    slugify_goal,
)


@pytest.fixture
def store(tmp_path: Path) -> WorkingDocStore:
    return WorkingDocStore(tmp_path / "Inbox")


async def test_ensure_inbox_creates_directory(store: WorkingDocStore) -> None:
    store.ensure_inbox()
    assert store.inbox_root.is_dir()


async def test_create_is_idempotent_and_writes_frontmatter(
    store: WorkingDocStore,
) -> None:
    path1 = await store.create(
        instance_id="pipe-001",
        task_id="task-001",
        pipeline_name="test_pipeline",
        goal="do a thing",
        seed_plan_items=["step one", "step two"],
    )
    assert path1.exists()
    text = path1.read_text()
    assert text.startswith("---\n")
    assert "task_id: task-001" in text
    assert "pipeline: test_pipeline" in text
    assert "status: in_progress" in text  # default

    # Second call is idempotent — returns same path, does not rewrite.
    path2 = await store.create(
        instance_id="pipe-001",
        task_id="task-001",
        pipeline_name="test_pipeline",
        goal="do a thing",
    )
    assert path1 == path2


async def test_read_roundtrips_sections(store: WorkingDocStore) -> None:
    path = await store.create(
        instance_id="pipe-002",
        task_id="task-002",
        pipeline_name="test_pipeline",
        goal="read back",
        seed_plan_items=["a", "b"],
    )
    handle = await store.read(path)
    assert handle is not None
    assert handle.goal == "read back"
    items = handle.parse_current_plan()
    assert [i.text for i in items] == ["a", "b"]
    assert all(i.marker == " " for i in items)


async def test_parse_plan_items_handles_all_markers() -> None:
    text = (
        "- [ ] open\n"
        "- [x] done\n"
        "- [skip] skipped\n"
        "- [cancel] cancelled — **done**\n"
    )
    items = parse_plan_items(text)
    assert [i.marker for i in items] == [" ", "x", "skip", "cancel"]
    assert items[3].text == "cancelled"  # annotation stripped


def test_slugify_goal_is_filesystem_safe() -> None:
    assert slugify_goal("Plan a meal for Thursday!") == "plan-a-meal-for-thursday"
    assert slugify_goal("") == "task"
    assert slugify_goal("!!!") == "task"


async def test_per_instance_locks_serialise_writes(
    store: WorkingDocStore,
) -> None:
    path = await store.create(
        instance_id="pipe-003",
        task_id="task-003",
        pipeline_name="concurrent",
        goal="race condition",
    )

    async def append(marker: str) -> None:
        await store.apply_update(
            path=path,
            instance_id="pipe-003",
            update=WorkingDocUpdate(
                section_patches={"Summary": f"tick {marker}"},
            ),
        )

    # Concurrent applies; last-writer-wins is fine, but nothing should
    # raise and the file must remain parseable.
    await asyncio.gather(append("a"), append("b"), append("c"))
    handle = await store.read(path)
    assert handle is not None
    assert handle.sections.get("Summary", "").startswith("tick ")
