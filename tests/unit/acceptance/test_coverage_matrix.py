"""Coverage-matrix sanity tests for the acceptance scenario.

These tests guard ``tests/acceptance/scenario/week_plan.py`` against
accidental drift — duplicate ids, stale ``start_autonomous`` /
``BackgroundWorker`` references, phases pointing at undefined items,
and Phase 7.5 / Phase 8 items falling off the matrix.
"""

from __future__ import annotations

import re

from tests.acceptance.scenario.week_plan import (
    COVERAGE_ITEMS,
    WEEK_PLAN,
    CoverageItem,
    CoverageStatus,
)

# Strings that must no longer appear in any description — their underlying
# surfaces were retired in Phase 7.5 and leaving the wording around makes
# the matrix lie about what the test actually exercises.
_STALE_REFERENCE_PATTERNS = (
    re.compile(r"\bstart_autonomous\b"),
    re.compile(r"\bBackgroundWorker\b"),
    re.compile(r"\bautonomous_plans\b"),
    re.compile(r"\bautonomous_checkpoints\b"),
)

_ALLOWED_CATEGORIES = frozenset(
    {
        "core",
        "orchestration",
        "memory_steward",
        "vault_organizer",
        "context_engine",
        "proactive_agent",
        "life_management",
        "capability_pack",
    }
)


def test_coverage_items_unique_ids() -> None:
    """Dict keys are unique by construction, but guard against future lists."""
    ids = list(COVERAGE_ITEMS.keys())
    assert len(ids) == len(set(ids)), f"duplicate ids in COVERAGE_ITEMS: {ids}"


def test_active_items_have_descriptions() -> None:
    for item_id, item in COVERAGE_ITEMS.items():
        assert isinstance(item, CoverageItem), item_id
        desc = item.description.strip()
        assert desc, f"item {item_id} has empty description"
        assert len(desc) > 20, (
            f"item {item_id} description too short ({len(desc)} chars): {desc!r}"
        )


def test_phase_coverage_items_exist() -> None:
    """Every phase's ``coverage_items`` references a defined matrix id."""
    for day_name, day in WEEK_PLAN.items():
        for phase in day["phases"]:
            for item_id in phase.get("coverage_items", []):
                assert item_id in COVERAGE_ITEMS, (
                    f"{day_name}:{phase['name']} references undefined "
                    f"coverage item {item_id}"
                )


def test_no_stale_references_in_descriptions() -> None:
    """No item description should mention retired Phase 7.5 surfaces."""
    for item_id, item in COVERAGE_ITEMS.items():
        for pat in _STALE_REFERENCE_PATTERNS:
            assert not pat.search(item.description), (
                f"item {item_id} description still references "
                f"{pat.pattern!r}: {item.description!r}"
            )


def test_categories_present() -> None:
    """Every item has a category drawn from the agreed-upon set."""
    for item_id, item in COVERAGE_ITEMS.items():
        assert item.category, f"item {item_id} has no category"
        assert item.category in _ALLOWED_CATEGORIES, (
            f"item {item_id} has unknown category {item.category!r}"
        )


def test_phase_8_items_present() -> None:
    """Items 47-67 (Phase 8) are all in the matrix and ACTIVE."""
    for item_id in range(47, 68):
        assert item_id in COVERAGE_ITEMS, f"Phase 8 item {item_id} missing"
        item = COVERAGE_ITEMS[item_id]
        assert item.status == CoverageStatus.ACTIVE, (
            f"Phase 8 item {item_id} should be ACTIVE, got {item.status}"
        )


def test_phase_7_5_items_present() -> None:
    """Items 24-46 (Phase 7.5) are all in the matrix and ACTIVE."""
    for item_id in range(24, 47):
        assert item_id in COVERAGE_ITEMS, f"Phase 7.5 item {item_id} missing"
        item = COVERAGE_ITEMS[item_id]
        assert item.status == CoverageStatus.ACTIVE, (
            f"Phase 7.5 item {item_id} should be ACTIVE, got {item.status}"
        )


def test_item_8_and_12_un_deferred() -> None:
    """Items 8 and 12 were un-deferred in Phase 7.5 / AT1."""
    for item_id in (8, 12):
        item = COVERAGE_ITEMS[item_id]
        assert item.status == CoverageStatus.ACTIVE, (
            f"item {item_id} must be ACTIVE after Phase 7.5 un-deferral, "
            f"got {item.status}"
        )


def test_capability_pack_items_renumbered() -> None:
    """The legacy capability-pack items live at 100+ to avoid collision
    with Phase 7.5 items 24-26."""
    assert 100 in COVERAGE_ITEMS
    assert 101 in COVERAGE_ITEMS
    assert 102 in COVERAGE_ITEMS
    for item_id in (100, 101, 102):
        assert COVERAGE_ITEMS[item_id].category == "capability_pack"
