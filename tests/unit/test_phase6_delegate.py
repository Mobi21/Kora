"""Tests for ClaudeCodeDelegate — Phase 6 subprocess delegation module."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kora_v2.llm.claude_code import (
    ClaudeCodeDelegate,
    DelegateFailure,
    DelegateOutput,
    DelegateResult,
    DelegationBrief,
)


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def delegate() -> ClaudeCodeDelegate:
    return ClaudeCodeDelegate(
        claude_binary="claude",
        default_timeout=30,
        max_output_bytes=1_000_000,
    )


@pytest.fixture
def simple_brief() -> DelegationBrief:
    return DelegationBrief(
        goal="Add type annotations to kora_v2/core/models.py",
        target_files=["kora_v2/core/models.py"],
        allowed_tools=["Read", "Write"],
        forbidden_actions=["git push", "rm -rf"],
        expected_deliverables=["Annotated models.py"],
        validation_steps=["Run mypy"],
        budget_limits={"max_tokens": 8000},
        context="This is a Pydantic V2 codebase.",
    )


def _make_process(
    stdout: bytes = b"",
    stderr: bytes = b"",
    returncode: int = 0,
) -> MagicMock:
    """Build a mock asyncio subprocess."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    return proc


def _json_output(**kwargs) -> bytes:
    """Return bytes with a valid JSON block in markdown."""
    payload = {
        "summary": "Done",
        "files_touched": [],
        "tests_run": [],
        "validation_result": "passed",
        "remaining_risks": [],
        "patch_references": [],
    }
    payload.update(kwargs)
    block = f"```json\n{json.dumps(payload)}\n```"
    return block.encode()


# ── Successful delegation ─────────────────────────────────────────────────


class TestSuccessfulDelegation:
    @pytest.mark.asyncio
    async def test_successful_delegation_returns_delegate_output(
        self, delegate: ClaudeCodeDelegate, simple_brief: DelegationBrief
    ):
        proc = _make_process(stdout=_json_output(summary="All done", files_touched=["models.py"]))

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await delegate.delegate(simple_brief)

        assert result.success is True
        assert result.output is not None
        assert result.output.summary == "All done"
        assert "models.py" in result.output.files_touched
        assert result.fell_back_to_local is False
        assert result.attempts == 1

    @pytest.mark.asyncio
    async def test_successful_result_includes_validation_result(
        self, delegate: ClaudeCodeDelegate, simple_brief: DelegationBrief
    ):
        proc = _make_process(
            stdout=_json_output(validation_result="passed", tests_run=["mypy"])
        )

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await delegate.delegate(simple_brief)

        assert result.success is True
        assert result.output.validation_result == "passed"
        assert "mypy" in result.output.tests_run


# ── Missing binary ────────────────────────────────────────────────────────


class TestMissingBinary:
    @pytest.mark.asyncio
    async def test_missing_binary_returns_failure(
        self, delegate: ClaudeCodeDelegate, simple_brief: DelegationBrief
    ):
        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError("No such file"),
        ):
            result = await delegate.delegate(simple_brief)

        assert result.success is False
        assert result.failure is not None
        assert result.failure.category == "missing_binary"
        assert result.fell_back_to_local is True

    @pytest.mark.asyncio
    async def test_missing_binary_does_not_retry(
        self, delegate: ClaudeCodeDelegate, simple_brief: DelegationBrief
    ):
        """Missing binary should short-circuit — only 1 attempt, no retry."""
        call_count = 0

        async def raise_fnf(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise FileNotFoundError("no claude")

        with patch("asyncio.create_subprocess_exec", side_effect=raise_fnf):
            result = await delegate.delegate(simple_brief)

        assert result.attempts == 1
        assert call_count == 1


# ── Timeout ───────────────────────────────────────────────────────────────


class TestTimeout:
    @pytest.mark.asyncio
    async def test_timeout_returns_timeout_failure(
        self, delegate: ClaudeCodeDelegate, simple_brief: DelegationBrief
    ):
        async def slow_communicate(input=None):
            await asyncio.sleep(9999)
            return b"", b""

        proc = MagicMock()
        proc.returncode = -1
        proc.communicate = slow_communicate
        proc.kill = MagicMock()
        proc.wait = AsyncMock()

        fast_delegate = ClaudeCodeDelegate(
            claude_binary="claude",
            default_timeout=0,   # instant timeout
        )

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await fast_delegate.delegate(simple_brief)

        assert result.success is False
        # At least one failure with timeout or nonzero_exit (timeout triggers retry)
        assert result.failure is not None
        assert result.failure.category in ("timeout", "nonzero_exit", "missing_binary")


# ── Non-zero exit ─────────────────────────────────────────────────────────


class TestNonzeroExit:
    @pytest.mark.asyncio
    async def test_nonzero_exit_returns_failure(
        self, delegate: ClaudeCodeDelegate, simple_brief: DelegationBrief
    ):
        proc = _make_process(stdout=b"Error occurred", stderr=b"", returncode=1)

        # Both attempts will fail with same error
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await delegate.delegate(simple_brief)

        assert result.success is False
        assert result.failure is not None
        assert result.failure.category == "nonzero_exit"
        assert result.failure.exit_code == 1

    @pytest.mark.asyncio
    async def test_nonzero_exit_failure_contains_exit_code(
        self, delegate: ClaudeCodeDelegate, simple_brief: DelegationBrief
    ):
        proc = _make_process(stdout=b"crash", returncode=2)

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await delegate.delegate(simple_brief)

        assert result.failure.exit_code == 2


# ── Retry logic ───────────────────────────────────────────────────────────


class TestRetryLogic:
    @pytest.mark.asyncio
    async def test_retry_succeeds_on_second_attempt(
        self, delegate: ClaudeCodeDelegate, simple_brief: DelegationBrief
    ):
        """First attempt fails (nonzero exit); retry succeeds."""
        fail_proc = _make_process(stdout=b"bad", returncode=1)
        ok_proc = _make_process(stdout=_json_output(summary="retry worked"))

        call_count = 0

        async def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return fail_proc if call_count == 1 else ok_proc

        with patch("asyncio.create_subprocess_exec", side_effect=side_effect):
            result = await delegate.delegate(simple_brief)

        assert result.success is True
        assert result.attempts == 2
        assert result.output.summary == "retry worked"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_both_attempts_fail_sets_fell_back_to_local(
        self, delegate: ClaudeCodeDelegate, simple_brief: DelegationBrief
    ):
        proc = _make_process(stdout=b"nope", returncode=1)

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await delegate.delegate(simple_brief)

        assert result.success is False
        assert result.fell_back_to_local is True
        assert result.attempts == 2


# ── _build_prompt ─────────────────────────────────────────────────────────


class TestBuildPrompt:
    def test_build_prompt_includes_goal(self, delegate: ClaudeCodeDelegate, simple_brief: DelegationBrief):
        prompt = delegate._build_prompt(simple_brief)
        assert "GOAL:" in prompt
        assert simple_brief.goal in prompt

    def test_build_prompt_includes_target_files(self, delegate: ClaudeCodeDelegate, simple_brief: DelegationBrief):
        prompt = delegate._build_prompt(simple_brief)
        assert "TARGET FILES:" in prompt
        assert "kora_v2/core/models.py" in prompt

    def test_build_prompt_includes_allowed_tools(self, delegate: ClaudeCodeDelegate, simple_brief: DelegationBrief):
        prompt = delegate._build_prompt(simple_brief)
        assert "ALLOWED TOOLS:" in prompt
        assert "Read" in prompt
        assert "Write" in prompt

    def test_build_prompt_includes_forbidden_actions(self, delegate: ClaudeCodeDelegate, simple_brief: DelegationBrief):
        prompt = delegate._build_prompt(simple_brief)
        assert "FORBIDDEN:" in prompt
        assert "git push" in prompt

    def test_build_prompt_includes_deliverables(self, delegate: ClaudeCodeDelegate, simple_brief: DelegationBrief):
        prompt = delegate._build_prompt(simple_brief)
        assert "DELIVERABLES:" in prompt
        assert "Annotated models.py" in prompt

    def test_build_prompt_includes_budget(self, delegate: ClaudeCodeDelegate, simple_brief: DelegationBrief):
        prompt = delegate._build_prompt(simple_brief)
        assert "BUDGET:" in prompt
        assert "max_tokens" in prompt

    def test_build_prompt_includes_context(self, delegate: ClaudeCodeDelegate, simple_brief: DelegationBrief):
        prompt = delegate._build_prompt(simple_brief)
        assert "CONTEXT:" in prompt
        assert "Pydantic V2" in prompt

    def test_build_prompt_includes_json_instruction(self, delegate: ClaudeCodeDelegate, simple_brief: DelegationBrief):
        prompt = delegate._build_prompt(simple_brief)
        assert "JSON" in prompt
        assert "summary" in prompt
        assert "files_touched" in prompt


# ── _narrow_brief ─────────────────────────────────────────────────────────


class TestNarrowBrief:
    def test_narrow_brief_reduces_target_files(self, delegate: ClaudeCodeDelegate):
        brief = DelegationBrief(
            goal="Do lots of things",
            target_files=["a.py", "b.py", "c.py", "d.py", "e.py"],
        )
        narrowed = delegate._narrow_brief(brief)
        assert len(narrowed.target_files) <= 3

    def test_narrow_brief_shortens_long_goal(self, delegate: ClaudeCodeDelegate):
        long_goal = "x" * 200
        brief = DelegationBrief(goal=long_goal)
        narrowed = delegate._narrow_brief(brief)
        assert len(narrowed.goal) <= 123  # 120 + "..."

    def test_narrow_brief_shortens_context(self, delegate: ClaudeCodeDelegate):
        brief = DelegationBrief(
            goal="Short goal",
            context="c" * 1000,
        )
        narrowed = delegate._narrow_brief(brief)
        assert len(narrowed.context) <= 500

    def test_narrow_brief_preserves_short_goal(self, delegate: ClaudeCodeDelegate):
        brief = DelegationBrief(goal="Fix the bug.")
        narrowed = delegate._narrow_brief(brief)
        assert narrowed.goal == "Fix the bug."

    def test_narrow_brief_splits_goal_at_sentence(self, delegate: ClaudeCodeDelegate):
        brief = DelegationBrief(
            goal="Fix the bug. Also do other things that are very long and detailed."
        )
        narrowed = delegate._narrow_brief(brief)
        assert narrowed.goal == "Fix the bug."


# ── _parse_output ─────────────────────────────────────────────────────────


class TestParseOutput:
    def test_parse_output_extracts_json_block(self, delegate: ClaudeCodeDelegate):
        raw = (
            "Here is my response:\n"
            "```json\n"
            '{"summary": "All done", "files_touched": ["x.py"], '
            '"tests_run": ["pytest"], "validation_result": "passed", '
            '"remaining_risks": [], "patch_references": []}\n'
            "```\n"
            "Hope that helps!"
        )
        out = delegate._parse_output(raw)
        assert out.summary == "All done"
        assert "x.py" in out.files_touched
        assert out.validation_result == "passed"

    def test_parse_output_falls_back_to_raw_text(self, delegate: ClaudeCodeDelegate):
        raw = "I completed the task successfully. No JSON available."
        out = delegate._parse_output(raw)
        assert "completed the task" in out.summary
        assert out.raw_output == raw

    def test_parse_output_handles_empty_output(self, delegate: ClaudeCodeDelegate):
        out = delegate._parse_output("")
        assert out.summary == "No output produced."

    def test_parse_output_json_block_case_insensitive(self, delegate: ClaudeCodeDelegate):
        raw = '```JSON\n{"summary": "ok", "validation_result": "skipped"}\n```'
        out = delegate._parse_output(raw)
        assert out.summary == "ok"

    def test_parse_output_remaining_risks_preserved(self, delegate: ClaudeCodeDelegate):
        payload = {
            "summary": "done",
            "files_touched": [],
            "tests_run": [],
            "validation_result": "failed",
            "remaining_risks": ["race condition in writer"],
            "patch_references": ["patches/fix.diff"],
        }
        raw = f"```json\n{json.dumps(payload)}\n```"
        out = delegate._parse_output(raw)
        assert "race condition in writer" in out.remaining_risks
        assert "patches/fix.diff" in out.patch_references


# ── is_available ──────────────────────────────────────────────────────────


class TestIsAvailable:
    @pytest.mark.asyncio
    async def test_is_available_returns_true_when_binary_exists(self, delegate: ClaudeCodeDelegate):
        # is_available() now uses shutil.which — no subprocess spawned.
        with patch("shutil.which", return_value="/usr/local/bin/claude"):
            result = await delegate.is_available()
        assert result is True

    @pytest.mark.asyncio
    async def test_is_available_returns_false_when_binary_missing(self, delegate: ClaudeCodeDelegate):
        with patch("shutil.which", return_value=None):
            result = await delegate.is_available()
        assert result is False

    @pytest.mark.asyncio
    async def test_is_available_returns_false_on_nonzero_exit(self, delegate: ClaudeCodeDelegate):
        # shutil.which-based check: nonzero exit is not applicable; binary not on PATH → False.
        with patch("shutil.which", return_value=None):
            result = await delegate.is_available()
        assert result is False


# ── _classify_failure ─────────────────────────────────────────────────────


class TestClassifyFailure:
    def test_timeout_classification(self, delegate: ClaudeCodeDelegate):
        f = delegate._classify_failure(returncode=-1, stdout="", stderr="", timed_out=True)
        assert f.category == "timeout"

    def test_budget_exhaustion_classification(self, delegate: ClaudeCodeDelegate):
        f = delegate._classify_failure(
            returncode=1, stdout="rate limit exceeded", stderr="", timed_out=False
        )
        assert f.category == "budget_exhaustion"

    def test_policy_violation_classification(self, delegate: ClaudeCodeDelegate):
        f = delegate._classify_failure(
            returncode=1, stdout="policy violation detected", stderr="", timed_out=False
        )
        assert f.category == "policy_violation"

    def test_nonzero_exit_default_classification(self, delegate: ClaudeCodeDelegate):
        f = delegate._classify_failure(
            returncode=1, stdout="something went wrong", stderr="", timed_out=False
        )
        assert f.category == "nonzero_exit"
        assert f.exit_code == 1
