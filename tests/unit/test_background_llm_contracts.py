"""Background handler LLM provider contract tests."""

from __future__ import annotations


class _GenerateOnlyProvider:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def generate(self, messages, **kwargs):  # noqa: ANN001, ANN202
        self.calls.append({"messages": messages, **kwargs})
        return {"content": "ok"}


class _Container:
    def __init__(self) -> None:
        self.llm = _GenerateOnlyProvider()


async def test_vault_organizer_llm_call_uses_generate_provider_contract() -> None:
    from kora_v2.agents.background.vault_organizer_handlers import _llm_call

    container = _Container()

    response = await _llm_call(container, "system", "user")

    assert response == "ok"
    assert container.llm.calls[0]["system_prompt"] == "system"


async def test_proactive_llm_call_uses_generate_provider_contract() -> None:
    from kora_v2.agents.background.proactive_handlers import _llm_call

    container = _Container()

    response = await _llm_call(container, "system", "user")

    assert response == "ok"
    assert container.llm.calls[0]["system_prompt"] == "system"


def test_proactive_research_goal_coverage_only_requires_terms_in_goal() -> None:
    from kora_v2.agents.background.proactive_handlers import (
        _ensure_research_goal_coverage,
    )

    report = _ensure_research_goal_coverage(
        "## Summary\n\nThis is about a user profile.",
        "Research local-first productivity tools for privacy-focused notes.",
    )

    lower = report.lower()
    assert "goal coverage check" in lower
    assert "local-first" in lower
    assert "privacy" in lower
    assert "obsidian" not in lower
    assert "logseq" not in lower
    assert "anytype" not in lower


def test_proactive_research_working_doc_report_demotes_top_level_headings() -> None:
    from kora_v2.agents.background.proactive_handlers import (
        _normalize_research_report_for_working_doc,
    )

    report = _normalize_research_report_for_working_doc(
        "# Research Document\n\n## Findings\n\n- useful"
    )

    assert report.startswith("## Research Document")
    assert "\n## Findings" in report
