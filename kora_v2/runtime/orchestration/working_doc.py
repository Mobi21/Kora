"""Working Document primitive — spec §11.

A working document is the living markdown file under
``_KoraMemory/Inbox/{slug}.md`` that a pipeline instance uses as its
source of truth for goal, plan, findings, and completion state. This
module holds the read/write contract:

* Atomic writes via temp-file + rename (no partial writes visible).
* Per-pipeline-instance ``asyncio.Lock`` for single-writer discipline;
  reads are lock-free.
* YAML frontmatter parsing.
* Section-based reads (``# Goal``, ``# Summary``, ``# Current Plan``, etc.).
* ``- [ ]`` / ``- [x]`` / ``- [skip]`` / ``- [cancel]`` task markers in
  the Current Plan section.
* User edit detection via mtime + merge rules (user wins on conflicts).
* Adaptive task list reconciliation — a new item appearing in the
  Current Plan section produces a new WorkerTask row on the dispatcher's
  next tick.
* Kora-judged completion via frontmatter ``status: done``.
* Summary maintenance helpers.

Everything outside this module should touch a working doc through
:class:`WorkingDocStore`, never by reading or writing markdown directly,
so the file contract stays in one place.
"""

from __future__ import annotations

import asyncio
import os
import re
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import structlog
import yaml

log = structlog.get_logger(__name__)


# ── Constants ────────────────────────────────────────────────────────────

FRONTMATTER_FENCE = "---"
_DEFAULT_STATUS = "in_progress"

# Canonical section headings in the order they appear in a fresh doc.
SECTIONS: tuple[str, ...] = (
    "Goal",
    "Summary",
    "Current Plan",
    "Findings",
    "Notes",
    "Open Questions",
    "Dead Ends",
    "Completed Tasks Log",
    "Completion",
)


class WorkingDocStatus(StrEnum):
    """Valid values for the frontmatter ``status`` field."""

    IN_PROGRESS = "in_progress"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


# ── Data model ───────────────────────────────────────────────────────────


@dataclass
class PlanItem:
    """A single entry in the ``# Current Plan`` section.

    ``marker`` is the checkbox/state indicator:
        ``' '`` — open task (``- [ ]``)
        ``'x'`` — completed task (``- [x]``)
        ``'skip'`` — user strike marker (``- [skip]``)
        ``'cancel'`` — user cancellation marker (``- [cancel]``)
    """

    text: str
    marker: str = " "
    raw_line: str | None = None

    @property
    def is_open(self) -> bool:
        return self.marker == " "

    @property
    def is_done(self) -> bool:
        return self.marker.lower() == "x"

    @property
    def is_skipped(self) -> bool:
        return self.marker.lower() in {"skip", "cancel"}


@dataclass
class WorkingDocHandle:
    """An in-memory view of a working document.

    Held by :class:`WorkingDocStore` callers only for the duration of a
    single read-modify-write cycle — the store reads current state from
    disk before every write, so handles are not long-lived.
    """

    path: Path
    frontmatter: dict[str, Any] = field(default_factory=dict)
    sections: dict[str, str] = field(default_factory=dict)
    mtime: float = 0.0

    def exists(self) -> bool:
        return self.path.exists()

    # ── Convenience accessors ────────────────────────────────────
    @property
    def status(self) -> str:
        return str(self.frontmatter.get("status", _DEFAULT_STATUS))

    @property
    def task_id(self) -> str | None:
        tid = self.frontmatter.get("task_id")
        return str(tid) if tid is not None else None

    @property
    def pipeline(self) -> str | None:
        p = self.frontmatter.get("pipeline")
        return str(p) if p is not None else None

    @property
    def goal(self) -> str:
        return self.sections.get("Goal", "").strip()

    @property
    def summary(self) -> str:
        return self.sections.get("Summary", "").strip()

    def section(self, name: str) -> str:
        return self.sections.get(name, "")

    def parse_current_plan(self) -> list[PlanItem]:
        return parse_plan_items(self.sections.get("Current Plan", ""))


@dataclass
class WorkingDocUpdate:
    """A structured write request against a working document.

    The store merges these onto whatever the on-disk doc currently
    holds, preserving user edits.
    """

    frontmatter_patch: dict[str, Any] = field(default_factory=dict)
    section_patches: dict[str, str] = field(default_factory=dict)
    append_plan_items: list[PlanItem] = field(default_factory=list)
    mark_plan_items_done: list[str] = field(default_factory=list)
    completed_task_log_entry: str | None = None
    summary: str | None = None
    reason: str = ""


@dataclass
class TaskListDiff:
    """Result of reconciling the Current Plan section against WorkerTask rows.

    Consumers turn ``added`` items into new :class:`WorkerTask` rows on
    the dispatcher's next tick; ``cancelled`` items are forwarded as
    cancellation requests.
    """

    added: list[PlanItem]
    cancelled: list[PlanItem]
    completed: list[PlanItem]


# ── Parsing helpers ──────────────────────────────────────────────────────

_FRONTMATTER_RE = re.compile(
    r"^---\n(?P<meta>.*?)\n---\n(?P<body>.*)$",
    re.DOTALL,
)
_PLAN_ITEM_RE = re.compile(
    r"^-\s*\[(?P<marker>[^\]]*)\]\s*(?P<text>.*?)\s*$"
)


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split *text* into (frontmatter_dict, body). Missing/invalid frontmatter
    returns ``({}, text)`` so the caller can still read sections.
    """
    if not text.startswith(FRONTMATTER_FENCE + "\n"):
        return {}, text
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        return {}, text
    try:
        data = yaml.safe_load(match.group("meta")) or {}
        if not isinstance(data, dict):
            log.warning("working_doc_frontmatter_not_dict", type=type(data).__name__)
            return {}, text
    except yaml.YAMLError:
        log.warning("working_doc_frontmatter_invalid")
        return {}, text
    return data, match.group("body")


def parse_sections(body: str) -> dict[str, str]:
    """Split the markdown body into ``{heading: content}``.

    Only top-level ``# Heading`` lines are recognised as section
    boundaries; deeper headings (``##`` and below) are kept inside their
    parent section.
    """
    sections: dict[str, str] = {}
    current_name: str | None = None
    current_lines: list[str] = []

    for line in body.splitlines(keepends=True):
        m = re.match(r"^#\s+(.*?)\s*$", line.rstrip("\n"))
        if m is not None:
            if current_name is not None:
                sections[current_name] = "".join(current_lines).rstrip("\n")
            current_name = m.group(1).strip()
            current_lines = []
        else:
            if current_name is not None:
                current_lines.append(line)

    if current_name is not None:
        sections[current_name] = "".join(current_lines).rstrip("\n")
    return sections


def parse_plan_items(section_text: str) -> list[PlanItem]:
    """Extract ``- [ ]`` / ``- [x]`` / ``- [skip]`` / ``- [cancel]`` items.

    Lines that don't match the item shape (free prose, sub-lists, etc.)
    are ignored. Text inside a matching line is stripped of trailing
    ``— **done**`` / ``← currently working`` annotations so repeat
    parses are idempotent.
    """
    items: list[PlanItem] = []
    for raw_line in section_text.splitlines():
        m = _PLAN_ITEM_RE.match(raw_line.strip())
        if m is None:
            continue
        marker = m.group("marker").strip().lower()
        if marker == "":
            marker = " "
        text = m.group("text").strip()
        text = re.sub(r"\s*—\s*\*\*done\*\*\s*$", "", text, flags=re.IGNORECASE)
        text = re.sub(
            r"\s*←\s*\*\*currently working\*\*\s*$", "", text, flags=re.IGNORECASE
        )
        items.append(PlanItem(text=text, marker=marker, raw_line=raw_line))
    return items


def serialise_plan_items(items: list[PlanItem]) -> str:
    """Reverse of :func:`parse_plan_items` — render as markdown."""
    lines: list[str] = []
    for item in items:
        marker = item.marker
        if marker == " ":
            lines.append(f"- [ ] {item.text}")
        elif marker.lower() == "x":
            lines.append(f"- [x] {item.text}")
        else:
            lines.append(f"- [{marker}] {item.text}")
    return "\n".join(lines)


def render_document(
    frontmatter: dict[str, Any],
    sections: dict[str, str],
) -> str:
    """Serialise a working doc back to markdown, preserving section order."""
    parts: list[str] = []
    parts.append(FRONTMATTER_FENCE)
    parts.append(yaml.safe_dump(frontmatter, sort_keys=False).rstrip())
    parts.append(FRONTMATTER_FENCE)
    parts.append("")

    seen: set[str] = set()
    for name in SECTIONS:
        body = sections.get(name, "").rstrip()
        parts.append(f"# {name}")
        if body:
            parts.append(body)
        parts.append("")
        seen.add(name)

    # Preserve any non-canonical sections users created (e.g. "Final")
    for name, body in sections.items():
        if name in seen:
            continue
        parts.append(f"# {name}")
        body = body.rstrip()
        if body:
            parts.append(body)
        parts.append("")

    return "\n".join(parts).rstrip() + "\n"


def slugify_goal(goal: str, *, max_len: int = 40) -> str:
    """Turn a goal string into a filesystem-safe slug."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", goal.lower()).strip("-")
    if not slug:
        slug = "task"
    return slug[:max_len].rstrip("-") or "task"


def build_doc_path(
    inbox_root: Path,
    *,
    pipeline_name: str,
    instance_id: str,
    goal: str,
) -> Path:
    """Compose the canonical working doc path (spec §11.2)."""
    short = instance_id.split("-")[-1][:6] if "-" in instance_id else instance_id[:6]
    slug = slugify_goal(goal)
    fname = f"{pipeline_name}_{short}_{slug}.md"
    return inbox_root / fname


def build_initial_document(
    *,
    task_id: str,
    pipeline_name: str,
    goal: str,
    intent_duration: str | None,
    parent_session_id: str | None,
    seed_plan_items: list[str] | None = None,
) -> str:
    """Build the initial markdown for a new working doc."""
    now = datetime.now(UTC).isoformat()
    frontmatter: dict[str, Any] = {
        "task_id": task_id,
        "pipeline": pipeline_name,
        "goal": goal,
        "status": _DEFAULT_STATUS,
        "intent_duration": intent_duration or "open",
        "started_at": now,
        "updated_at": now,
        "completed_at": None,
        "parent_session_id": parent_session_id,
    }
    plan_items: list[PlanItem] = []
    for text in seed_plan_items or []:
        plan_items.append(PlanItem(text=text, marker=" "))
    sections: dict[str, str] = {
        "Goal": goal.strip(),
        "Summary": "",
        "Current Plan": serialise_plan_items(plan_items),
        "Findings": "",
        "Notes": "",
        "Open Questions": "",
        "Dead Ends": "",
        "Completed Tasks Log": "",
        "Completion": "",
    }
    return render_document(frontmatter, sections)


# ── Store ────────────────────────────────────────────────────────────────


class WorkingDocStore:
    """Per-instance-locked read/write access to working docs.

    One :class:`WorkingDocStore` is held by :class:`OrchestrationEngine`
    and reused across pipelines. It maintains the lock dictionary and
    the Inbox root directory so callers do not need to know where docs
    live.
    """

    def __init__(self, inbox_root: Path) -> None:
        self._inbox_root = inbox_root
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._last_known_mtimes: dict[str, float] = {}

    @property
    def inbox_root(self) -> Path:
        return self._inbox_root

    def ensure_inbox(self) -> None:
        self._inbox_root.mkdir(parents=True, exist_ok=True)

    def doc_path(
        self,
        *,
        pipeline_name: str,
        instance_id: str,
        goal: str,
    ) -> Path:
        return build_doc_path(
            self._inbox_root,
            pipeline_name=pipeline_name,
            instance_id=instance_id,
            goal=goal,
        )

    # ── Locking ──────────────────────────────────────────────────
    def lock_for(self, instance_id: str) -> asyncio.Lock:
        """Return the per-instance write lock (created on first access)."""
        return self._locks[instance_id]

    # ── Creation ─────────────────────────────────────────────────
    async def create(
        self,
        *,
        instance_id: str,
        task_id: str,
        pipeline_name: str,
        goal: str,
        intent_duration: str | None = None,
        parent_session_id: str | None = None,
        seed_plan_items: list[str] | None = None,
    ) -> Path:
        """Create a new working doc if it does not already exist.

        Idempotent: if a doc already exists at the computed path the
        method returns the existing path without overwriting.
        """
        self.ensure_inbox()
        path = self.doc_path(
            pipeline_name=pipeline_name, instance_id=instance_id, goal=goal
        )
        if path.exists():
            log.debug("working_doc_exists", path=str(path))
            self._last_known_mtimes[str(path)] = path.stat().st_mtime
            return path

        body = build_initial_document(
            task_id=task_id,
            pipeline_name=pipeline_name,
            goal=goal,
            intent_duration=intent_duration,
            parent_session_id=parent_session_id,
            seed_plan_items=seed_plan_items,
        )
        async with self._locks[instance_id]:
            await _atomic_write(path, body)
        self._last_known_mtimes[str(path)] = path.stat().st_mtime
        log.info(
            "working_doc_created",
            pipeline=pipeline_name,
            instance_id=instance_id,
            path=str(path),
        )
        return path

    # ── Reads ────────────────────────────────────────────────────
    async def read(self, path: Path) -> WorkingDocHandle | None:
        """Load a working doc into an in-memory handle.

        Returns ``None`` if the file does not exist. Reads are
        lock-free — stale snapshots are accepted per §11.4.
        """
        if not path.exists():
            return None
        mtime = path.stat().st_mtime
        text = await asyncio.to_thread(path.read_text, encoding="utf-8")
        frontmatter, body = parse_frontmatter(text)
        sections = parse_sections(body)
        return WorkingDocHandle(
            path=path, frontmatter=frontmatter, sections=sections, mtime=mtime
        )

    async def read_sections(
        self, path: Path, names: list[str]
    ) -> dict[str, str]:
        """Return a subset of named sections for targeted reads (§11.4)."""
        handle = await self.read(path)
        if handle is None:
            return {}
        return {n: handle.sections.get(n, "") for n in names}

    def user_edit_detected(self, path: Path) -> bool:
        """True if the file's mtime has advanced since the store last wrote it."""
        if not path.exists():
            return False
        current = path.stat().st_mtime
        last = self._last_known_mtimes.get(str(path))
        if last is None:
            return False
        return current > last + 0.0005  # small epsilon to absorb filesystem jitter

    # ── Writes ───────────────────────────────────────────────────
    async def apply_update(
        self,
        *,
        instance_id: str,
        path: Path,
        update: WorkingDocUpdate,
    ) -> WorkingDocHandle:
        """Apply *update* to the doc under the instance's write lock."""
        async with self._locks[instance_id]:
            current_text = ""
            if path.exists():
                current_text = await asyncio.to_thread(
                    path.read_text, encoding="utf-8"
                )
            frontmatter, body = parse_frontmatter(current_text)
            sections = parse_sections(body)

            for name in SECTIONS:
                sections.setdefault(name, "")

            # Goal edit detection (spec §11.5 — log warning if changed)
            prior_goal = sections.get("Goal", "").strip()

            for k, v in update.frontmatter_patch.items():
                frontmatter[k] = v
            frontmatter["updated_at"] = datetime.now(UTC).isoformat()
            frontmatter.setdefault("status", _DEFAULT_STATUS)

            for name, body_text in update.section_patches.items():
                sections[name] = body_text.rstrip()

            if update.summary is not None:
                sections["Summary"] = update.summary.rstrip()

            if update.completed_task_log_entry:
                log_section = sections.get("Completed Tasks Log", "")
                line = f"- {update.completed_task_log_entry.strip()}"
                sections["Completed Tasks Log"] = (
                    (log_section + "\n" + line).strip() if log_section else line
                )

            if update.append_plan_items or update.mark_plan_items_done:
                plan_items = parse_plan_items(sections.get("Current Plan", ""))
                existing_texts = {p.text for p in plan_items}
                for item in update.append_plan_items:
                    if item.text not in existing_texts:
                        plan_items.append(item)
                        existing_texts.add(item.text)
                for done_text in update.mark_plan_items_done:
                    for p in plan_items:
                        if p.text == done_text and p.marker == " ":
                            p.marker = "x"
                sections["Current Plan"] = serialise_plan_items(plan_items)

            new_goal = sections.get("Goal", "").strip()
            if prior_goal and new_goal != prior_goal:
                log.warning(
                    "working_doc_goal_changed",
                    path=str(path),
                    instance_id=instance_id,
                )

            new_text = render_document(frontmatter, sections)
            await _atomic_write(path, new_text)
            self._last_known_mtimes[str(path)] = path.stat().st_mtime
            log.debug(
                "working_doc_updated",
                path=str(path),
                reason=update.reason or "update",
            )
            return WorkingDocHandle(
                path=path,
                frontmatter=frontmatter,
                sections=sections,
                mtime=self._last_known_mtimes[str(path)],
            )

    async def mark_status(
        self,
        *,
        instance_id: str,
        path: Path,
        status: WorkingDocStatus,
        reason: str = "",
        completion_text: str | None = None,
    ) -> WorkingDocHandle:
        """Flip the frontmatter ``status`` field and append a Completion section."""
        patch: dict[str, Any] = {"status": status.value}
        if status in {
            WorkingDocStatus.DONE,
            WorkingDocStatus.FAILED,
            WorkingDocStatus.CANCELLED,
        }:
            patch["completed_at"] = datetime.now(UTC).isoformat()
        section_patches: dict[str, str] = {}
        if completion_text:
            section_patches["Completion"] = completion_text
        return await self.apply_update(
            instance_id=instance_id,
            path=path,
            update=WorkingDocUpdate(
                frontmatter_patch=patch,
                section_patches=section_patches,
                reason=reason or f"status={status.value}",
            ),
        )

    # ── Merge: adaptive task list reconciliation ────────────────
    def reconcile_plan(
        self,
        handle: WorkingDocHandle,
        known_task_descriptions: list[str],
    ) -> TaskListDiff:
        """Diff the doc's Current Plan against ``known_task_descriptions``.

        * ``added``     — items in the doc with marker ``' '`` not in the
          known list → caller creates new WorkerTasks.
        * ``cancelled`` — items marked ``skip`` / ``cancel`` that are in
          the known list → caller requests cancellation.
        * ``completed`` — items marked ``x`` in the doc → caller can
          ack completion state if the task is not already terminal.
        """
        plan_items = handle.parse_current_plan()
        known = set(known_task_descriptions)
        added: list[PlanItem] = []
        cancelled: list[PlanItem] = []
        completed: list[PlanItem] = []
        for item in plan_items:
            if item.is_skipped and item.text in known:
                cancelled.append(item)
            elif item.is_done and item.text in known:
                completed.append(item)
            elif item.is_open and item.text not in known:
                added.append(item)
        return TaskListDiff(added=added, cancelled=cancelled, completed=completed)


# ── Private: atomic filesystem write ────────────────────────────────────


async def _atomic_write(path: Path, content: str) -> None:
    """Write *content* atomically via temp file + rename (§11.4)."""
    path.parent.mkdir(parents=True, exist_ok=True)

    def _sync_write() -> None:
        fd, tmp_path = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=str(path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(content)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, str(path))
        except Exception:
            try:
                os.unlink(tmp_path)
            except FileNotFoundError:
                pass
            raise

    await asyncio.to_thread(_sync_write)
