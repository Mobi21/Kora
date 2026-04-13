"""Typed ADHD profile + YAML loader.

Single source of truth for all runtime-deterministic ADHD settings. The
profile lives at ``_KoraMemory/User Model/adhd_profile/profile.yaml``
alongside any qualitative prose notes the memory worker writes as
``{note_id}.md`` files under the same domain. The two don't conflict —
different filename patterns, different consumers.
"""

from __future__ import annotations

from datetime import time
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator


class MedicationWindow(BaseModel):
    """A single time window in which a scheduled dose is expected.

    ``start``/``end`` are local (``Settings.user_tz``) times. The
    ``ContextEngine`` combines them with today's date and converts to
    UTC when matching against ``medication_log`` rows.
    """

    start: time
    end: time
    label: str | None = None

    @field_validator("start", "end", mode="before")
    @classmethod
    def _coerce_time(cls, value: Any) -> Any:
        if isinstance(value, str):
            return time.fromisoformat(value)
        return value


class MedicationScheduleEntry(BaseModel):
    name: str
    dose: str | None = None
    windows: list[MedicationWindow] = Field(default_factory=list)


class ADHDProfile(BaseModel):
    """Structured ADHD profile read from ``profile.yaml``.

    New users get a default profile with empty lists — the ADHD module
    degrades gracefully (no medication signals, no time-of-day peaks).
    """

    version: int = 1
    time_correction_factor: float = 1.5
    check_in_interval_minutes: int = 120
    transition_buffer_minutes: int = 15
    peak_windows: list[tuple[int, int]] = Field(default_factory=list)
    crash_periods: list[tuple[int, int]] = Field(default_factory=list)
    medication_schedule: list[MedicationScheduleEntry] = Field(default_factory=list)
    coping_strategies: list[str] = Field(default_factory=list)
    overwhelm_triggers: list[str] = Field(default_factory=list)


class ADHDProfileLoader:
    """Filesystem loader/writer for the ADHD profile YAML."""

    def __init__(self, base: Path):
        self._base = Path(base)
        self._path = self._base / "User Model" / "adhd_profile" / "profile.yaml"

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> ADHDProfile:
        """Load the profile; return defaults if the file does not exist."""
        if not self._path.exists():
            return ADHDProfile()
        try:
            with self._path.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except (OSError, yaml.YAMLError):
            return ADHDProfile()
        if not isinstance(data, dict):
            return ADHDProfile()
        return ADHDProfile.model_validate(data)

    def save(self, profile: ADHDProfile) -> None:
        """Persist the profile to ``profile.yaml``. Creates parent dirs."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = profile.model_dump(mode="json")
        with self._path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, sort_keys=False)


__all__ = [
    "ADHDProfile",
    "ADHDProfileLoader",
    "MedicationScheduleEntry",
    "MedicationWindow",
]
