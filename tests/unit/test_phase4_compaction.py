"""Tests for Phase 4 compaction engine — 4-stage pipeline.

Covers:
- TestObservationMasking: mask_observations (3 tests)
- TestStructuredSummary: create_structured_summary + apply_structured_compaction (2 tests)
- TestCompactionPipeline: run_compaction routing by tier (5 tests)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from kora_v2.context.budget import BudgetTier
from kora_v2.context.compaction import (
    apply_structured_compaction,
    build_hard_stop_bridge,
    create_structured_summary,
    mask_observations,
    run_compaction,
)
from kora_v2.core.models import CompactionResult, SessionBridge

# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_turn(
    user_content: str,
    assistant_content: str | list[dict],
    tool_call_id: str | None = None,
    tool_name: str | None = None,
    tool_result_content: str | None = None,
) -> list[dict]:
    """Build a single turn (user + assistant + optional tool exchange)."""
    msgs: list[dict] = [{"role": "user", "content": user_content}]

    if tool_call_id and tool_name:
        # Assistant with a tool call block
        msgs.append(
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": tool_call_id,
                        "name": tool_name,
                        "input": {"query": "test"},
                    }
                ],
            }
        )
        msgs.append(
            {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": tool_result_content or "short result",
            }
        )
    else:
        if isinstance(assistant_content, str):
            msgs.append({"role": "assistant", "content": assistant_content})
        else:
            msgs.append({"role": "assistant", "content": assistant_content})

    return msgs


def _make_conversation(n_turns: int = 15) -> list[dict]:
    """Build a multi-turn conversation for compaction tests."""
    messages: list[dict] = []
    for i in range(n_turns):
        messages.extend(
            _make_turn(
                user_content=f"User message {i + 1}",
                assistant_content=f"Assistant reply {i + 1}",
            )
        )
    return messages


def _make_mock_llm(response_text: str) -> MagicMock:
    """Create a mock LLM with generate() returning a MagicMock with .content."""
    llm = MagicMock()
    llm.generate = AsyncMock(return_value=MagicMock(content=response_text))
    return llm


# ── TestObservationMasking ──────────────────────────────────────────────────────


class TestObservationMasking:
    """Tests for mask_observations — Stage 1."""

    def test_masks_old_tool_results(self):
        """Large tool result outside the last 10 turns is replaced with a short placeholder."""
        # Build 12 turns: turns 1-2 have large tool results (old), turns 11-12 are recent
        messages: list[dict] = []

        # Old turn 1: user + assistant with tool_call + tool result (large)
        messages.append({"role": "user", "content": "Search for something"})
        messages.append(
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tc-001",
                        "name": "web_search",
                        "input": {"query": "python"},
                    }
                ],
            }
        )
        large_content = "A" * 300  # > 200 chars → should be masked
        messages.append(
            {
                "role": "tool",
                "tool_call_id": "tc-001",
                "content": large_content,
            }
        )

        # Fill in more old turns to push the above outside the last 10 turns
        for i in range(2, 12):
            messages.extend(
                _make_turn(
                    user_content=f"Turn {i} user",
                    assistant_content=f"Turn {i} reply",
                )
            )

        result = mask_observations(messages, preserve_last_n=10)

        # Find the tool message that was masked
        tool_msgs = [m for m in result if m.get("role") == "tool"]
        assert len(tool_msgs) >= 1

        masked = tool_msgs[0]
        content = masked["content"]
        # Original 300-char content should be replaced with a short placeholder
        assert len(content) < len(large_content)
        assert "web_search" in content or "..." in content or "[result" in content

    def test_preserves_recent_tool_results(self):
        """Tool results inside the last N turns are NOT masked."""
        messages: list[dict] = []

        # Many older turns to push below
        for i in range(10):
            messages.extend(
                _make_turn(
                    user_content=f"Old turn {i}",
                    assistant_content=f"Old reply {i}",
                )
            )

        # Recent turn with large tool result — should NOT be masked
        large_content = "B" * 500
        messages.append({"role": "user", "content": "Recent search"})
        messages.append(
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tc-recent",
                        "name": "file_read",
                        "input": {},
                    }
                ],
            }
        )
        messages.append(
            {
                "role": "tool",
                "tool_call_id": "tc-recent",
                "content": large_content,
            }
        )

        result = mask_observations(messages, preserve_last_n=10)

        # Find the recent tool message
        tool_msgs = [m for m in result if m.get("role") == "tool"]
        recent_tool = None
        for m in tool_msgs:
            if m.get("tool_call_id") == "tc-recent":
                recent_tool = m
                break

        assert recent_tool is not None
        # Content should be unchanged
        assert recent_tool["content"] == large_content

    def test_strips_thinking_blocks(self):
        """Thinking blocks in old assistant messages are removed; text blocks are kept."""
        messages: list[dict] = []

        # Old assistant message with thinking block
        messages.append({"role": "user", "content": "A question"})
        messages.append(
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "thinking",
                        "thinking": "Let me think about this carefully...",
                    },
                    {
                        "type": "text",
                        "text": "The answer is 42.",
                    },
                ],
            }
        )

        # Fill in recent turns to push the thinking message outside the window
        for i in range(11):
            messages.extend(
                _make_turn(
                    user_content=f"New turn {i}",
                    assistant_content=f"New reply {i}",
                )
            )

        result = mask_observations(messages, preserve_last_n=10)

        # Find the old assistant message (second message, index 1)
        old_assistant = result[1]
        assert old_assistant["role"] == "assistant"

        content = old_assistant["content"]
        # Thinking blocks should be removed
        if isinstance(content, list):
            thinking_blocks = [b for b in content if b.get("type") == "thinking"]
            assert len(thinking_blocks) == 0, "Thinking blocks should have been stripped"
            # Text blocks should still be present
            text_blocks = [b for b in content if b.get("type") == "text"]
            assert len(text_blocks) > 0, "Text blocks should be preserved"
        else:
            # If content is now a plain string, it should contain the original text
            assert "42" in content


# ── TestStructuredSummary ────────────────────────────────────────────────────


class TestStructuredSummary:
    """Tests for create_structured_summary + apply_structured_compaction — Stage 2."""

    MOCK_SUMMARY = """\
## Goal
[Complete the project planning]

## Progress
### Done
- Defined scope
### In Progress
- Writing tests
### Blocked
- [none]

## Key Decisions
- Framework: LangGraph — Fits multi-agent design

## Emotional Context
Engaged and focused throughout.

## Open Threads
- Review timeline

## Critical Context
Project due 2026-04-15, team size 3"""

    @pytest.mark.asyncio
    async def test_produces_structured_summary(self):
        """Mock LLM → 6-section structured summary is returned as a string."""
        messages = _make_conversation(n_turns=15)
        llm = _make_mock_llm(self.MOCK_SUMMARY)

        summary = await create_structured_summary(
            messages, llm, preserve_first_n=2, preserve_last_n=10
        )

        assert isinstance(summary, str)
        assert len(summary) > 0
        # LLM was called exactly once
        llm.generate.assert_called_once()
        # The returned content matches what the mock produced
        assert summary == self.MOCK_SUMMARY

    @pytest.mark.asyncio
    async def test_preserves_first_and_last_turns(self):
        """After compaction, first N and last N messages are in the result unchanged."""
        messages = _make_conversation(n_turns=20)
        llm = _make_mock_llm(self.MOCK_SUMMARY)

        result = await apply_structured_compaction(
            messages, llm, preserve_first_n=2, preserve_last_n=10
        )

        assert isinstance(result, CompactionResult)
        assert result.stage == "structured_summary"

        new_msgs = result.messages

        # First 2 messages are preserved unchanged
        assert new_msgs[0] == messages[0]
        assert new_msgs[1] == messages[1]

        # Last 10 messages of the original are preserved
        original_last_10 = messages[-10:]
        result_last_10 = new_msgs[-10:]
        assert result_last_10 == original_last_10

        # Token counts are populated
        assert result.tokens_before > 0
        assert result.tokens_after > 0

        # A summary system message is inserted in the middle
        middle_msgs = new_msgs[2:-10]
        assert len(middle_msgs) >= 1
        summary_msgs = [m for m in middle_msgs if m.get("role") == "system"]
        assert len(summary_msgs) >= 1
        assert self.MOCK_SUMMARY in summary_msgs[0]["content"]


# ── TestCompactionPipeline ────────────────────────────────────────────────────


class TestCompactionPipeline:
    """Tests for run_compaction — routing by BudgetTier."""

    MOCK_SUMMARY = "## Goal\nTesting compaction\n\n## Progress\n### Done\n- setup\n### In Progress\n- tests\n### Blocked\n- [none]\n\n## Key Decisions\n- None\n\n## Emotional Context\nNeutral.\n\n## Open Threads\n- None\n\n## Critical Context\nTest suite run."

    @pytest.mark.asyncio
    async def test_normal_tier_no_compaction(self):
        """NORMAL tier returns None — no compaction needed."""
        messages = _make_conversation(n_turns=5)
        llm = _make_mock_llm(self.MOCK_SUMMARY)

        result = await run_compaction(messages, BudgetTier.NORMAL, llm)

        assert result is None
        llm.generate.assert_not_called()

    @pytest.mark.asyncio
    async def test_prune_tier_runs_masking_only(self):
        """PRUNE tier returns a CompactionResult from observation masking."""
        messages = _make_conversation(n_turns=15)
        llm = _make_mock_llm(self.MOCK_SUMMARY)

        result = await run_compaction(messages, BudgetTier.PRUNE, llm)

        assert result is not None
        assert isinstance(result, CompactionResult)
        assert result.stage == "observation_masking"
        # For PRUNE, we do NOT call the LLM
        llm.generate.assert_not_called()

    @pytest.mark.asyncio
    async def test_summarize_tier_runs_summary(self):
        """SUMMARIZE tier returns a CompactionResult with stage='structured_summary'."""
        messages = _make_conversation(n_turns=20)
        llm = _make_mock_llm(self.MOCK_SUMMARY)

        result = await run_compaction(messages, BudgetTier.SUMMARIZE, llm)

        assert result is not None
        assert isinstance(result, CompactionResult)
        assert result.stage == "structured_summary"
        # LLM was used for summarization
        llm.generate.assert_called()

    @pytest.mark.asyncio
    async def test_aggressive_tier_recompresses(self):
        """AGGRESSIVE tier returns CompactionResult with at most 5 user messages remaining."""
        messages = _make_conversation(n_turns=20)
        llm = _make_mock_llm(self.MOCK_SUMMARY)

        result = await run_compaction(
            messages,
            BudgetTier.AGGRESSIVE,
            llm,
            existing_summary="Prior context summary.",
        )

        assert result is not None
        assert isinstance(result, CompactionResult)
        assert result.stage == "aggressive_recompress"

        # Count user messages in the result — should be heavily reduced
        user_msgs = [m for m in result.messages if m.get("role") == "user"]
        assert len(user_msgs) <= 5, (
            f"Expected <=5 user messages after aggressive compaction, got {len(user_msgs)}"
        )

    @pytest.mark.asyncio
    async def test_hard_stop_builds_bridge(self):
        """HARD_STOP tier returns None from run_compaction (handled by session manager).

        However, build_hard_stop_bridge() is tested separately here.
        """
        # build_hard_stop_bridge is a synchronous heuristic — no LLM
        messages: list[dict] = []
        for i in range(10):
            messages.append({"role": "user", "content": f"Message {i + 1}? What about this?"})
            messages.append({"role": "assistant", "content": f"Reply {i + 1}"})

        bridge = build_hard_stop_bridge(messages, session_id="sess-test-001")

        assert isinstance(bridge, SessionBridge)
        assert bridge.session_id == "sess-test-001"
        assert isinstance(bridge.summary, str)
        assert len(bridge.summary) > 0
        assert isinstance(bridge.open_threads, list)
        assert isinstance(bridge.emotional_trajectory, str)

        # HARD_STOP itself: run_compaction should return None (caller handles it)
        llm = _make_mock_llm("irrelevant")
        result = await run_compaction(messages, BudgetTier.HARD_STOP, llm)
        assert result is None
