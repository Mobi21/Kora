"""User Model profile bootstrapper — spec §16.3.

Phase 7.5 ensures the User Model profile carries the fields the
orchestration layer needs to compute time-based phases, render
notifications inside DND windows, and schedule the weekly review
pipeline. Per spec §16.3, the canonical home is
``_KoraMemory/User Model/profile.md`` — a markdown file with YAML
frontmatter the user can edit by hand.

This module owns the *one-time write of the defaults*: when the engine
starts and the profile file does not exist (or is missing the
orchestration keys), we write the §16.3 default block so the rest of
Kora always has a consistent profile to read from. Existing user values
are **never** overwritten — the bootstrapper only fills in keys the
user has not set yet.

This is intentionally a thin module: it owns one concern (file +
frontmatter merge) and is exercised by a unit test that constructs a
temp memory root and asserts the resulting markdown matches the spec.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog
import yaml

log = structlog.get_logger(__name__)


# ── Defaults shipped with Phase 7.5 (spec §16.3) ─────────────────────────

DEFAULT_PROFILE_FRONTMATTER: dict[str, Any] = {
    "wake_time": "07:00",
    "sleep_start": "23:00",
    "sleep_end": "07:00",
    "dnd_start": "22:00",
    "dnd_end": "08:00",
    "timezone": "America/Los_Angeles",
    "weekly_review_time": "Sunday 18:00",
    "hyperfocus_suppression": True,
}

# Keys the orchestration layer reads from the profile. Used by the
# bootstrapper to detect which §16.3 fields are missing without
# touching unrelated user-authored frontmatter (e.g. name, pronouns).
_ORCHESTRATION_KEYS: frozenset[str] = frozenset(DEFAULT_PROFILE_FRONTMATTER.keys())

_PROFILE_RELPATH = Path("User Model") / "profile.md"

# Boilerplate body written when the file does not exist yet. Kept short
# so the user is encouraged to edit it; the orchestration-relevant data
# is in the frontmatter regardless of body content.
_DEFAULT_BODY = (
    "# Kora User Profile\n"
    "\n"
    "This file holds the per-user anchors Kora uses to plan its day.\n"
    "Edit the frontmatter at the top to update wake/sleep, do-not-disturb,\n"
    "or timezone. Defaults ship with Phase 7.5 and never overwrite values\n"
    "you have already set.\n"
)


@dataclass(frozen=True)
class BootstrapResult:
    """Outcome of a profile bootstrap call."""

    path: Path
    created: bool          # True if the file did not exist before
    fields_added: list[str]


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Parse a markdown file with YAML frontmatter.

    Returns ``(frontmatter_dict, body_str)``. If the file has no
    frontmatter block, ``frontmatter_dict`` is empty and ``body_str``
    is the whole text.
    """
    if not text.startswith("---"):
        return {}, text
    # Find the closing fence — second `---` on its own line.
    lines = text.splitlines()
    end_idx: int | None = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        return {}, text
    yaml_block = "\n".join(lines[1:end_idx])
    body = "\n".join(lines[end_idx + 1 :]).lstrip("\n")
    try:
        parsed = yaml.safe_load(yaml_block) or {}
    except yaml.YAMLError:
        log.warning("profile_frontmatter_parse_failed")
        return {}, text
    if not isinstance(parsed, dict):
        return {}, text
    return parsed, body


def _render(frontmatter: dict[str, Any], body: str) -> str:
    """Serialize a profile.md file from frontmatter + body."""
    yaml_block = yaml.safe_dump(
        frontmatter,
        sort_keys=False,
        default_flow_style=False,
    ).rstrip()
    parts = ["---", yaml_block, "---", "", body.rstrip(), ""]
    return "\n".join(parts)


def _atomic_write(path: Path, content: str) -> None:
    """Write *content* to *path* via a temp+rename to avoid torn writes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def ensure_profile_defaults(memory_root: Path) -> BootstrapResult:
    """Ensure ``_KoraMemory/User Model/profile.md`` carries §16.3 defaults.

    Behaviour:

    * If the file does not exist, write a fresh one with the full default
      frontmatter block and a short prose body.
    * If the file exists, parse its frontmatter and add only the
      orchestration keys that are missing. Existing values — even ones
      the user wrote that disagree with the defaults — are preserved.
    * If the file exists with all orchestration keys already set, this
      is a no-op (the file is not rewritten so mtime stays clean).

    Returns a :class:`BootstrapResult` describing what (if anything)
    was changed so callers and tests can audit the bootstrap.
    """
    profile_path = memory_root / _PROFILE_RELPATH

    if not profile_path.exists():
        body = _DEFAULT_BODY
        rendered = _render(dict(DEFAULT_PROFILE_FRONTMATTER), body)
        _atomic_write(profile_path, rendered)
        log.info(
            "profile_bootstrap_created",
            path=str(profile_path),
            keys=sorted(DEFAULT_PROFILE_FRONTMATTER.keys()),
        )
        return BootstrapResult(
            path=profile_path,
            created=True,
            fields_added=sorted(DEFAULT_PROFILE_FRONTMATTER.keys()),
        )

    raw = profile_path.read_text(encoding="utf-8")
    frontmatter, body = _split_frontmatter(raw)
    missing = sorted(_ORCHESTRATION_KEYS - set(frontmatter.keys()))
    if not missing:
        return BootstrapResult(
            path=profile_path,
            created=False,
            fields_added=[],
        )

    for key in missing:
        frontmatter[key] = DEFAULT_PROFILE_FRONTMATTER[key]
    rendered = _render(frontmatter, body or _DEFAULT_BODY)
    _atomic_write(profile_path, rendered)
    log.info(
        "profile_bootstrap_filled_missing",
        path=str(profile_path),
        keys=missing,
    )
    return BootstrapResult(
        path=profile_path,
        created=False,
        fields_added=missing,
    )


__all__ = [
    "DEFAULT_PROFILE_FRONTMATTER",
    "BootstrapResult",
    "ensure_profile_defaults",
]
