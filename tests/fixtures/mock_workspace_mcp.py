"""Fake MCP manager for workspace capability tests."""
from __future__ import annotations

from typing import Any

from kora_v2.mcp.results import MCPContentBlock, MCPToolResult

# Default tool list — mimics taylorwilsdon/google_workspace_mcp discovery.
_DEFAULT_TOOLS: list[str] = [
    "search_gmail_messages",
    "get_gmail_message",
    "create_gmail_draft",
    "send_gmail_message",
    "get_events",
    "manage_event",
    "search_drive_files",
    "get_drive_file_content",
    "upload_drive_file",
    "get_docs_content",
    "create_doc",
    "update_doc",
    "list_tasks",
    "create_task",
]


class MockWorkspaceMCPManager:
    """Fake MCP manager that returns canned MCPToolResult for workspace tool names."""

    def __init__(self, tools: list[str] | None = None) -> None:
        self.calls: list[tuple[str, str, dict]] = []
        self._responses: dict[str, MCPToolResult] = {}
        self._tools = tools if tools is not None else list(_DEFAULT_TOOLS)

    def set_response(self, tool_name: str, data: dict[str, Any]) -> None:
        """Register a canned structured-data response for a tool."""
        import json

        result = MCPToolResult(
            server="workspace",
            tool=tool_name,
            is_error=False,
            content=[MCPContentBlock(type="text", text=json.dumps(data))],
            raw={"content": [{"type": "text", "text": json.dumps(data)}]},
        )
        self._responses[tool_name] = result

    def set_error(self, tool_name: str, message: str) -> None:
        """Register a canned error response for a tool."""
        result = MCPToolResult(
            server="workspace",
            tool=tool_name,
            is_error=True,
            content=[MCPContentBlock(type="text", text=message)],
            raw={"isError": True, "content": [{"type": "text", "text": message}]},
        )
        self._responses[tool_name] = result

    async def call_tool(
        self,
        server: str,
        tool: str,
        args: dict[str, Any] | None = None,
    ) -> MCPToolResult:
        """Record the call and return a canned MCPToolResult."""
        self.calls.append((server, tool, dict(args or {})))

        if tool in self._responses:
            return self._responses[tool]

        # Default: return an empty success result
        return MCPToolResult(
            server=server,
            tool=tool,
            is_error=False,
            content=[MCPContentBlock(type="text", text="{}")],
            raw={"content": [{"type": "text", "text": "{}"}]},
        )

    async def list_tools(self, server: str) -> list[dict[str, Any]]:
        """Return the configured tool list as MCP-style dicts."""
        return [{"name": t} for t in self._tools]

    async def discover_tools(self, server: str) -> list[str]:
        """Return the list of tool names (compatible with MCPManager.discover_tools)."""
        return list(self._tools)

    def get_server_info(self, server: str) -> Any:
        """Return a fake server info object that appears RUNNING."""
        from kora_v2.mcp.manager import MCPServerInfo, MCPServerState

        info = MCPServerInfo(name=server, state=MCPServerState.RUNNING)
        info.tools = list(self._tools)
        return info

    async def ensure_server_running(self, name: str) -> None:
        """No-op — the mock is always 'running'."""
