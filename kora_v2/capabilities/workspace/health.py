"""Workspace health checks."""
from __future__ import annotations

import structlog

from kora_v2.capabilities.base import CapabilityHealth, HealthStatus
from kora_v2.capabilities.workspace.config import WorkspaceConfig
from kora_v2.core.settings import Settings
from kora_v2.mcp.manager import MCPManager, MCPServerState

log = structlog.get_logger(__name__)


async def check_workspace_health(
    config: WorkspaceConfig,
    settings: Settings,
    mcp_manager: MCPManager | None,
) -> CapabilityHealth:
    """Return a CapabilityHealth describing the workspace integration state.

    Behavior:
    - If no MCP server with config.mcp_server_name is configured → UNCONFIGURED
    - If mcp_manager is None → UNCONFIGURED
    - If server is configured but not yet started → DEGRADED (lazy-start is fine)
    - If server is running, list its tools and report which configured action names
      in config.tool_map resolve to discovered tool names → OK if all discovered,
      DEGRADED if some missing, UNHEALTHY if none.
    - Never raises. On unexpected errors return UNHEALTHY with the error in details.
    """
    server_name = config.mcp_server_name

    try:
        # Check MCP manager availability
        if mcp_manager is None:
            return CapabilityHealth(
                status=HealthStatus.UNCONFIGURED,
                summary="Workspace MCP manager is not initialized.",
                remediation=(
                    "Call container.initialize_workers() or bind the capability "
                    "before checking health."
                ),
            )

        # Check server is configured
        if server_name not in settings.mcp.servers:
            return CapabilityHealth(
                status=HealthStatus.UNCONFIGURED,
                summary=f"No MCP server named '{server_name}' is configured.",
                remediation=(
                    f"Add a [mcp.servers.{server_name}] entry to ~/.kora/settings.toml "
                    "pointing to the google_workspace_mcp binary."
                ),
            )

        # Check server runtime state
        server_info = mcp_manager.get_server_info(server_name)
        if server_info is None:
            return CapabilityHealth(
                status=HealthStatus.UNCONFIGURED,
                summary=f"MCP server '{server_name}' is configured but not tracked by the manager.",
                remediation="Ensure the server is enabled in MCP settings.",
            )

        if server_info.state != MCPServerState.RUNNING:
            return CapabilityHealth(
                status=HealthStatus.DEGRADED,
                summary=(
                    f"MCP server '{server_name}' is not running "
                    f"(state: {server_info.state}). Lazy-start will attempt on first use."
                ),
                details={
                    "state": server_info.state,
                    "last_error": server_info.last_error,
                    "discoverable": False,
                },
                remediation=(
                    "The server will start automatically on first tool call. "
                    "If it remains in this state, check the server binary and credentials."
                ),
            )

        # Server is running — check tool discovery
        try:
            discovered_tools = await mcp_manager.discover_tools(server_name)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "workspace.health.discover_tools_failed",
                server=server_name,
                error=str(exc),
            )
            return CapabilityHealth(
                status=HealthStatus.UNHEALTHY,
                summary=f"MCP server '{server_name}' is running but tool discovery failed.",
                details={"error": str(exc)},
                remediation="Inspect the MCP server logs for errors.",
            )

        discovered_set = set(discovered_tools)
        expected_mcp_tools = set(config.tool_map.values())
        missing_tools = expected_mcp_tools - discovered_set
        found_tools = expected_mcp_tools & discovered_set

        if not found_tools:
            return CapabilityHealth(
                status=HealthStatus.UNHEALTHY,
                summary=(
                    f"MCP server '{server_name}' is running but none of the expected "
                    "Workspace tools were discovered."
                ),
                details={
                    "expected": sorted(expected_mcp_tools),
                    "discovered": sorted(discovered_set),
                    "missing": sorted(missing_tools),
                },
                remediation=(
                    "Verify the MCP server is the correct google_workspace_mcp build "
                    "or update tool_map in WorkspaceConfig to match the server's tool names."
                ),
            )

        if missing_tools:
            return CapabilityHealth(
                status=HealthStatus.DEGRADED,
                summary=(
                    f"MCP server '{server_name}' is running but {len(missing_tools)} "
                    f"of {len(expected_mcp_tools)} expected tools are missing."
                ),
                details={
                    "found_count": len(found_tools),
                    "missing_count": len(missing_tools),
                    "missing": sorted(missing_tools),
                    "discovered": sorted(discovered_set),
                },
                remediation=(
                    "Some actions will be unavailable. Update tool_map in WorkspaceConfig "
                    "or upgrade the MCP server to expose the missing tools."
                ),
            )

        return CapabilityHealth(
            status=HealthStatus.OK,
            summary=(
                f"Workspace MCP server '{server_name}' is running with all "
                f"{len(expected_mcp_tools)} expected tools discovered."
            ),
            details={
                "server": server_name,
                "tool_count": len(discovered_set),
                "expected_count": len(expected_mcp_tools),
            },
        )

    except Exception as exc:  # noqa: BLE001
        log.exception("workspace.health.unexpected_error", error=str(exc))
        return CapabilityHealth(
            status=HealthStatus.UNHEALTHY,
            summary="Unexpected error during workspace health check.",
            details={"error": str(exc)},
            remediation="Check Kora daemon logs for the full traceback.",
        )
