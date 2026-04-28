"""Crisis safety boundary for Life OS routing."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import aiosqlite
from pydantic import BaseModel, Field

from kora_v2.support.profiles import to_json

CrisisSeverity = Literal["none", "support", "urgent", "emergency"]


class CrisisPreemptionResult(BaseModel):
    """Result of checking whether crisis safety preempts normal Life OS flow."""

    preempt: bool
    severity: CrisisSeverity
    matched_terms: list[str] = Field(default_factory=list)
    reason: str
    next_action: str
    user_message: str
    record_id: str | None = None


class CrisisSafetyRecord(BaseModel):
    id: str
    input_excerpt: str
    severity: CrisisSeverity
    matched_terms: list[str] = Field(default_factory=list)
    preempted: bool
    response_family: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


async def ensure_crisis_safety_tables(db: aiosqlite.Connection) -> None:
    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS safety_boundary_records (
            id TEXT PRIMARY KEY,
            boundary_type TEXT NOT NULL,
            trigger_text TEXT,
            risk_level TEXT NOT NULL,
            preempted_flow TEXT,
            response_summary TEXT NOT NULL,
            input_excerpt TEXT,
            severity TEXT,
            matched_terms TEXT,
            preempted INTEGER,
            response_family TEXT,
            metadata TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS domain_events (
            id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            aggregate_type TEXT NOT NULL,
            aggregate_id TEXT,
            source_service TEXT NOT NULL,
            correlation_id TEXT,
            causation_id TEXT,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )


class CrisisSafetyRouter:
    """Detect crisis language and return a preemption decision."""

    _EMERGENCY_PATTERNS = [
        r"\bkill myself\b",
        r"\bend my life\b",
        r"\bsuicide\b",
        r"\bsuicidal\b",
        r"\bi want to die\b",
        r"\bi'?m going to die by\b",
        r"\boverdose\b",
        r"\bcan'?t keep myself safe\b",
        r"\bgoing to hurt myself\b",
        r"\babout to hurt myself\b",
    ]
    _URGENT_PATTERNS = [
        r"\bself[- ]?harm\b",
        r"\bhurt myself\b",
        r"\bnot safe with myself\b",
        r"\bno reason to live\b",
        r"\bplanning to die\b",
    ]

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path

    def evaluate(
        self,
        text: str,
        *,
        preempted_flow: str | None = None,
    ) -> CrisisPreemptionResult:
        normalized = text.lower()
        emergency_terms = self._matches(normalized, self._EMERGENCY_PATTERNS)
        if emergency_terms:
            return CrisisPreemptionResult(
                preempt=True,
                severity="emergency",
                matched_terms=emergency_terms,
                reason="Crisis language indicates possible imminent self-harm risk.",
                next_action="preempt_life_os_and_show_crisis_support",
                user_message=(
                    "I cannot treat this like normal planning. If you might hurt "
                    "yourself or cannot stay safe, call or text 988 in the U.S. "
                    "and Canada, or call emergency services now. If there is "
                    "someone nearby you trust, contact them and do not stay alone."
                ),
            )

        urgent_terms = self._matches(normalized, self._URGENT_PATTERNS)
        if urgent_terms:
            return CrisisPreemptionResult(
                preempt=True,
                severity="urgent",
                matched_terms=urgent_terms,
                reason="Crisis-adjacent language requires safety handling before productivity.",
                next_action="preempt_life_os_and_offer_safety_options",
                user_message=(
                    "This needs safety support before day repair. If you are in "
                    "immediate danger, call emergency services. In the U.S. and "
                    "Canada, call or text 988 for crisis support. I can also help "
                    "you write a short message to a trusted person."
                ),
            )

        return CrisisPreemptionResult(
            preempt=False,
            severity="none",
            reason="No crisis safety pattern matched.",
            next_action="continue_life_os_flow",
            user_message="",
        )

    async def route(
        self,
        text: str,
        *,
        preempted_flow: str | None = None,
        persist: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> CrisisPreemptionResult:
        result = self.evaluate(text, preempted_flow=preempted_flow)
        if persist and result.preempt:
            if self.db_path is None:
                raise ValueError("db_path is required to persist crisis safety records")
            record = await self.record_boundary(
                text,
                severity=result.severity,
                matched_terms=result.matched_terms,
                preempted=result.preempt,
                metadata={"preempted_flow": preempted_flow, **(metadata or {})},
            )
            result.record_id = record.id
        return result

    async def record_boundary(
        self,
        text: str,
        *,
        severity: CrisisSeverity,
        matched_terms: list[str],
        preempted: bool,
        metadata: dict[str, Any] | None = None,
    ) -> CrisisSafetyRecord:
        if self.db_path is None:
            raise ValueError("db_path is required to persist crisis safety records")

        record = CrisisSafetyRecord(
            id=f"safety-{uuid.uuid4().hex[:12]}",
            input_excerpt=text[:500],
            severity=severity,
            matched_terms=matched_terms,
            preempted=preempted,
            response_family="crisis_safety",
            metadata=metadata or {},
            created_at=datetime.now(UTC),
        )

        async with aiosqlite.connect(str(self.db_path)) as db:
            await ensure_crisis_safety_tables(db)
            await db.execute(
                """
                INSERT INTO safety_boundary_records
                    (id, boundary_type, trigger_text, risk_level, preempted_flow,
                     response_summary, input_excerpt, severity, matched_terms,
                     preempted, response_family, metadata, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    "crisis",
                    record.input_excerpt,
                    record.severity,
                    record.metadata.get("preempted_flow"),
                    record.response_family,
                    record.input_excerpt,
                    record.severity,
                    to_json(record.matched_terms),
                    1 if record.preempted else 0,
                    record.response_family,
                    to_json(record.metadata),
                    record.created_at.isoformat(),
                ),
            )
            await db.execute(
                """
                INSERT INTO domain_events
                    (id, event_type, aggregate_type, aggregate_id, source_service,
                     correlation_id, causation_id, payload, created_at)
                VALUES (?, ?, ?, ?, ?, NULL, NULL, ?, ?)
                """,
                (
                    f"domain-{uuid.uuid4().hex[:12]}",
                    "CRISIS_SAFETY_PREEMPTED",
                    "safety_boundary",
                    record.id,
                    "CrisisSafetyRouter",
                    to_json(record.model_dump(mode="json")),
                    record.created_at.isoformat(),
                ),
            )
            await db.commit()

        return record

    def _matches(self, normalized_text: str, patterns: list[str]) -> list[str]:
        matches: list[str] = []
        for pattern in patterns:
            if re.search(pattern, normalized_text):
                matches.append(pattern.strip(r"\b").replace("\\", ""))
        return matches
