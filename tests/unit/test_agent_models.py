"""Unit tests for kora_v2.agents.models -- ActionRecord and idempotency rules."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from kora_v2.agents.models import (
    IDEMPOTENCY_RULES,
    ActionRecord,
    IdempotencyRule,
    SideEffectLevel,
)


class TestSideEffectLevel:
    """SideEffectLevel enum has expected members and string values."""

    def test_enum_members(self) -> None:
        assert set(SideEffectLevel) == {
            SideEffectLevel.NONE,
            SideEffectLevel.LOCAL,
            SideEffectLevel.EXTERNAL,
            SideEffectLevel.DESTRUCTIVE,
        }

    def test_string_values(self) -> None:
        assert SideEffectLevel.NONE.value == "none"
        assert SideEffectLevel.LOCAL.value == "local"
        assert SideEffectLevel.EXTERNAL.value == "external"
        assert SideEffectLevel.DESTRUCTIVE.value == "destructive"

    def test_is_str(self) -> None:
        """SideEffectLevel inherits from str -- usable as dict key and in JSON."""
        assert isinstance(SideEffectLevel.NONE, str)


class TestActionRecord:
    """ActionRecord creation and serialization."""

    @pytest.fixture()
    def sample_record(self) -> ActionRecord:
        return ActionRecord(
            action_id="act-001",
            tool_name="read_file",
            input_hash="abc123",
            output_hash="def456",
            side_effect_level=SideEffectLevel.NONE,
            idempotent=True,
            timestamp=datetime(2026, 4, 6, 12, 0, 0, tzinfo=timezone.utc),
            session_id="sess-xyz",
            turn_number=3,
        )

    def test_creation(self, sample_record: ActionRecord) -> None:
        assert sample_record.action_id == "act-001"
        assert sample_record.tool_name == "read_file"
        assert sample_record.side_effect_level == SideEffectLevel.NONE
        assert sample_record.idempotent is True
        assert sample_record.turn_number == 3

    def test_serialization_roundtrip(self, sample_record: ActionRecord) -> None:
        data = sample_record.model_dump()
        restored = ActionRecord.model_validate(data)
        assert restored == sample_record

    def test_json_roundtrip(self, sample_record: ActionRecord) -> None:
        json_str = sample_record.model_dump_json()
        restored = ActionRecord.model_validate_json(json_str)
        assert restored == sample_record

    def test_optional_output_hash(self) -> None:
        record = ActionRecord(
            action_id="act-002",
            tool_name="write_file",
            input_hash="xyz",
            side_effect_level=SideEffectLevel.LOCAL,
            idempotent=False,
            timestamp=datetime.now(tz=timezone.utc),
            session_id="sess-abc",
            turn_number=1,
        )
        assert record.output_hash is None

    def test_side_effect_level_from_string(self) -> None:
        """Pydantic coerces string values to enum members."""
        record = ActionRecord(
            action_id="act-003",
            tool_name="api_call",
            input_hash="hash",
            side_effect_level="external",  # type: ignore[arg-type]
            idempotent=False,
            timestamp=datetime.now(tz=timezone.utc),
            session_id="sess-def",
            turn_number=5,
        )
        assert record.side_effect_level == SideEffectLevel.EXTERNAL


class TestIdempotencyRules:
    """IDEMPOTENCY_RULES covers all SideEffectLevel members."""

    def test_completeness(self) -> None:
        """Every SideEffectLevel has a corresponding rule."""
        for level in SideEffectLevel:
            assert level in IDEMPOTENCY_RULES, f"Missing rule for {level}"

    def test_no_extra_rules(self) -> None:
        """Rules dict has exactly as many entries as enum members."""
        assert len(IDEMPOTENCY_RULES) == len(SideEffectLevel)

    def test_none_is_safe_to_replay(self) -> None:
        rule = IDEMPOTENCY_RULES[SideEffectLevel.NONE]
        assert rule.safe_to_replay is True
        assert rule.requires_reauth is False

    def test_destructive_requires_reauth(self) -> None:
        rule = IDEMPOTENCY_RULES[SideEffectLevel.DESTRUCTIVE]
        assert rule.safe_to_replay is False
        assert rule.requires_reauth is True

    def test_local_not_replayed(self) -> None:
        rule = IDEMPOTENCY_RULES[SideEffectLevel.LOCAL]
        assert rule.safe_to_replay is False

    def test_external_not_replayed(self) -> None:
        rule = IDEMPOTENCY_RULES[SideEffectLevel.EXTERNAL]
        assert rule.safe_to_replay is False

    def test_all_rules_are_idempotency_rule_instances(self) -> None:
        for rule in IDEMPOTENCY_RULES.values():
            assert isinstance(rule, IdempotencyRule)
