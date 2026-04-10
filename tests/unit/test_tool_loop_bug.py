"""Tests for the tool loop bug — multi-tool call handling in MiniMaxProvider.

The bug: when the LLM calls 2+ tools simultaneously, _format_messages()
produces one separate user message per tool result. cleanup_incomplete_messages()
then checks only the immediately following message for matching IDs, finds a
subset mismatch, and drops all tool messages. The LLM re-calls the same tools.

These tests verify:
1. _format_messages batches consecutive tool results into one user message
2. cleanup_incomplete_messages handles multi-tool results (separate or batched)
3. The args/arguments key is handled correctly in _format_messages
"""

import pytest

from kora_v2.llm.minimax import (
    MiniMaxProvider,
    _extract_tool_result_ids,
    _extract_tool_use_ids,
    _msg_has_tool_use,
)


# ── Helpers ──────────────────────────────────────────────────────────────


def _make_assistant_with_tools(*tool_ids: str) -> dict:
    """Create an assistant message with N tool_use blocks."""
    return {
        "role": "assistant",
        "content": [
            {"type": "tool_use", "id": tid, "name": f"tool_{tid}", "input": {"k": "v"}}
            for tid in tool_ids
        ],
    }


def _make_tool_result(tool_use_id: str, content: str = "ok") -> dict:
    """Create a user message with a single tool_result block (current broken format)."""
    return {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}
        ],
    }


def _make_batched_tool_results(*pairs: tuple[str, str]) -> dict:
    """Create a user message with multiple tool_result blocks (correct format)."""
    return {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": tid, "content": content}
            for tid, content in pairs
        ],
    }


def _make_tool_msg_dict(tool_call_id: str, content: str = "ok") -> dict:
    """Create a tool result message dict as produced by tool_loop (role=tool)."""
    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "content": content,
    }


def _make_assistant_tool_calls_dict(*tool_calls: tuple[str, str, dict]) -> dict:
    """Create an assistant message dict with tool_calls as produced by think node.

    Each tool_call is (id, name, arguments).
    """
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"id": tid, "name": name, "args": args}
            for tid, name, args in tool_calls
        ],
    }


# ═══════════════════════════════════════════════════════════════════════
# Test cleanup_incomplete_messages — the core bug
# ═══════════════════════════════════════════════════════════════════════


class TestCleanupMultiToolResults:
    """cleanup_incomplete_messages must keep multi-tool pairs intact."""

    def test_single_tool_pair_kept(self):
        """Baseline: 1 tool_use + 1 tool_result is kept."""
        messages = [
            {"role": "user", "content": "hello"},
            _make_assistant_with_tools("A"),
            _make_tool_result("A"),
            {"role": "assistant", "content": "done"},
        ]
        result = MiniMaxProvider.cleanup_incomplete_messages(messages)
        assert len(result) == 4

    def test_multi_tool_separate_results_kept(self):
        """BUG TEST: 3 tool_use IDs + 3 separate tool_result messages must be kept.

        This is the exact pattern that triggers the bug: _format_messages produces
        one user message per tool result, but cleanup only checks i+1.
        """
        messages = [
            {"role": "user", "content": "hello"},
            _make_assistant_with_tools("A", "B", "C"),
            _make_tool_result("A"),
            _make_tool_result("B"),
            _make_tool_result("C"),
            {"role": "assistant", "content": "all done"},
        ]
        result = MiniMaxProvider.cleanup_incomplete_messages(messages)
        # All 6 messages must survive
        assert len(result) == 6, (
            f"Expected 6 messages (all kept), got {len(result)}. "
            "cleanup_incomplete_messages dropped valid multi-tool results."
        )

    def test_multi_tool_batched_results_kept(self):
        """3 tool_use IDs + 1 batched tool_result message must be kept."""
        messages = [
            {"role": "user", "content": "hello"},
            _make_assistant_with_tools("A", "B", "C"),
            _make_batched_tool_results(("A", "ok"), ("B", "ok"), ("C", "ok")),
            {"role": "assistant", "content": "all done"},
        ]
        result = MiniMaxProvider.cleanup_incomplete_messages(messages)
        assert len(result) == 4

    def test_partial_results_dropped(self):
        """3 tool_use IDs but only 1 result → genuinely orphaned, drop the pair."""
        messages = [
            {"role": "user", "content": "hello"},
            _make_assistant_with_tools("A", "B", "C"),
            _make_tool_result("A"),
            # B and C results missing — this is a real orphan
            {"role": "user", "content": "never mind"},
        ]
        result = MiniMaxProvider.cleanup_incomplete_messages(messages)
        # Assistant + result A should be dropped (orphaned)
        # The "never mind" user message should survive
        roles = [m["role"] for m in result]
        assert "user" in roles
        # The assistant with tool_use should NOT be in result
        for m in result:
            if m["role"] == "assistant":
                assert not _msg_has_tool_use(m), "Orphaned tool_use assistant should be removed"

    def test_two_tool_separate_results_kept(self):
        """2 tool_use IDs + 2 separate results — minimal multi-tool case."""
        messages = [
            {"role": "user", "content": "hello"},
            _make_assistant_with_tools("A", "B"),
            _make_tool_result("A"),
            _make_tool_result("B"),
            {"role": "assistant", "content": "done"},
        ]
        result = MiniMaxProvider.cleanup_incomplete_messages(messages)
        assert len(result) == 5, (
            f"Expected 5 messages, got {len(result)}. "
            "Even 2-tool results are being dropped."
        )

    def test_six_tools_separate_results_kept(self):
        """6 tool_use IDs + 6 separate results — the acceptance test scenario."""
        messages = [
            {"role": "user", "content": "hello"},
            _make_assistant_with_tools("A", "B", "C", "D", "E", "F"),
            _make_tool_result("A"),
            _make_tool_result("B"),
            _make_tool_result("C"),
            _make_tool_result("D"),
            _make_tool_result("E"),
            _make_tool_result("F"),
            {"role": "assistant", "content": "all six done"},
        ]
        result = MiniMaxProvider.cleanup_incomplete_messages(messages)
        assert len(result) == 9, (
            f"Expected 9 messages, got {len(result)}. "
            "6-tool batch was incorrectly treated as orphaned."
        )

    def test_consecutive_multi_tool_batches(self):
        """Two consecutive multi-tool batches should both be kept."""
        messages = [
            {"role": "user", "content": "hello"},
            # First batch: 2 tools
            _make_assistant_with_tools("A", "B"),
            _make_tool_result("A"),
            _make_tool_result("B"),
            # Second batch: 2 tools
            _make_assistant_with_tools("C", "D"),
            _make_tool_result("C"),
            _make_tool_result("D"),
            {"role": "assistant", "content": "all done"},
        ]
        result = MiniMaxProvider.cleanup_incomplete_messages(messages)
        assert len(result) == 8

    def test_trailing_assistant_with_tools_stripped(self):
        """Pass 1: trailing assistant with tool_use and no results should be removed."""
        messages = [
            {"role": "user", "content": "hello"},
            _make_assistant_with_tools("A", "B"),
        ]
        result = MiniMaxProvider.cleanup_incomplete_messages(messages)
        # Trailing assistant with tool_use removed by pass 1
        assert len(result) == 1
        assert result[0]["role"] == "user"


# ═══════════════════════════════════════════════════════════════════════
# Test _format_messages — tool result batching
# ═══════════════════════════════════════════════════════════════════════


class TestFormatMessagesBatching:
    """_format_messages must batch consecutive tool results into one user message."""

    @pytest.fixture
    def provider(self):
        """Create a MiniMaxProvider with minimal config for testing _format_messages."""
        from unittest.mock import MagicMock

        settings = MagicMock()
        settings.api_base = "https://api.minimax.io"
        settings.api_key = "test-key"
        settings.timeout = 30
        settings.retry_attempts = 0
        settings.max_tokens = 4096
        # Prevent real HTTP client creation
        import kora_v2.llm.minimax as mod
        original_init = MiniMaxProvider.__init__

        provider = MiniMaxProvider.__new__(MiniMaxProvider)
        provider._settings = settings
        provider._full_base_url = "https://api.minimax.io"
        provider._client = MagicMock()
        provider._call_count = 0
        provider._total_prompt_tokens = 0
        provider._total_completion_tokens = 0
        provider._total_thinking_tokens = 0
        provider._cache_hash = None
        return provider

    def test_consecutive_tool_results_batched(self, provider):
        """3 consecutive role=tool dicts should produce 1 user message with 3 tool_result blocks."""
        messages = [
            {"role": "user", "content": "do stuff"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "A", "name": "t1", "arguments": {"x": 1}},
                    {"id": "B", "name": "t2", "arguments": {"y": 2}},
                    {"id": "C", "name": "t3", "arguments": {"z": 3}},
                ],
            },
            _make_tool_msg_dict("A", "result_A"),
            _make_tool_msg_dict("B", "result_B"),
            _make_tool_msg_dict("C", "result_C"),
        ]
        _, api_messages = provider._format_messages(messages)

        # Should be: user, assistant, user (batched results)
        assert len(api_messages) == 3, (
            f"Expected 3 API messages (user, assistant, batched-user), "
            f"got {len(api_messages)}: {[m['role'] for m in api_messages]}"
        )
        assert api_messages[0]["role"] == "user"
        assert api_messages[1]["role"] == "assistant"
        assert api_messages[2]["role"] == "user"

        # The batched user message should have 3 tool_result blocks
        result_content = api_messages[2]["content"]
        assert isinstance(result_content, list), "Batched results should be a list"
        assert len(result_content) == 3, (
            f"Expected 3 tool_result blocks in batched message, got {len(result_content)}"
        )
        result_ids = {b["tool_use_id"] for b in result_content}
        assert result_ids == {"A", "B", "C"}

    def test_single_tool_result_unchanged(self, provider):
        """Single tool result should still work (produces 1 user message with 1 block)."""
        messages = [
            {"role": "user", "content": "do stuff"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "X", "name": "t1", "arguments": {"x": 1}},
                ],
            },
            _make_tool_msg_dict("X", "result_X"),
        ]
        _, api_messages = provider._format_messages(messages)

        assert len(api_messages) == 3
        result_msg = api_messages[2]
        assert result_msg["role"] == "user"
        assert len(result_msg["content"]) == 1
        assert result_msg["content"][0]["tool_use_id"] == "X"

    def test_tool_results_not_merged_across_non_tool(self, provider):
        """Tool results separated by a regular message should NOT be merged."""
        messages = [
            {"role": "user", "content": "do stuff"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "A", "name": "t1", "arguments": {}},
                ],
            },
            _make_tool_msg_dict("A", "result_A"),
            {"role": "user", "content": "now do more"},  # breaks the sequence
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "B", "name": "t2", "arguments": {}},
                ],
            },
            _make_tool_msg_dict("B", "result_B"),
        ]
        _, api_messages = provider._format_messages(messages)

        # Should be: user, assistant, user(A), user(text), assistant, user(B)
        assert len(api_messages) == 6
        # Each tool result should be in its own user message (not batched across the gap)
        assert api_messages[2]["content"][0]["tool_use_id"] == "A"
        assert api_messages[5]["content"][0]["tool_use_id"] == "B"


# ═══════════════════════════════════════════════════════════════════════
# Test args vs arguments key mismatch
# ═══════════════════════════════════════════════════════════════════════


class TestArgsKeyMismatch:
    """_format_messages must handle both 'args' and 'arguments' keys for tool call input."""

    @pytest.fixture
    def provider(self):
        """Minimal MiniMaxProvider instance."""
        provider = MiniMaxProvider.__new__(MiniMaxProvider)
        from unittest.mock import MagicMock

        provider._settings = MagicMock()
        provider._settings.api_base = "https://api.minimax.io"
        provider._full_base_url = "https://api.minimax.io"
        provider._client = MagicMock()
        provider._call_count = 0
        provider._total_prompt_tokens = 0
        provider._total_completion_tokens = 0
        provider._total_thinking_tokens = 0
        provider._cache_hash = None
        return provider

    def test_args_key_preserved(self, provider):
        """tool_calls with 'args' key (LangGraph format) should produce non-empty input."""
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "tc1", "name": "recall", "args": {"query": "test", "layer": "all"}},
                ],
            },
        ]
        _, api_messages = provider._format_messages(messages)

        assert len(api_messages) == 1
        blocks = api_messages[0]["content"]
        tool_use = next(b for b in blocks if b["type"] == "tool_use")
        assert tool_use["input"] == {"query": "test", "layer": "all"}, (
            f"Expected args to be preserved in input, got: {tool_use['input']}"
        )

    def test_arguments_key_preserved(self, provider):
        """tool_calls with 'arguments' key should also produce correct input."""
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "tc1", "name": "recall", "arguments": {"query": "test"}},
                ],
            },
        ]
        _, api_messages = provider._format_messages(messages)

        blocks = api_messages[0]["content"]
        tool_use = next(b for b in blocks if b["type"] == "tool_use")
        assert tool_use["input"] == {"query": "test"}


# ═══════════════════════════════════════════════════════════════════════
# Test full pipeline: format → cleanup (integration)
# ═══════════════════════════════════════════════════════════════════════


class TestFormatThenCleanupIntegration:
    """After _format_messages + cleanup_incomplete_messages, multi-tool results must survive."""

    @pytest.fixture
    def provider(self):
        provider = MiniMaxProvider.__new__(MiniMaxProvider)
        from unittest.mock import MagicMock

        provider._settings = MagicMock()
        provider._settings.api_base = "https://api.minimax.io"
        provider._full_base_url = "https://api.minimax.io"
        provider._client = MagicMock()
        provider._call_count = 0
        provider._total_prompt_tokens = 0
        provider._total_completion_tokens = 0
        provider._total_thinking_tokens = 0
        provider._cache_hash = None
        return provider

    def test_format_then_cleanup_preserves_multi_tool(self, provider):
        """The exact scenario from the bug: think produces 3 tools, tool_loop returns 3 results.

        After _format_messages converts them and cleanup runs, all messages must survive.
        """
        # Messages as they arrive from the think node (dicts, not LangGraph objects)
        messages = [
            {"role": "user", "content": "log my meds and breakfast and start a focus block"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "tc_1", "name": "log_medication", "args": {"medication_name": "Adderall", "dose": "20mg"}},
                    {"id": "tc_2", "name": "log_meal", "args": {"meal_type": "breakfast", "description": "Bagel"}},
                    {"id": "tc_3", "name": "start_focus_block", "args": {"label": "Dashboard"}},
                ],
            },
            {"role": "tool", "tool_call_id": "tc_1", "content": '{"status": "ok"}'},
            {"role": "tool", "tool_call_id": "tc_2", "content": '{"status": "ok"}'},
            {"role": "tool", "tool_call_id": "tc_3", "content": '{"status": "ok"}'},
        ]

        _, api_messages = provider._format_messages(messages)
        cleaned = MiniMaxProvider.cleanup_incomplete_messages(api_messages)

        # Should have: user, assistant (with tool_use blocks), user (with tool_result blocks)
        assert len(cleaned) >= 3, (
            f"Expected at least 3 messages after format+cleanup, got {len(cleaned)}. "
            "The tool loop bug is still present — multi-tool results are being dropped."
        )

        # The assistant message with tool_use must survive
        assistant_msgs = [m for m in cleaned if m["role"] == "assistant"]
        assert any(_msg_has_tool_use(m) for m in assistant_msgs), (
            "Assistant message with tool_use was dropped by cleanup"
        )

        # All 3 tool_result IDs must be present
        all_result_ids: set[str] = set()
        for m in cleaned:
            all_result_ids |= _extract_tool_result_ids(m)
        assert all_result_ids == {"tc_1", "tc_2", "tc_3"}, (
            f"Expected tool_result IDs {{tc_1, tc_2, tc_3}}, got {all_result_ids}. "
            "Some tool results were dropped."
        )
