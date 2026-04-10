"""Tests for kora_v2.graph.reducers — Custom LangGraph state reducers."""

import pytest

from kora_v2.graph.reducers import (
    MAX_ERRORS,
    MAX_TRACE_SIZE,
    MAX_WORKSPACE_SIZE,
    append_list_reducer,
    bounded_append_list_reducer,
    bounded_errors_reducer,
    bounded_node_outputs_reducer,
    ensure_tool_pair_integrity,
    last_value_bool_reducer,
    last_value_list_reducer,
    last_value_string_reducer,
    merge_skills_reducer,
    or_bool_reducer,
    trace_reducer,
    workspace_reducer,
)


class TestAddMessagesReducer:
    """Test the message reducer with tool pair integrity."""

    def test_ensure_tool_pair_integrity_clean(self):
        """Clean messages should pass through unchanged."""
        msgs = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        result = ensure_tool_pair_integrity(msgs)
        assert len(result) == 2

    def test_ensure_tool_pair_integrity_orphaned_tool(self):
        """Orphaned tool result at the start should be trimmed."""
        msgs = [
            {"role": "tool", "content": "result data", "tool_call_id": "123"},
            {"role": "user", "content": "What happened?"},
            {"role": "assistant", "content": "Let me explain"},
        ]
        result = ensure_tool_pair_integrity(msgs)
        # Should skip the orphaned tool message and start at user
        assert len(result) == 2
        assert result[0]["role"] == "user"

    def test_ensure_tool_pair_integrity_complete_pair(self):
        """Complete tool_use + tool_result pair should be kept."""
        msgs = [
            {"role": "assistant", "content": [{"type": "tool_use", "id": "1", "name": "search", "input": {}}]},
            {"role": "tool", "content": "search results", "tool_call_id": "1"},
            {"role": "user", "content": "Thanks"},
        ]
        result = ensure_tool_pair_integrity(msgs)
        assert len(result) == 3

    def test_ensure_tool_pair_integrity_broken_pair_skipped(self):
        """Assistant with tool_use but no following tool result should be skipped."""
        msgs = [
            {"role": "assistant", "content": [{"type": "tool_use", "id": "1", "name": "search", "input": {}}]},
            {"role": "user", "content": "Never mind"},
        ]
        result = ensure_tool_pair_integrity(msgs)
        # The assistant with broken tool_use gets skipped, starts at user
        assert len(result) == 1
        assert result[0]["role"] == "user"

    def test_ensure_tool_pair_integrity_empty(self):
        """Empty list should return empty."""
        assert ensure_tool_pair_integrity([]) == []

    def test_ensure_tool_pair_integrity_system_message(self):
        """System message should be a safe start point."""
        msgs = [
            {"role": "system", "content": "You are Kora."},
            {"role": "user", "content": "Hi"},
        ]
        result = ensure_tool_pair_integrity(msgs)
        assert len(result) == 2
        assert result[0]["role"] == "system"


class TestWorkspaceReducer:
    """Test FIFO workspace with capacity limit."""

    def test_basic_add(self):
        """New items should be added to workspace."""
        result = workspace_reducer(None, [{"content": "fact1", "salience_score": 0.8}])
        assert len(result) == 1
        assert result[0]["content"] == "fact1"

    def test_capacity_limit(self):
        """Workspace should not exceed MAX_WORKSPACE_SIZE."""
        items = [{"content": f"item_{i}", "salience_score": 0.9} for i in range(10)]
        result = workspace_reducer(None, items)
        assert len(result) <= MAX_WORKSPACE_SIZE

    def test_age_decay(self):
        """Existing items should have salience decayed by 10%."""
        existing = [{"content": "old", "salience_score": 1.0}]
        new = [{"content": "new", "salience_score": 0.8}]
        result = workspace_reducer(existing, new)
        # The old item should have decayed salience (1.0 * 0.9 = 0.9)
        old_item = next(i for i in result if i["content"] == "old")
        assert old_item["salience_score"] == pytest.approx(0.9)

    def test_deduplication(self):
        """Duplicate content should be deduplicated (keep newest)."""
        existing = [{"content": "fact", "salience_score": 0.5}]
        new = [{"content": "fact", "salience_score": 0.9}]
        result = workspace_reducer(existing, new)
        # Should have only one "fact" entry — the new one (appears first in combined)
        facts = [i for i in result if i["content"] == "fact"]
        assert len(facts) == 1
        assert facts[0]["salience_score"] == 0.9

    def test_none_inputs(self):
        """Both None inputs should return empty list."""
        assert workspace_reducer(None, None) == []


class TestBoundedErrorsReducer:
    """Test bounded errors list stays within MAX_ERRORS."""

    def test_basic_append(self):
        """New errors should be appended."""
        result = bounded_errors_reducer(["err1"], ["err2"])
        assert result == ["err1", "err2"]

    def test_bounded_at_max(self):
        """Should not exceed MAX_ERRORS."""
        existing = [f"err_{i}" for i in range(MAX_ERRORS)]
        result = bounded_errors_reducer(existing, ["new_error"])
        assert len(result) == MAX_ERRORS
        # Newest error should be kept
        assert result[-1] == "new_error"
        # Oldest should be dropped
        assert result[0] == "err_1"

    def test_none_inputs(self):
        """Both None inputs should return empty list."""
        assert bounded_errors_reducer(None, None) == []

    def test_new_only(self):
        """Only new errors should work."""
        result = bounded_errors_reducer(None, ["err1", "err2"])
        assert result == ["err1", "err2"]


class TestTraceReducer:
    """Test bounded reasoning trace."""

    def test_basic_append(self):
        """Trace entries should be appended."""
        result = trace_reducer(
            [{"step": "s1"}],
            [{"step": "s2"}],
        )
        assert len(result) == 2

    def test_bounded_at_max(self):
        """Trace should not exceed MAX_TRACE_SIZE."""
        existing = [{"step": f"s_{i}"} for i in range(MAX_TRACE_SIZE)]
        result = trace_reducer(existing, [{"step": "new"}])
        assert len(result) == MAX_TRACE_SIZE
        assert result[-1]["step"] == "new"

    def test_none_inputs(self):
        """Both None returns empty."""
        assert trace_reducer(None, None) == []


class TestMergeSkillsReducer:
    """Test session-persistent skill union."""

    def test_union_no_duplicates(self):
        """Skills should be merged without duplicates."""
        result = merge_skills_reducer(["obsidian_vault"], ["coding", "obsidian_vault"])
        assert result == ["obsidian_vault", "coding"]

    def test_new_none_keeps_existing(self):
        """None new should return existing."""
        result = merge_skills_reducer(["obsidian_vault"], None)
        assert result == ["obsidian_vault"]

    def test_existing_none(self):
        """None existing should use new."""
        result = merge_skills_reducer(None, ["coding"])
        assert result == ["coding"]


class TestLastValueReducers:
    """Test last-value-wins reducers."""

    def test_last_value_list_new_replaces(self):
        """New list replaces existing."""
        result = last_value_list_reducer(["old"], ["new"])
        assert result == ["new"]

    def test_last_value_list_none_keeps_existing(self):
        """None new keeps existing."""
        result = last_value_list_reducer(["keep"], None)
        assert result == ["keep"]

    def test_or_bool_reducer(self):
        """OR reducer: True if either is True."""
        assert or_bool_reducer(False, True) is True
        assert or_bool_reducer(True, False) is True
        assert or_bool_reducer(False, False) is False
        assert or_bool_reducer(True, True) is True

    def test_last_value_bool_reducer(self):
        """Last-value bool: new wins."""
        assert last_value_bool_reducer(True, False) is False
        assert last_value_bool_reducer(False, True) is True

    def test_last_value_string_reducer(self):
        """Last-value string: new non-None wins."""
        assert last_value_string_reducer("old", "new") == "new"
        assert last_value_string_reducer("keep", None) == "keep"
        assert last_value_string_reducer(None, None) == ""


class TestAppendListReducers:
    """Test append-only list reducers."""

    def test_append_list(self):
        """Items should be appended."""
        result = append_list_reducer(["a"], ["b", "c"])
        assert result == ["a", "b", "c"]

    def test_bounded_append_list(self):
        """Bounded append should cap at MAX_TOOL_CALL_HISTORY."""
        from kora_v2.graph.reducers import MAX_TOOL_CALL_HISTORY
        existing = list(range(MAX_TOOL_CALL_HISTORY))
        result = bounded_append_list_reducer(existing, [999])
        assert len(result) == MAX_TOOL_CALL_HISTORY
        assert result[-1] == 999

    def test_bounded_node_outputs(self):
        """Node outputs should merge and cap at MAX_NODE_OUTPUTS."""
        from kora_v2.graph.reducers import MAX_NODE_OUTPUTS
        existing = {f"node_{i}": i for i in range(MAX_NODE_OUTPUTS)}
        result = bounded_node_outputs_reducer(existing, {"new_node": 99})
        assert len(result) == MAX_NODE_OUTPUTS
        assert "new_node" in result
