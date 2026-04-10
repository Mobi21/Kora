"""Tests for kora_v2.graph.state -- SupervisorState TypedDict."""

from __future__ import annotations

from kora_v2.graph.state import SupervisorState


class TestSupervisorState:
    """Verify SupervisorState structure and defaults."""

    def test_state_has_expected_keys(self) -> None:
        """SupervisorState annotations include all required fields."""
        keys = set(SupervisorState.__annotations__)
        expected = {
            "messages",
            "session_id",
            "turn_count",
            "emotional_state",
            "energy_estimate",
            "pending_items",
            "active_workers",
            "tool_call_records",
            "frozen_prefix",
            "response_content",
            "errors",
            "_dynamic_suffix",
            "_pending_tool_calls",
            # Phase 4 compaction fields
            "compaction_tier",
            "compaction_tokens",
            "compaction_summary",
            "session_bridge",
            "greeting_sent",
            # Phase 6 autonomous updates
            "_unread_autonomous_updates",
            # WS4 overlap detection
            "_overlap_score",
            "_overlap_action",
        }
        assert expected == keys

    def test_state_is_total_false(self) -> None:
        """SupervisorState uses total=False so all fields are optional."""
        assert SupervisorState.__total__ is False

    def test_state_can_be_instantiated_empty(self) -> None:
        """An empty dict satisfies SupervisorState (total=False)."""
        state: SupervisorState = {}  # type: ignore[typeddict-item]
        assert isinstance(state, dict)

    def test_state_can_be_instantiated_with_values(self) -> None:
        """SupervisorState accepts all documented fields."""
        state: SupervisorState = {
            "messages": [{"role": "user", "content": "hello"}],
            "session_id": "abc123",
            "turn_count": 1,
            "emotional_state": None,
            "energy_estimate": None,
            "pending_items": [],
            "active_workers": [],
            "tool_call_records": [],
            "frozen_prefix": "test prefix",
            "response_content": "",
            "errors": [],
        }
        assert state["session_id"] == "abc123"
        assert state["turn_count"] == 1
        assert len(state["messages"]) == 1
