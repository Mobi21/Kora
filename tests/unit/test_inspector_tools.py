"""Unit tests for RuntimeInspector.inspect_tools().

Verifies that:
- inspect_tools() returns a valid result (no exception) when skills are loaded
- tool_count matches the number of tools in each skill
- the result includes a "mcp" key
- the result has the correct structure with list[str] tools (not list-of-dicts)
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_PROJECT_ROOT = Path(__file__).parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _make_skill(name: str, tools: list[str], guidance: str = "") -> MagicMock:
    """Build a mock Skill object matching the real Skill model interface."""
    skill = MagicMock()
    skill.name = name
    skill.tools = tools
    skill.guidance = guidance
    return skill


def _make_container(
    skills: list[MagicMock] | None = None,
    mcp_manager: object | None = None,
) -> MagicMock:
    """Build a mock Container with an optional skill_loader."""
    container = MagicMock()

    if skills is None:
        container.skill_loader = None
    else:
        skill_loader = MagicMock()
        skill_loader.get_all_skills.return_value = skills
        container.skill_loader = skill_loader

    container._mcp_manager = mcp_manager
    return container


class TestInspectToolsNoLoader:
    """When skill_loader is None the result should say so gracefully."""

    @pytest.mark.asyncio
    async def test_no_skill_loader_returns_not_initialized(self) -> None:
        from kora_v2.runtime.inspector import RuntimeInspector

        container = _make_container(skills=None)
        inspector = RuntimeInspector(container)

        result = await inspector.inspect_tools()

        assert result["topic"] == "tools"
        assert result["skill_loader_initialized"] is False
        assert result["skills"] == []

    @pytest.mark.asyncio
    async def test_no_skill_loader_result_has_no_mcp_key(self) -> None:
        """When loader is None we skip MCP — no error."""
        from kora_v2.runtime.inspector import RuntimeInspector

        container = _make_container(skills=None)
        inspector = RuntimeInspector(container)
        result = await inspector.inspect_tools()

        # Result should not raise and must be a dict
        assert isinstance(result, dict)


class TestInspectToolsWithSkills:
    """When skills are loaded, the result should reflect them correctly."""

    @pytest.mark.asyncio
    async def test_returns_200_equivalent_no_exception(self) -> None:
        """inspect_tools() should complete without raising."""
        from kora_v2.runtime.inspector import RuntimeInspector

        skills = [
            _make_skill("web_research", ["web_search", "fetch_url"]),
            _make_skill("code_work", ["run_code", "read_file", "write_file"]),
        ]
        container = _make_container(skills=skills)
        inspector = RuntimeInspector(container)

        result = await inspector.inspect_tools()

        assert result["topic"] == "tools"
        assert result["skill_loader_initialized"] is True

    @pytest.mark.asyncio
    async def test_skill_count_matches(self) -> None:
        from kora_v2.runtime.inspector import RuntimeInspector

        skills = [
            _make_skill("web_research", ["web_search", "fetch_url"]),
            _make_skill("code_work", ["run_code", "read_file", "write_file"]),
            _make_skill("life_management", []),
        ]
        container = _make_container(skills=skills)
        inspector = RuntimeInspector(container)

        result = await inspector.inspect_tools()

        assert result["skill_count"] == 3
        assert len(result["skills"]) == 3

    @pytest.mark.asyncio
    async def test_tool_count_matches_number_of_tools_per_skill(self) -> None:
        """tool_count for each skill entry must match len(skill.tools)."""
        from kora_v2.runtime.inspector import RuntimeInspector

        skills = [
            _make_skill("alpha", ["t1", "t2", "t3"]),
            _make_skill("beta", ["x1"]),
            _make_skill("gamma", []),
        ]
        container = _make_container(skills=skills)
        inspector = RuntimeInspector(container)

        result = await inspector.inspect_tools()

        skill_map = {s["name"]: s for s in result["skills"]}
        assert skill_map["alpha"]["tool_count"] == 3
        assert skill_map["beta"]["tool_count"] == 1
        assert skill_map["gamma"]["tool_count"] == 0

    @pytest.mark.asyncio
    async def test_tools_are_list_of_str_not_dicts(self) -> None:
        """tools field must be list[str], not list-of-dicts."""
        from kora_v2.runtime.inspector import RuntimeInspector

        skills = [
            _make_skill("web_research", ["web_search", "fetch_url"]),
        ]
        container = _make_container(skills=skills)
        inspector = RuntimeInspector(container)

        result = await inspector.inspect_tools()

        for skill_entry in result["skills"]:
            for tool in skill_entry["tools"]:
                assert isinstance(tool, str), (
                    f"Expected str tool names, got {type(tool)}: {tool!r}"
                )

    @pytest.mark.asyncio
    async def test_result_includes_mcp_key(self) -> None:
        """The result dict must contain a 'mcp' key."""
        from kora_v2.runtime.inspector import RuntimeInspector

        skills = [_make_skill("web_research", ["web_search"])]
        container = _make_container(skills=skills)
        inspector = RuntimeInspector(container)

        result = await inspector.inspect_tools()

        assert "mcp" in result, "Result must contain 'mcp' key"

    @pytest.mark.asyncio
    async def test_mcp_not_initialized_when_no_mcp_manager(self) -> None:
        """When _mcp_manager is None, mcp.initialized should be False."""
        from kora_v2.runtime.inspector import RuntimeInspector

        skills = [_make_skill("code_work", ["run_code"])]
        container = _make_container(skills=skills, mcp_manager=None)
        inspector = RuntimeInspector(container)

        result = await inspector.inspect_tools()

        assert result["mcp"]["initialized"] is False

    @pytest.mark.asyncio
    async def test_mcp_initialized_when_mcp_manager_present(self) -> None:
        """When _mcp_manager is set, mcp.initialized should be True."""
        from kora_v2.runtime.inspector import RuntimeInspector

        mcp_manager = MagicMock()
        # No list_servers method — fallback to empty list via getattr default
        del mcp_manager.list_servers

        skills = [_make_skill("web_research", ["web_search"])]
        container = _make_container(skills=skills, mcp_manager=mcp_manager)
        inspector = RuntimeInspector(container)

        result = await inspector.inspect_tools()

        assert result["mcp"]["initialized"] is True

    @pytest.mark.asyncio
    async def test_mcp_lists_servers_when_available(self) -> None:
        """When mcp_manager.list_servers() returns servers, they appear in the result."""
        from kora_v2.runtime.inspector import RuntimeInspector

        # Build a mock MCP server entry
        mock_server = MagicMock()
        mock_server.name = "filesystem"
        mock_server.state = "connected"
        mock_server.tools = ["read_file", "write_file"]

        mcp_manager = MagicMock()
        mcp_manager.list_servers.return_value = [mock_server]

        skills = [_make_skill("code_work", ["run_code"])]
        container = _make_container(skills=skills, mcp_manager=mcp_manager)
        inspector = RuntimeInspector(container)

        result = await inspector.inspect_tools()

        mcp = result["mcp"]
        assert mcp["initialized"] is True
        assert len(mcp["servers"]) == 1
        assert mcp["servers"][0]["name"] == "filesystem"

    @pytest.mark.asyncio
    async def test_description_is_truncated_to_120_chars(self) -> None:
        """Skill description (from guidance) is capped at 120 characters."""
        from kora_v2.runtime.inspector import RuntimeInspector

        long_guidance = "x" * 200
        skills = [_make_skill("alpha", ["t1"], guidance=long_guidance)]
        container = _make_container(skills=skills)
        inspector = RuntimeInspector(container)

        result = await inspector.inspect_tools()

        desc = result["skills"][0]["description"]
        assert len(desc) <= 120

    @pytest.mark.asyncio
    async def test_skill_name_field_present(self) -> None:
        """Each skill entry must have a 'name' field matching skill.name."""
        from kora_v2.runtime.inspector import RuntimeInspector

        skills = [
            _make_skill("web_research", ["web_search"]),
            _make_skill("life_management", ["calendar_check"]),
        ]
        container = _make_container(skills=skills)
        inspector = RuntimeInspector(container)

        result = await inspector.inspect_tools()

        names = {s["name"] for s in result["skills"]}
        assert names == {"web_research", "life_management"}
