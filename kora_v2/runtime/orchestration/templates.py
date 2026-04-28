"""Template registry — spec §10.2.

YAML-backed registry at ``_KoraMemory/.kora/templates/templates.yaml``
that renders zero-request templated messages. Users can edit the file
to tune Kora's voice without touching Python; the registry hot-reloads
on mtime change.

A single ``Template`` row looks like::

    rate_limit_paused:
      text: "Hey — I'm catching up on my request window. Back in {minutes} min."
      priority: high
      bypass_dnd: false

``render(template_id, **vars)`` substitutes ``{name}`` placeholders with
the supplied variables and returns a :class:`RenderedTemplate` the
:class:`~kora_v2.runtime.orchestration.notifications.NotificationGate`
can deliver.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import structlog
import yaml

log = structlog.get_logger(__name__)


# ── Defaults shipped with Kora (spec §10.2) ──────────────────────────────

DEFAULT_TEMPLATES: dict[str, dict[str, Any]] = {
    "rate_limit_paused": {
        "text": (
            "Hey — I'm catching up on my request window. "
            "Back to full speed in about {minutes} minutes."
        ),
        "priority": "high",
        "bypass_dnd": False,
    },
    "task_started": {
        "text": "Working on {goal}. I'll report back when I have something.",
        "priority": "medium",
        "bypass_dnd": False,
    },
    "task_progress": {
        "text": "{goal}: {marker} ({percent}% done)",
        "priority": "low",
        "bypass_dnd": False,
    },
    "task_completed": {
        "text": "Finished {goal}. {summary}",
        "priority": "medium",
        "bypass_dnd": False,
    },
    "task_failed": {
        "text": (
            "Ran into trouble with {goal} — {reason}. "
            "Working doc has what I got so far."
        ),
        "priority": "medium",
        "bypass_dnd": False,
    },
    "pipeline_completed": {
        "text": "{pipeline_name} finished. {goal}",
        "priority": "medium",
        "bypass_dnd": False,
    },
    "pipeline_failed": {
        "text": "{pipeline_name} ran into trouble: {reason}. {goal}",
        "priority": "medium",
        "bypass_dnd": False,
    },
    "background_digest_ready": {
        "text": "Overnight digest of {count} items is ready to review in your Inbox.",
        "priority": "low",
        "bypass_dnd": True,
    },
    "budget_low_warning": {
        "text": "Rate limit window is getting tight. Pausing background work.",
        "priority": "high",
        "bypass_dnd": False,
    },
    "reminder_generic": {
        "text": "Reminder: {subject}",
        "priority": "high",
        "bypass_dnd": False,
    },
    "pattern_nudge": {
        "text": "I noticed a pattern: {title}. {description}",
        "priority": "medium",
        "bypass_dnd": False,
    },
}


class TemplatePriority(StrEnum):
    """Priority carried by a template."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# ── Data model ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Template:
    """A single template entry from the YAML registry."""

    id: str
    text: str
    priority: TemplatePriority
    bypass_dnd: bool


@dataclass
class RenderedTemplate:
    """Result of ``registry.render(...)``, ready for delivery."""

    template_id: str
    text: str
    priority: TemplatePriority
    bypass_dnd: bool
    vars: dict[str, Any]


# ── Registry ──────────────────────────────────────────────────────────────


class TemplateRegistry:
    """YAML-backed template registry with mtime hot-reload.

    The store file lives at ``{template_root}/templates.yaml`` where
    ``template_root`` is typically ``_KoraMemory/.kora/templates/``. If
    the file does not exist the registry writes the
    :data:`DEFAULT_TEMPLATES` block so the user can edit it.
    """

    def __init__(self, template_root: Path) -> None:
        self._root = template_root
        self._path = template_root / "templates.yaml"
        self._templates: dict[str, Template] = {}
        self._last_mtime: float = 0.0

    @property
    def path(self) -> Path:
        return self._path

    def ensure_defaults(self) -> None:
        """Write the default YAML file if it doesn't exist yet."""
        self._root.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            self._path.write_text(
                yaml.safe_dump(DEFAULT_TEMPLATES, sort_keys=False),
                encoding="utf-8",
            )
            log.info("template_defaults_written", path=str(self._path))

    def reload_if_changed(self) -> bool:
        """Reload from disk if the file's mtime has advanced.

        Returns True if a reload actually happened. Missing file falls
        back to in-memory defaults so callers still get usable
        templates.
        """
        if not self._path.exists():
            if not self._templates:
                self._templates = {
                    tid: _coerce(tid, payload)
                    for tid, payload in DEFAULT_TEMPLATES.items()
                }
            return False

        current = self._path.stat().st_mtime
        if current <= self._last_mtime and self._templates:
            return False

        try:
            raw = yaml.safe_load(self._path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            log.exception("template_registry_parse_failed", path=str(self._path))
            return False
        if not isinstance(raw, dict):
            log.warning("template_registry_not_dict", path=str(self._path))
            return False

        new_templates: dict[str, Template] = {}
        for tid, payload in raw.items():
            if not isinstance(payload, dict):
                log.warning("template_entry_not_dict", template_id=tid)
                continue
            try:
                new_templates[tid] = _coerce(tid, payload)
            except (KeyError, ValueError) as exc:
                log.warning(
                    "template_entry_invalid",
                    template_id=tid,
                    error=str(exc),
                )
        # Ensure every default id is present — user-deleted entries
        # fall back to the hardcoded default so the orchestration layer
        # always has a template for its critical messages.
        for tid, payload in DEFAULT_TEMPLATES.items():
            new_templates.setdefault(tid, _coerce(tid, payload))

        self._templates = new_templates
        self._last_mtime = current
        log.info(
            "template_registry_loaded",
            count=len(new_templates),
            path=str(self._path),
        )
        return True

    def get(self, template_id: str) -> Template | None:
        self.reload_if_changed()
        return self._templates.get(template_id)

    def render(
        self,
        template_id: str,
        *,
        priority_override: TemplatePriority | None = None,
        **kwargs: Any,
    ) -> RenderedTemplate:
        """Render the named template with ``kwargs`` substituted.

        Raises :class:`KeyError` if the template is unknown and not in
        :data:`DEFAULT_TEMPLATES` either. Missing variables are left as
        literal ``{name}`` placeholders rather than raising, so a
        misspelt key produces a visible artefact instead of an exception
        deep inside the dispatcher.
        """
        tpl = self.get(template_id)
        if tpl is None and template_id in DEFAULT_TEMPLATES:
            tpl = _coerce(template_id, DEFAULT_TEMPLATES[template_id])
        if tpl is None:
            raise KeyError(f"Unknown template: {template_id!r}")

        try:
            text = tpl.text.format_map(_DefaultDict(kwargs))
        except Exception:
            log.exception("template_render_failed", template_id=template_id)
            text = tpl.text

        return RenderedTemplate(
            template_id=template_id,
            text=text,
            priority=priority_override or tpl.priority,
            bypass_dnd=tpl.bypass_dnd,
            vars=dict(kwargs),
        )

    def ids(self) -> list[str]:
        self.reload_if_changed()
        return sorted(self._templates.keys())

    def reset_to_defaults(self) -> None:
        """Overwrite the YAML file with :data:`DEFAULT_TEMPLATES`.

        Mainly used by tests; humans use the file directly.
        """
        if self._path.exists():
            backup = self._path.with_suffix(".yaml.bak")
            shutil.copy(self._path, backup)
        self._path.write_text(
            yaml.safe_dump(DEFAULT_TEMPLATES, sort_keys=False),
            encoding="utf-8",
        )
        self._templates.clear()
        self._last_mtime = 0.0
        self.reload_if_changed()


# ── Helpers ───────────────────────────────────────────────────────────────


class _DefaultDict(dict):
    """str.format_map helper that keeps missing keys as ``{key}``."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _coerce(template_id: str, payload: dict[str, Any]) -> Template:
    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"Template {template_id!r} missing `text`")
    priority_raw = payload.get("priority", "medium")
    try:
        priority = TemplatePriority(priority_raw)
    except ValueError:
        priority = TemplatePriority.MEDIUM
    bypass = bool(payload.get("bypass_dnd", False))
    return Template(id=template_id, text=text, priority=priority, bypass_dnd=bypass)
