"""Tests for workspace provenance injection (Task 6 — Phase 9 Tooling)."""
from __future__ import annotations

import copy

from kora_v2.capabilities.workspace.config import WorkspaceConfig
from kora_v2.capabilities.workspace.provenance import inject_calendar_create_provenance


def _cfg(**overrides) -> WorkspaceConfig:
    return WorkspaceConfig(**overrides)


# ── 1. Marker is appended to existing description ────────────────────────────

def test_provenance_appends_marker_to_description() -> None:
    cfg = _cfg()
    args = {"summary": "Dentist", "description": "Annual checkup"}
    result = inject_calendar_create_provenance(args, cfg)
    assert result["description"] == f"Annual checkup\n\n{cfg.provenance_marker}"


# ── 2. extendedProperties.private key is set ─────────────────────────────────

def test_provenance_sets_extended_properties_private() -> None:
    cfg = _cfg()
    args = {"summary": "Team lunch"}
    result = inject_calendar_create_provenance(args, cfg)
    ext = result.get("extendedProperties", {})
    private = ext.get("private", {})
    assert private.get(cfg.provenance_metadata_key) == cfg.provenance_metadata_value


# ── 3. Input dict is not mutated ──────────────────────────────────────────────

def test_provenance_does_not_mutate_input() -> None:
    cfg = _cfg()
    args = {
        "summary": "Budget review",
        "description": "Q3 review",
        "extendedProperties": {"private": {"existing_key": "existing_value"}},
    }
    original = copy.deepcopy(args)
    inject_calendar_create_provenance(args, cfg)
    assert args == original, "Input dict was mutated"


# ── 4. Missing description is created ────────────────────────────────────────

def test_provenance_creates_description_when_absent() -> None:
    cfg = _cfg()
    args = {"summary": "New event"}
    result = inject_calendar_create_provenance(args, cfg)
    assert "description" in result
    assert result["description"] == cfg.provenance_marker


# ── 5. Existing extendedProperties is merged, not replaced ───────────────────

def test_provenance_merges_extended_properties() -> None:
    cfg = _cfg()
    args = {
        "summary": "Merge test",
        "extendedProperties": {
            "private": {"existing_key": "keep_me"},
            "shared": {"other_key": "keep_me_too"},
        },
    }
    result = inject_calendar_create_provenance(args, cfg)
    private = result["extendedProperties"]["private"]
    # Original key preserved
    assert private.get("existing_key") == "keep_me"
    # Provenance key added
    assert private.get(cfg.provenance_metadata_key) == cfg.provenance_metadata_value
    # shared section preserved
    assert result["extendedProperties"]["shared"]["other_key"] == "keep_me_too"


# ── 6. Respects custom config values ─────────────────────────────────────────

def test_provenance_uses_config_values() -> None:
    cfg = _cfg(
        provenance_marker="[Kora Custom Marker]",
        provenance_metadata_key="custom_key",
        provenance_metadata_value="custom_value",
    )
    args = {"summary": "Custom config test"}
    result = inject_calendar_create_provenance(args, cfg)
    assert result["description"] == "[Kora Custom Marker]"
    private = result["extendedProperties"]["private"]
    assert private["custom_key"] == "custom_value"
