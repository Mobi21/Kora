"""First-run wizard for the Rich CLI (Phase 5).

Replaces the 3-question stub in ``cli/app.py:_check_first_run`` with a
4-section guided wizard. Stores results in:

* ``_KoraMemory/User Model/adhd_profile/profile.yaml`` via
  :class:`kora_v2.adhd.profile.ADHDProfileLoader`
* ``kora_v2/core/settings.py`` fields (``user_tz``, notifications, DND,
  cadence)
* Optional prose note under ``_KoraMemory/User Model/adhd_profile/``

The wizard is intentionally small — it only asks what it needs to
populate ``ADHDProfile`` deterministically, and folds the legacy
name/use_case/Brave-key prompts into Section 1.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

from kora_v2.adhd.profile import (
    ADHDProfile,
    ADHDProfileLoader,
    MedicationScheduleEntry,
    MedicationWindow,
)

# ── Result structure ───────────────────────────────────────────────────────


@dataclass
class WizardResult:
    """Everything a completed wizard produced, for the caller to act on."""

    name: str = ""
    pronouns: str = ""
    use_case: str = ""
    conditions: list[str] = field(default_factory=list)
    peak_window_label: str = ""
    crash_window_label: str = ""
    medications_text: str = ""
    coping_strategies: list[str] = field(default_factory=list)
    timezone: str = ""
    weekly_planning_day: str = "Sunday"
    weekly_planning_time: time = time(18, 0)
    notifications_per_hour: int = 4
    dnd_start: time | None = None
    dnd_end: time | None = None
    life_tracking_domains: list[str] = field(default_factory=list)
    minimax_api_key: str = ""
    brave_api_key: str = ""


# ── Mapping helpers ─────────────────────────────────────────────────────────


_PEAK_RANGES = {
    "morning": (6, 9),
    "late morning": (9, 12),
    "afternoon": (12, 16),
    "evening": (16, 21),
    "varies": None,
}

_CRASH_RANGES = {
    "early afternoon": (13, 15),
    "late afternoon": (15, 17),
    "evening": (17, 21),
    "varies": None,
}


def _parse_medication_text(text: str) -> list[MedicationScheduleEntry]:
    """Extract medication entries from a freeform string.

    Accepts lines like:
      ``Adderall XR 20mg 08:00-09:00``
      ``Adderall IR 10mg 13:00-15:00``
    Returns an empty list if nothing parses — the wizard surfaces a
    note telling the user they can edit profile.yaml by hand later.
    """
    import re

    entries: list[MedicationScheduleEntry] = []
    # Regex: <name> [<dose_with_unit>] <HH:MM>-<HH:MM>
    # The dose must include a unit (mg/mcg/g/mL) so the parser can tell
    # "Adderall 08:00-09:00" (no dose) from "Adderall 20mg 08:00-09:00".
    pattern = re.compile(
        r"([A-Za-z][\w\s.-]*?)(?:\s+([\d.]+\s*(?:mg|mcg|g|mL)))?\s+"
        r"(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})",
        re.IGNORECASE,
    )
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        match = pattern.search(line)
        if not match:
            continue
        name = match.group(1).strip()
        dose = (match.group(2) or "").strip()
        try:
            start = time.fromisoformat(match.group(3))
            end = time.fromisoformat(match.group(4))
        except ValueError:
            continue
        entries.append(
            MedicationScheduleEntry(
                name=name,
                dose=dose or None,
                windows=[MedicationWindow(start=start, end=end)],
            )
        )
    return entries


def _detect_user_tz() -> str:
    """Best-effort system timezone detection (IANA name)."""
    from kora_v2.core.settings import _detect_user_tz as _detect

    return _detect()


# ── Prompts (async-safe wrappers) ───────────────────────────────────────────


async def _aprompt(
    console: Console, question: str, default: str = "", choices: list[str] | None = None
) -> str:
    def _run() -> str:
        return Prompt.ask(question, default=default, choices=choices)

    return await asyncio.get_event_loop().run_in_executor(None, _run)


async def _aconfirm(console: Console, question: str, default: bool = False) -> bool:
    def _run() -> bool:
        return Confirm.ask(question, default=default)

    return await asyncio.get_event_loop().run_in_executor(None, _run)


# ── Wizard sections ─────────────────────────────────────────────────────────


async def _section_identity(console: Console, result: WizardResult) -> None:
    console.print()
    console.print(
        Panel(
            "Let's start with a few things about you.",
            title="1. Identity",
            border_style="cyan",
        )
    )
    result.name = await _aprompt(console, "What should I call you?")
    result.pronouns = await _aprompt(
        console, "What are your pronouns? (optional)", default=""
    )
    result.use_case = await _aprompt(
        console, "What do you mainly want help with?"
    )


async def _section_adhd(console: Console, result: WizardResult) -> None:
    console.print()
    console.print(
        Panel(
            "A few questions about how your brain works.",
            title="2. ADHD & Neurodivergent Support",
            border_style="magenta",
        )
    )
    conditions_raw = await _aprompt(
        console,
        "Do you have ADHD or another condition I should be aware of? "
        "(comma-separated: adhd,anxiety,depression,none,other)",
        default="none",
    )
    result.conditions = [
        c.strip() for c in conditions_raw.split(",") if c.strip()
    ]

    if "adhd" not in {c.lower() for c in result.conditions}:
        return

    result.peak_window_label = await _aprompt(
        console,
        "When are you usually most focused?",
        default="late morning",
        choices=list(_PEAK_RANGES.keys()),
    )
    result.crash_window_label = await _aprompt(
        console,
        "When does your energy usually crash?",
        default="early afternoon",
        choices=list(_CRASH_RANGES.keys()),
    )
    take_meds = await _aconfirm(
        console, "Do you take medication on a schedule?", default=False
    )
    if take_meds:
        console.print(
            "[dim]Enter one medication per line, e.g. "
            "'Adderall XR 20mg 08:00-09:00'. Blank line to finish.[/dim]"
        )
        lines: list[str] = []
        while True:
            line = await _aprompt(console, "  meds", default="")
            if not line.strip():
                break
            lines.append(line)
        result.medications_text = "\n".join(lines)
    coping_raw = await _aprompt(
        console,
        "What helps you get started on tasks? (comma-separated)",
        default="timers",
    )
    result.coping_strategies = [
        c.strip() for c in coping_raw.split(",") if c.strip()
    ]


async def _section_planning(console: Console, result: WizardResult) -> None:
    console.print()
    console.print(
        Panel(
            "Planning preferences.",
            title="3. Planning",
            border_style="green",
        )
    )
    detected_tz = _detect_user_tz()
    result.timezone = await _aprompt(
        console,
        f"Your timezone? (detected: {detected_tz})",
        default=detected_tz,
    )
    result.weekly_planning_day = await _aprompt(
        console,
        "When do you like to plan your week? (day of week)",
        default="Sunday",
    )
    plan_time_raw = await _aprompt(
        console, "At what time? (HH:MM, 24h)", default="18:00"
    )
    try:
        result.weekly_planning_time = time.fromisoformat(plan_time_raw)
    except ValueError:
        result.weekly_planning_time = time(18, 0)

    notify_raw = await _aprompt(
        console,
        "How many notifications per hour feel right?",
        default="4",
        choices=["1", "2", "4", "8"],
    )
    try:
        result.notifications_per_hour = int(notify_raw)
    except ValueError:
        result.notifications_per_hour = 4

    set_dnd = await _aconfirm(
        console, "Any times I should never bother you? (DND window)", default=False
    )
    if set_dnd:
        dnd_start_raw = await _aprompt(
            console, "DND start (HH:MM)", default="22:00"
        )
        dnd_end_raw = await _aprompt(
            console, "DND end (HH:MM)", default="07:00"
        )
        try:
            result.dnd_start = time.fromisoformat(dnd_start_raw)
            result.dnd_end = time.fromisoformat(dnd_end_raw)
        except ValueError:
            result.dnd_start = None
            result.dnd_end = None


async def _section_life_mgmt(console: Console, result: WizardResult) -> None:
    console.print()
    console.print(
        Panel(
            "Which life domains would you like help tracking? "
            "(multi-select: medications,meals,finances,routines,focus,all,none)",
            title="4. Life Management",
            border_style="yellow",
        )
    )
    raw = await _aprompt(
        console, "  domains", default="medications,meals,focus"
    )
    selected = {d.strip().lower() for d in raw.split(",") if d.strip()}
    if "all" in selected:
        selected = {"medications", "meals", "finances", "routines", "focus"}
    result.life_tracking_domains = sorted(selected - {"none"})


# ── Persistence ─────────────────────────────────────────────────────────────


def _result_to_profile(result: WizardResult) -> ADHDProfile:
    """Build an ``ADHDProfile`` from wizard answers."""
    peak_windows: list[tuple[int, int]] = []
    crash_periods: list[tuple[int, int]] = []
    peak_range = _PEAK_RANGES.get(result.peak_window_label)
    if peak_range is not None:
        peak_windows.append(peak_range)
    crash_range = _CRASH_RANGES.get(result.crash_window_label)
    if crash_range is not None:
        crash_periods.append(crash_range)

    medication_schedule = _parse_medication_text(result.medications_text)

    return ADHDProfile(
        version=1,
        time_correction_factor=1.5,
        check_in_interval_minutes=120,
        transition_buffer_minutes=15,
        peak_windows=peak_windows,
        crash_periods=crash_periods,
        medication_schedule=medication_schedule,
        coping_strategies=result.coping_strategies,
        overwhelm_triggers=[],
    )


def _persist(result: WizardResult, memory_base: Path, container: Any) -> None:
    """Write wizard output to profile.yaml + settings."""
    profile = _result_to_profile(result)
    loader = ADHDProfileLoader(memory_base)
    loader.save(profile)

    # Best-effort settings update — the container's settings object is
    # a pydantic BaseSettings instance, which allows live mutation of
    # fields even without a re-construction.
    settings = getattr(container, "settings", None)
    if settings is not None:
        try:
            if result.timezone:
                settings.user_tz = result.timezone
            notifications = getattr(settings, "notifications", None)
            if notifications is not None:
                notifications.max_per_hour = result.notifications_per_hour
                if result.dnd_start is not None:
                    notifications.dnd_start = result.dnd_start
                if result.dnd_end is not None:
                    notifications.dnd_end = result.dnd_end
            planning = getattr(settings, "planning", None)
            if planning is not None and getattr(planning, "cadence", None):
                planning.cadence.weekly_planning_day = result.weekly_planning_day
                planning.cadence.weekly_planning_time = (
                    result.weekly_planning_time
                )
        except Exception:
            pass


# ── Entry point ─────────────────────────────────────────────────────────────


async def run_wizard(
    console: Console,
    container: Any | None = None,
    memory_base: Path | None = None,
) -> WizardResult:
    """Run all 4 sections and return the populated ``WizardResult``.

    If ``memory_base`` is provided, the result is saved to
    ``<memory_base>/User Model/adhd_profile/profile.yaml`` immediately.
    ``container`` is optional — when present, runtime settings get
    updated in-place; without it, only the YAML profile is written.
    """
    console.print()
    console.print(
        Panel(
            "Welcome to Kora. This takes ~2 minutes and helps me "
            "understand how to support you.",
            title="Getting Started",
            border_style="bold cyan",
        )
    )

    result = WizardResult()
    try:
        await _section_identity(console, result)
        await _section_adhd(console, result)
        await _section_planning(console, result)
        await _section_life_mgmt(console, result)
    except (EOFError, KeyboardInterrupt):
        console.print("[yellow]Wizard cancelled.[/yellow]")
        return result

    base = memory_base
    if base is None and container is not None:
        settings = getattr(container, "settings", None)
        if settings is not None and hasattr(settings, "memory"):
            base = Path(settings.memory.kora_memory_path)

    if base is not None:
        _persist(result, base, container)
        console.print(
            Panel(
                "Saved your profile. You can always edit "
                "``_KoraMemory/User Model/adhd_profile/profile.yaml`` "
                "later.",
                title="Done",
                border_style="green",
            )
        )

    return result


__all__ = ["WizardResult", "run_wizard"]
