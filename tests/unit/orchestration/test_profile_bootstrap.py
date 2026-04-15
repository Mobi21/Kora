"""Unit tests for the User Model profile bootstrapper (spec §16.3)."""

from __future__ import annotations

from pathlib import Path

import yaml

from kora_v2.runtime.orchestration.profile_bootstrap import (
    DEFAULT_PROFILE_FRONTMATTER,
    ensure_profile_defaults,
)


def _read_frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---"), "profile.md must begin with a YAML frontmatter fence"
    closing = text.find("\n---", 3)
    assert closing > 0, "profile.md must close its frontmatter fence"
    yaml_block = text[3:closing].strip()
    return yaml.safe_load(yaml_block) or {}


def test_creates_profile_md_with_all_defaults_when_missing(tmp_path: Path) -> None:
    """First run: file does not exist → write the full §16.3 default block."""
    result = ensure_profile_defaults(tmp_path)
    assert result.created is True
    assert result.path.exists()
    assert sorted(result.fields_added) == sorted(DEFAULT_PROFILE_FRONTMATTER.keys())

    fm = _read_frontmatter(result.path)
    for key, expected in DEFAULT_PROFILE_FRONTMATTER.items():
        assert fm[key] == expected, f"default for {key} not written"


def test_idempotent_when_file_already_complete(tmp_path: Path) -> None:
    """Second run on an unchanged file: no fields added, file untouched."""
    first = ensure_profile_defaults(tmp_path)
    mtime_before = first.path.stat().st_mtime_ns
    second = ensure_profile_defaults(tmp_path)
    assert second.created is False
    assert second.fields_added == []
    # The file should not be rewritten when nothing is missing.
    assert first.path.stat().st_mtime_ns == mtime_before


def test_preserves_user_set_values(tmp_path: Path) -> None:
    """User-authored frontmatter values must never be overwritten."""
    profile_path = tmp_path / "User Model" / "profile.md"
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    user_text = (
        "---\n"
        "wake_time: \"05:30\"\n"
        "timezone: \"Europe/Berlin\"\n"
        "name: Mobi\n"
        "---\n"
        "\n"
        "# my custom notes\n"
    )
    profile_path.write_text(user_text, encoding="utf-8")

    result = ensure_profile_defaults(tmp_path)
    assert result.created is False
    # Should fill in everything *except* wake_time / timezone, and must
    # leave the unrelated `name` field alone.
    fm = _read_frontmatter(profile_path)
    assert fm["wake_time"] == "05:30"
    assert fm["timezone"] == "Europe/Berlin"
    assert fm["name"] == "Mobi"
    # The §16.3 defaults the user did NOT set should now be present.
    assert "dnd_start" in fm
    assert "hyperfocus_suppression" in fm
    assert fm["hyperfocus_suppression"] is True
    # And the user's body should not have been clobbered.
    body = profile_path.read_text(encoding="utf-8")
    assert "my custom notes" in body


def test_handles_file_without_frontmatter(tmp_path: Path) -> None:
    """A markdown file with no frontmatter still gets defaults injected."""
    profile_path = tmp_path / "User Model" / "profile.md"
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text("# Just a heading\n\nFreeform text.\n", encoding="utf-8")

    result = ensure_profile_defaults(tmp_path)
    assert result.created is False
    assert "wake_time" in result.fields_added
    fm = _read_frontmatter(profile_path)
    for key in DEFAULT_PROFILE_FRONTMATTER:
        assert key in fm
