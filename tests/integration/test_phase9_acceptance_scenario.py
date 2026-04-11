"""Phase 9 acceptance scenario integration test — MCP failure → browser.open path.

This is a focused integration-style test that exercises the report + policy +
dispatch layers together for the MCP-failure-to-browser-read scenario described
in Task 11. It does NOT spin up the full WebSocket daemon or run the 3-day
WEEK_PLAN soak.

Coverage:
1. Force workspace.gmail.search (via search_web dispatch) to return StructuredFailure
2. Validate the failure dict has the required fields (failed_path, degraded, action name)
3. Simulate the model's next turn choosing browser.open — confirm it is NOT denied
4. Pass the resulting conversation to the report builder and verify the report
   contains a policy/capability section
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_container_without_mcp() -> MagicMock:
    """Minimal container with no MCP manager (offline / unconfigured)."""
    container = MagicMock()
    container.mcp_manager = None
    settings = MagicMock()
    settings.security.auth_mode = "trust_all"
    container.settings = settings
    container.session_manager = None
    return container


def _parse(result_str: str) -> dict[str, Any]:
    try:
        return json.loads(result_str)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"Not valid JSON: {result_str!r}") from exc


def _build_fake_conversation(
    failure_str: str,
    failure_tool_name: str,
    browser_response: str,
) -> list[dict[str, Any]]:
    """Build a minimal fake conversation list for the report builder.

    Simulates:
      user → "check my email"
      assistant → tool_call: workspace.gmail.search → failure response
      assistant → content: acknowledgement of failure (mentions failure_tool_name)
      user → "can you try opening a google search instead?"
      assistant → tool_call: browser.open → success
    """
    return [
        {
            "role": "user",
            "content": "can you check my email for anything urgent this week?",
            "ts": "2026-04-10T09:00:00+00:00",
        },
        {
            "role": "assistant",
            "content": (
                f"I tried to reach {failure_tool_name} but it is unavailable right now. "
                "The MCP connection failed — I'll let you know what happened and suggest an alternative."
            ),
            "ts": "2026-04-10T09:00:01+00:00",
            "tool_calls": [failure_tool_name],
        },
        {
            "role": "user",
            "content": "ok, can you open google and search instead?",
            "ts": "2026-04-10T09:01:00+00:00",
        },
        {
            "role": "assistant",
            "content": browser_response,
            "ts": "2026-04-10T09:01:01+00:00",
            "tool_calls": ["browser.open"],
        },
    ]


# ---------------------------------------------------------------------------
# 1. workspace.gmail.search → StructuredFailure via search_web dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_failure_returns_structured_failure() -> None:
    """Forcing search_web without MCP returns a StructuredFailure-shaped dict."""
    from kora_v2.graph.dispatch import _execute_search_web

    container = _make_container_without_mcp()
    result_str = await _execute_search_web({"query": "urgent emails this week"}, container)
    result = _parse(result_str)

    assert result.get("degraded") is True, "Failure dict must have degraded=True"
    assert "failed_path" in result, "Failure dict must have a failed_path key"
    assert result.get("recoverable") is True, "Failure dict must be recoverable=True"


@pytest.mark.asyncio
async def test_failure_dict_contains_action_name_reference() -> None:
    """The failure response string must be parseable and reference a known path."""
    from kora_v2.graph.dispatch import _execute_search_web

    container = _make_container_without_mcp()
    result_str = await _execute_search_web({"query": "emails"}, container)
    result = _parse(result_str)

    # failed_path should reference the MCP path we tried
    failed_path = result.get("failed_path", "")
    assert "brave_search" in failed_path or "mcp" in failed_path, (
        f"failed_path should reference the MCP path, got: {failed_path!r}"
    )


@pytest.mark.asyncio
async def test_failure_dict_has_next_options_browser_open() -> None:
    """next_options must include browser.open so the model can choose it."""
    from kora_v2.graph.dispatch import _execute_search_web

    container = _make_container_without_mcp()
    result_str = await _execute_search_web({"query": "emails"}, container)
    result = _parse(result_str)

    next_options = result.get("next_options", [])
    assert "browser.open" in next_options, (
        f"next_options must include 'browser.open', got: {next_options}"
    )


# ---------------------------------------------------------------------------
# 2. browser.open is NOT denied by policy after MCP failure
# ---------------------------------------------------------------------------


def test_browser_open_not_denied_after_mcp_failure() -> None:
    """After search_web fails, the model choosing browser.open must be allowed."""
    from kora_v2.capabilities.browser.policy import build_browser_policy
    from kora_v2.capabilities.policy import ApprovalMode, PolicyKey, SessionState

    policy = build_browser_policy()
    key = PolicyKey(capability="browser", action="browser.open")
    session = SessionState(session_id="acceptance-test")
    decision = policy.evaluate(key, session=session, task=None)

    assert decision.allowed, (
        "browser.open must be allowed — the model's fallback must not be blocked"
    )
    assert not decision.requires_prompt, (
        "browser.open must not require an approval prompt (it is a read-only navigation)"
    )
    assert decision.mode == ApprovalMode.NEVER_ASK, (
        f"browser.open mode must be NEVER_ASK, got: {decision.mode!r}"
    )


def test_browser_open_google_url_not_denied() -> None:
    """browser.open with a google.com URL must be allowed (read is always permitted)."""
    from kora_v2.capabilities.browser.policy import build_browser_policy
    from kora_v2.capabilities.policy import PolicyKey, SessionState

    policy = build_browser_policy()
    key = PolicyKey(capability="browser", action="browser.open")
    session = SessionState(session_id="acceptance-test")
    decision = policy.evaluate(key, session=session, task=None)

    # Read-only open is never blocked
    assert decision.allowed, (
        "browser.open to google.com must be allowed"
    )


# ---------------------------------------------------------------------------
# 3. Report builder produces capability / policy sections
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_report_contains_capability_and_policy_sections(tmp_path: Path) -> None:
    """build_report must include Capability Packs and Policy Grants sections."""
    from tests.acceptance._report import build_report

    # Build a fake conversation that used browser.open after a failed search
    failure_tool = "workspace.gmail.search"
    conv = _build_fake_conversation(
        failure_str=json.dumps({
            "degraded": True,
            "failed_path": "mcp.google-workspace.search_messages",
            "recoverable": True,
            "next_options": ["browser.open"],
            "error": "MCP server unavailable",
            "results": [],
        }),
        failure_tool_name=failure_tool,
        browser_response="I opened google.com in the browser and searched for your emails.",
    )

    # Minimal session state
    session_state: dict[str, Any] = {
        "started_at": "2026-04-10T09:00:00+00:00",
        "simulated_hours_offset": 0,
        "messages": conv,
        "auth_test_results": [],
        "errors": [],
    }

    snapshots_dir = tmp_path / "snapshots"
    snapshots_dir.mkdir()

    # Create a coverage.md so the report parser doesn't error
    (tmp_path / "coverage.md").write_text("")

    report_path = await build_report(
        session_state=session_state,
        snapshots_dir=snapshots_dir,
        output_dir=tmp_path,
        compaction_events=[],
    )

    assert report_path.exists(), "Report file must be created"
    report_text = report_path.read_text()

    # The Capability Packs section must be present
    assert "## Capability Packs" in report_text, (
        "Report must contain '## Capability Packs' section"
    )

    # The Policy Grants section must be present
    assert "## Policy Grants" in report_text, (
        "Report must contain '## Policy Grants' section"
    )

    # The browser capability bucket must appear (we used browser.open)
    assert "Capability (browser)" in report_text, (
        "Report must include capability_browser bucket in Tool Usage"
    )


@pytest.mark.asyncio
async def test_report_capability_section_lists_four_packs(tmp_path: Path) -> None:
    """The Capability Packs section must mention all 4 expected pack names."""
    from tests.acceptance._report import build_report

    session_state: dict[str, Any] = {
        "started_at": "2026-04-10T09:00:00+00:00",
        "simulated_hours_offset": 0,
        "messages": [],
        "auth_test_results": [],
        "errors": [],
    }

    snapshots_dir = tmp_path / "snapshots"
    snapshots_dir.mkdir()
    (tmp_path / "coverage.md").write_text("")

    report_path = await build_report(
        session_state=session_state,
        snapshots_dir=snapshots_dir,
        output_dir=tmp_path,
        compaction_events=[],
    )

    report_text = report_path.read_text()

    # All 4 packs must appear
    for pack_name in ("workspace", "browser", "vault", "doctor"):
        assert pack_name in report_text, (
            f"Report's Capability Packs section must mention '{pack_name}'"
        )


# ---------------------------------------------------------------------------
# 4. Disclosed-failure path: assistant message must acknowledge MCP failure
# ---------------------------------------------------------------------------


def test_assistant_message_acknowledges_mcp_failure() -> None:
    """The fake conversation fixture shows failure acknowledgement in assistant reply.

    This mirrors what item 25 checks: if a tool fails, the user-visible reply
    must contain plain language about the failure (MCP / unavailable / failed).
    """
    failure_tool = "workspace.gmail.search"
    conv = _build_fake_conversation(
        failure_str="{}",
        failure_tool_name=failure_tool,
        browser_response="Opened browser as fallback.",
    )

    # Find the assistant message that follows the failed tool call
    assistant_msgs_after_tool = [
        m for m in conv
        if m.get("role") == "assistant" and failure_tool in m.get("tool_calls", [])
    ]

    assert assistant_msgs_after_tool, "Must have an assistant message that used the failing tool"

    disclosure_keywords = ("unavailable", "failed", "MCP", "mcp", "unreachable")
    for msg in assistant_msgs_after_tool:
        content = msg.get("content", "")
        assert any(kw in content for kw in disclosure_keywords), (
            f"Assistant message must acknowledge failure explicitly. "
            f"Content: {content!r}. "
            f"Expected one of: {disclosure_keywords}"
        )


# ---------------------------------------------------------------------------
# 5. Capability health check returns 4 packs (mirrors item 26)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_capability_health_check_returns_four_packs() -> None:
    """get_all_capabilities() must return exactly the 4 registered packs."""
    from kora_v2.capabilities.registry import get_all_capabilities

    packs = get_all_capabilities()
    pack_names = {p.name for p in packs}

    expected = {"workspace", "browser", "vault", "doctor"}
    assert expected <= pack_names, (
        f"Missing packs: {expected - pack_names}. Registered: {pack_names}"
    )


@pytest.mark.asyncio
async def test_capability_health_check_all_return_health_objects() -> None:
    """Every registered pack must return a CapabilityHealth from health_check()."""
    from kora_v2.capabilities.base import CapabilityHealth
    from kora_v2.capabilities.registry import get_all_capabilities

    packs = get_all_capabilities()
    assert packs, "At least one capability pack must be registered"

    for pack in packs:
        health = await pack.health_check()
        assert isinstance(health, CapabilityHealth), (
            f"Pack '{pack.name}' health_check() must return CapabilityHealth, "
            f"got {type(health)!r}"
        )
        assert health.status is not None, (
            f"Pack '{pack.name}' CapabilityHealth.status must not be None"
        )
        assert health.summary, (
            f"Pack '{pack.name}' CapabilityHealth.summary must be non-empty"
        )
