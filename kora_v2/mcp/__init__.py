"""MCP (Model Context Protocol) integration for Kora V2.

Provides subprocess-based stdio MCP client support so the supervisor
and workers can call out to real MCP servers (brave_search, fetch, …).
"""

from kora_v2.mcp.manager import (
    MCPError,
    MCPManager,
    MCPServerInfo,
    MCPServerNotFoundError,
    MCPServerState,
    MCPServerUnavailableError,
    MCPToolNotFoundError,
)

__all__ = [
    "MCPError",
    "MCPManager",
    "MCPServerInfo",
    "MCPServerNotFoundError",
    "MCPServerState",
    "MCPServerUnavailableError",
    "MCPToolNotFoundError",
]
