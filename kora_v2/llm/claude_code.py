"""Claude Code subprocess delegate for code-heavy autonomous work."""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any, Literal

import structlog
from pydantic import BaseModel, Field

logger = structlog.get_logger()


# ── Models ────────────────────────────────────────────────────────────────


class DelegationBrief(BaseModel):
    """Structured brief sent to the Claude Code subprocess."""

    goal: str
    target_files: list[str] = []
    target_dirs: list[str] = []
    allowed_tools: list[str] = []          # e.g. ["Read", "Write", "Bash"]
    forbidden_actions: list[str] = []      # e.g. ["git push", "rm -rf"]
    expected_deliverables: list[str] = []
    validation_steps: list[str] = []
    budget_limits: dict[str, Any] = Field(default_factory=dict)  # max_tokens, max_cost, etc.
    stop_conditions: list[str] = []
    context: str = ""                      # extra context the delegate needs


class DelegateOutput(BaseModel):
    """Structured output returned from Claude Code delegate."""

    summary: str
    files_touched: list[str] = []
    tests_run: list[str] = []
    validation_result: Literal["passed", "failed", "skipped"] = "skipped"
    remaining_risks: list[str] = []
    patch_references: list[str] = []   # file paths of diffs/patches
    exit_code: int = 0
    raw_output: str = ""
    error: str | None = None


class DelegateFailure(BaseModel):
    """Classified failure from a delegate invocation."""

    category: Literal[
        "missing_binary",
        "timeout",
        "nonzero_exit",
        "malformed_output",
        "validation_failure",
        "policy_violation",
        "budget_exhaustion",
    ]
    message: str
    exit_code: int | None = None
    raw_output: str = ""


class DelegateResult(BaseModel):
    """Final result after all attempts."""

    success: bool
    output: DelegateOutput | None = None
    failure: DelegateFailure | None = None
    attempts: int = 0
    fell_back_to_local: bool = False


# ── Delegate ──────────────────────────────────────────────────────────────


class ClaudeCodeDelegate:
    """Manages scoped Claude Code subprocess invocations."""

    def __init__(
        self,
        claude_binary: str = "claude",
        default_timeout: int = 300,
        max_output_bytes: int = 1_000_000,
    ) -> None:
        self._binary = claude_binary
        self._timeout = default_timeout
        self._max_output_bytes = max_output_bytes

    # ── Public ────────────────────────────────────────────────────────

    async def delegate(
        self,
        brief: DelegationBrief,
        working_dir: Path | None = None,
    ) -> DelegateResult:
        """Execute a delegation brief as a Claude Code subprocess.

        Retries once with a narrower brief on failure.
        Returns fell_back_to_local=True if both attempts fail.
        """
        log = logger.bind(goal=brief.goal[:80])

        # First attempt
        output, failure = await self._run_once(brief, working_dir)
        if output is not None:
            log.info("claude_code_delegate.success", attempt=1)
            return DelegateResult(success=True, output=output, attempts=1)

        log.warning(
            "claude_code_delegate.attempt_failed",
            attempt=1,
            category=failure.category if failure else "unknown",
        )

        # Do not retry on missing binary — it will fail again
        if failure and failure.category == "missing_binary":
            return DelegateResult(
                success=False,
                failure=failure,
                attempts=1,
                fell_back_to_local=True,
            )

        # Second attempt with narrower brief
        narrowed = self._narrow_brief(brief)
        output2, failure2 = await self._run_once(narrowed, working_dir)
        if output2 is not None:
            log.info("claude_code_delegate.success", attempt=2)
            return DelegateResult(success=True, output=output2, attempts=2)

        log.error(
            "claude_code_delegate.both_attempts_failed",
            first_category=failure.category if failure else "unknown",
            second_category=failure2.category if failure2 else "unknown",
        )
        return DelegateResult(
            success=False,
            failure=failure2 or failure,
            attempts=2,
            fell_back_to_local=True,
        )

    async def is_available(self) -> bool:
        """Check if the claude binary exists on PATH.

        Uses shutil.which() rather than spawning a subprocess — avoids
        blocking the event loop for up to 10 seconds on slow PATH lookups.
        """
        import shutil
        return shutil.which(self._binary) is not None

    # ── Internal ──────────────────────────────────────────────────────

    async def _run_once(
        self,
        brief: DelegationBrief,
        working_dir: Path | None,
    ) -> tuple[DelegateOutput | None, DelegateFailure | None]:
        """Single subprocess invocation. Returns (output, None) on success or (None, failure)."""
        prompt = self._build_prompt(brief)
        prompt_bytes = prompt.encode()

        # Build command
        cmd: list[str] = [self._binary, "--print"]
        if brief.allowed_tools:
            cmd += ["--allowedTools", ",".join(brief.allowed_tools)]

        log = logger.bind(cmd=cmd[0], goal=brief.goal[:60])
        log.debug("claude_code_delegate.starting_subprocess")

        timed_out = False
        stdout_raw = b""
        stderr_raw = b""
        returncode: int | None = None

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(working_dir) if working_dir else None,
            )

            async def _communicate() -> tuple[bytes, bytes]:
                return await process.communicate(input=prompt_bytes)

            try:
                stdout_raw, stderr_raw = await asyncio.wait_for(
                    _communicate(),
                    timeout=self._timeout,
                )
                returncode = process.returncode
            except TimeoutError:
                timed_out = True
                try:
                    process.kill()
                    await process.wait()
                except Exception:
                    pass
                returncode = process.returncode

        except FileNotFoundError:
            log.warning("claude_code_delegate.binary_not_found", binary=self._binary)
            return None, DelegateFailure(
                category="missing_binary",
                message=f"claude binary not found: {self._binary}",
            )
        except Exception as exc:
            log.error("claude_code_delegate.subprocess_error", error=str(exc))
            return None, DelegateFailure(
                category="nonzero_exit",
                message=f"Subprocess error: {exc}",
            )

        # Decode and truncate
        stdout = stdout_raw[: self._max_output_bytes].decode("utf-8", errors="replace")
        stderr = stderr_raw[:4096].decode("utf-8", errors="replace")

        if timed_out:
            failure = self._classify_failure(
                returncode or -1, stdout, stderr, timed_out=True
            )
            return None, failure

        if returncode != 0:
            failure = self._classify_failure(returncode or -1, stdout, stderr, timed_out=False)
            return None, failure

        # Attempt to parse output
        try:
            parsed = self._parse_output(stdout)
            parsed = parsed.model_copy(update={"exit_code": returncode or 0, "raw_output": stdout})
            return parsed, None
        except Exception as exc:
            log.warning("claude_code_delegate.parse_failed", error=str(exc))
            return None, DelegateFailure(
                category="malformed_output",
                message=f"Could not parse delegate output: {exc}",
                exit_code=returncode,
                raw_output=stdout[:2000],
            )

    def _build_prompt(self, brief: DelegationBrief) -> str:
        """Build the prompt string sent to the claude subprocess."""
        lines: list[str] = []

        lines.append(f"GOAL: {brief.goal}")

        if brief.target_files:
            lines.append(f"TARGET FILES: {', '.join(brief.target_files)}")
        else:
            lines.append("TARGET FILES: (none specified)")

        if brief.target_dirs:
            lines.append(f"TARGET DIRS: {', '.join(brief.target_dirs)}")

        lines.append(
            f"ALLOWED TOOLS: {', '.join(brief.allowed_tools) if brief.allowed_tools else '(all)'}"
        )

        if brief.forbidden_actions:
            lines.append(f"FORBIDDEN: {', '.join(brief.forbidden_actions)}")

        if brief.expected_deliverables:
            lines.append(
                f"DELIVERABLES: {', '.join(brief.expected_deliverables)}"
            )

        if brief.validation_steps:
            lines.append(
                f"VALIDATION STEPS: {', '.join(brief.validation_steps)}"
            )

        if brief.stop_conditions:
            lines.append(
                f"STOP CONDITIONS: {', '.join(brief.stop_conditions)}"
            )

        if brief.budget_limits:
            budget_str = ", ".join(f"{k}={v}" for k, v in brief.budget_limits.items())
            lines.append(f"BUDGET: {budget_str}")

        if brief.context:
            lines.append("")
            lines.append("CONTEXT:")
            lines.append(brief.context)

        lines.append("")
        lines.append(
            "Please respond with a JSON block containing: "
            "summary, files_touched, tests_run, validation_result, "
            "remaining_risks, patch_references."
        )

        return "\n".join(lines)

    def _classify_failure(
        self,
        returncode: int,
        stdout: str,
        stderr: str,
        timed_out: bool,
    ) -> DelegateFailure:
        """Classify what went wrong."""
        if timed_out:
            return DelegateFailure(
                category="timeout",
                message="Subprocess timed out",
                exit_code=returncode,
                raw_output=stdout[:2000],
            )

        combined = (stdout + stderr).lower()

        if "budget" in combined or "rate limit" in combined or "quota" in combined:
            return DelegateFailure(
                category="budget_exhaustion",
                message="Delegate exceeded budget or hit rate limit",
                exit_code=returncode,
                raw_output=stdout[:2000],
            )

        if "policy" in combined or "violation" in combined or "not allowed" in combined:
            return DelegateFailure(
                category="policy_violation",
                message="Delegate action was blocked by policy",
                exit_code=returncode,
                raw_output=stdout[:2000],
            )

        return DelegateFailure(
            category="nonzero_exit",
            message=f"Subprocess exited with code {returncode}",
            exit_code=returncode,
            raw_output=stdout[:2000],
        )

    def _narrow_brief(self, brief: DelegationBrief) -> DelegationBrief:
        """Return a narrower brief for retry: fewer target files, shorter goal."""
        # Trim goal to first sentence or 120 chars
        goal = brief.goal
        first_sentence_end = goal.find(". ")
        if first_sentence_end > 0 and first_sentence_end < 120:
            goal = goal[: first_sentence_end + 1]
        elif len(goal) > 120:
            goal = goal[:120].rstrip() + "..."

        # Keep only first 3 target files
        target_files = brief.target_files[:3]

        # Shorten context to 500 chars
        context = brief.context[:500] if brief.context else ""

        return DelegationBrief(
            goal=goal,
            target_files=target_files,
            target_dirs=brief.target_dirs,
            allowed_tools=brief.allowed_tools,
            forbidden_actions=brief.forbidden_actions,
            expected_deliverables=brief.expected_deliverables[:3],
            validation_steps=brief.validation_steps[:2],
            budget_limits=brief.budget_limits,
            stop_conditions=brief.stop_conditions,
            context=context,
        )

    def _parse_output(self, raw: str) -> DelegateOutput:
        """Parse Claude Code's text output into DelegateOutput.

        Tries to find JSON block first, falls back to text parsing.
        """
        # Look for ```json ... ``` block
        json_pattern = re.compile(r"```json\s*([\s\S]*?)```", re.IGNORECASE)
        match = json_pattern.search(raw)

        if match:
            json_text = match.group(1).strip()
            try:
                data = json.loads(json_text)
                return DelegateOutput(
                    summary=data.get("summary", ""),
                    files_touched=data.get("files_touched", []),
                    tests_run=data.get("tests_run", []),
                    validation_result=data.get("validation_result", "skipped"),
                    remaining_risks=data.get("remaining_risks", []),
                    patch_references=data.get("patch_references", []),
                    raw_output=raw,
                )
            except json.JSONDecodeError:
                pass  # fall through to text parsing

        # Fallback: try bare JSON object anywhere in the output
        bare_json_pattern = re.compile(r'\{[\s\S]*"summary"[\s\S]*\}')
        bare_match = bare_json_pattern.search(raw)
        if bare_match:
            try:
                data = json.loads(bare_match.group(0))
                return DelegateOutput(
                    summary=data.get("summary", ""),
                    files_touched=data.get("files_touched", []),
                    tests_run=data.get("tests_run", []),
                    validation_result=data.get("validation_result", "skipped"),
                    remaining_risks=data.get("remaining_risks", []),
                    patch_references=data.get("patch_references", []),
                    raw_output=raw,
                )
            except json.JSONDecodeError:
                pass

        # Final fallback: treat entire output as summary
        summary = raw.strip()[:500] if raw.strip() else "No output produced."
        return DelegateOutput(
            summary=summary,
            raw_output=raw,
        )
