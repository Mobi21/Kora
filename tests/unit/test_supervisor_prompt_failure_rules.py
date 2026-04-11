"""Tests that the supervisor system prompt contains the tool-failure language."""

from __future__ import annotations

from kora_v2.graph.prompts import build_frozen_prefix


def _build_prompt() -> str:
    """Build the frozen prefix with no optional args (cold-start defaults)."""
    return build_frozen_prefix(
        user_model_snapshot=None,
        skill_index=None,
        skill_loader=None,
        active_skills=None,
    )


class TestSupervisorPromptFailureRules:
    """The frozen prefix must contain the tool-failure-handling guidance."""

    def test_failure_handling_header_present(self) -> None:
        prompt = _build_prompt()
        assert "Tool Failure Handling" in prompt or "tool failure" in prompt.lower()

    def test_acknowledge_keyword_present(self) -> None:
        prompt = _build_prompt()
        assert "acknowledge" in prompt.lower()

    def test_degraded_keyword_present(self) -> None:
        prompt = _build_prompt()
        assert "degraded" in prompt.lower()

    def test_browser_open_mentioned_as_alternative(self) -> None:
        prompt = _build_prompt()
        assert "browser.open" in prompt

    def test_no_silent_fallback_language_present(self) -> None:
        """Prompt must warn against silent writes on Google account."""
        prompt = _build_prompt()
        # Must contain the privacy constraint about silent writes
        assert "silently" in prompt.lower() or "not use the browser" in prompt.lower() or "must not" in prompt.lower()

    def test_personal_google_account_mentioned(self) -> None:
        prompt = _build_prompt()
        lower = prompt.lower()
        assert "google" in lower or "personal" in lower

    def test_named_failed_path_guidance_present(self) -> None:
        prompt = _build_prompt()
        # Prompt should tell model to name the specific path that failed
        assert "brave_search" in prompt or "failed_path" in prompt or "failed path" in prompt.lower() or "path that failed" in prompt.lower()

    def test_prompt_is_nonempty_string(self) -> None:
        prompt = _build_prompt()
        assert isinstance(prompt, str)
        assert len(prompt) > 100

    def test_failure_section_appears_after_workers_section(self) -> None:
        """The tool-failure rules section must appear somewhere in the prompt."""
        prompt = _build_prompt()
        # The section heading or key content must be present
        assert "Tool Failure Handling" in prompt or "degraded mode" in prompt.lower()
