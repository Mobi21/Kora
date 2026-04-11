"""Workspace capability pack — Google Workspace via MCP."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from kora_v2.capabilities.base import CapabilityHealth, CapabilityPack, HealthStatus
from kora_v2.capabilities.policy import PolicyMatrix, SessionState, TaskState
from kora_v2.capabilities.registry import ActionRegistry
from kora_v2.capabilities.workspace.actions import (
    WorkspaceActionContext,
    calendar_create_event,
    calendar_delete_event,
    calendar_get_event,
    calendar_list,
    calendar_update_event,
    docs_create,
    docs_read,
    docs_update,
    drive_get_file,
    drive_search,
    drive_upload,
    gmail_draft,
    gmail_get_message,
    gmail_search,
    gmail_send,
    tasks_create,
    tasks_list,
)
from kora_v2.capabilities.workspace.config import WorkspaceConfig
from kora_v2.capabilities.workspace.health import check_workspace_health
from kora_v2.capabilities.workspace.policy import build_default_policy

if TYPE_CHECKING:
    from kora_v2.core.settings import Settings
    from kora_v2.mcp.manager import MCPManager


# ── Action metadata table ─────────────────────────────────────────────────────
# (action_name, description, read_only, requires_approval)
_ACTION_METADATA: list[tuple[str, str, bool, bool]] = [
    # Reads
    ("workspace.gmail.search",          "Search Gmail messages",              True,  False),
    ("workspace.gmail.get_message",     "Get a Gmail message by ID",          True,  False),
    ("workspace.calendar.list",         "List calendar events",               True,  False),
    ("workspace.calendar.get_event",    "Get a calendar event",               True,  False),
    ("workspace.drive.search",          "Search Google Drive files",          True,  False),
    ("workspace.drive.get_file",        "Get Drive file content",             True,  False),
    ("workspace.docs.read",             "Read a Google Doc",                  True,  False),
    ("workspace.tasks.list",            "List Google Tasks",                  True,  False),
    # Writes requiring approval
    ("workspace.gmail.draft",           "Create a Gmail draft",               False, True),
    ("workspace.gmail.send",            "Send a Gmail message",               False, True),
    ("workspace.calendar.create_event", "Create a calendar event",            False, True),
    ("workspace.calendar.update_event", "Update a calendar event",            False, True),
    ("workspace.calendar.delete_event", "Delete a calendar event",            False, True),
    ("workspace.drive.upload",          "Upload a file to Google Drive",      False, True),
    ("workspace.docs.create",           "Create a Google Doc",                False, True),
    ("workspace.docs.update",           "Update a Google Doc",                False, True),
    ("workspace.tasks.create",          "Create a Google Task",               False, True),
]

# Map full action names → callable coroutine functions
_ACTION_HANDLERS: dict[str, Any] = {
    "workspace.gmail.search":           gmail_search,
    "workspace.gmail.get_message":      gmail_get_message,
    "workspace.gmail.draft":            gmail_draft,
    "workspace.gmail.send":             gmail_send,
    "workspace.calendar.list":          calendar_list,
    "workspace.calendar.get_event":     calendar_get_event,
    "workspace.calendar.create_event":  calendar_create_event,
    "workspace.calendar.update_event":  calendar_update_event,
    "workspace.calendar.delete_event":  calendar_delete_event,
    "workspace.drive.search":           drive_search,
    "workspace.drive.get_file":         drive_get_file,
    "workspace.drive.upload":           drive_upload,
    "workspace.docs.read":              docs_read,
    "workspace.docs.create":            docs_create,
    "workspace.docs.update":            docs_update,
    "workspace.tasks.list":             tasks_list,
    "workspace.tasks.create":           tasks_create,
}


class WorkspaceCapability(CapabilityPack):
    """Google Workspace (Gmail, Calendar, Drive, Docs, Tasks) via an MCP server."""

    name = "workspace"
    description = "Google Workspace (Gmail, Calendar, Drive, Docs, Tasks) via an MCP server."

    def __init__(self, config: WorkspaceConfig | None = None) -> None:
        self._config = config or WorkspaceConfig()
        self._policy = build_default_policy(
            account=self._config.account,
            read_only=self._config.read_only,
        )
        self._settings: Settings | None = None
        self._mcp_manager: MCPManager | None = None

    def bind(self, settings: Settings, mcp_manager: MCPManager | None) -> None:
        """Late-bind runtime dependencies. Called by the DI container."""
        self._settings = settings
        self._mcp_manager = mcp_manager

    async def health_check(self) -> CapabilityHealth:
        if self._settings is None:
            return CapabilityHealth(
                status=HealthStatus.UNCONFIGURED,
                summary="Workspace capability not bound to runtime yet.",
                remediation="Container must call .bind(settings, mcp_manager).",
            )
        return await check_workspace_health(
            self._config, self._settings, self._mcp_manager
        )

    def register_actions(self, registry: ActionRegistry) -> None:
        """Register one Action per stable action name into the registry."""
        from kora_v2.capabilities.base import Action

        for full_name, description, read_only, requires_approval in _ACTION_METADATA:
            handler_fn = _ACTION_HANDLERS.get(full_name)
            cap_name = self.name

            # Build a closure that captures the current capability instance
            # and constructs a WorkspaceActionContext on each call.
            def _make_handler(fn: Any, cap_instance: WorkspaceCapability) -> Any:
                async def _handler(
                    session: SessionState,
                    task: TaskState | None = None,
                    **kwargs: Any,
                ) -> Any:
                    ctx = cap_instance.make_context(session=session, task=task)
                    return await fn(ctx, **kwargs)

                return _handler

            action = Action(
                name=full_name,
                description=description,
                capability=cap_name,
                input_schema={"type": "object", "properties": {}},
                requires_approval=requires_approval,
                read_only=read_only,
                handler=_make_handler(handler_fn, self) if handler_fn else None,
            )
            registry.register(action)

    def get_policy(self) -> PolicyMatrix:
        return self._policy

    def make_context(
        self,
        session: SessionState,
        task: TaskState | None = None,
    ) -> WorkspaceActionContext:
        """Build a WorkspaceActionContext for the current session/task."""
        if self._settings is None or self._mcp_manager is None:
            raise RuntimeError(
                "WorkspaceCapability not bound — call .bind(settings, mcp_manager) first."
            )
        return WorkspaceActionContext(
            config=self._config,
            policy=self._policy,
            mcp_manager=self._mcp_manager,
            session=session,
            task=task,
        )
