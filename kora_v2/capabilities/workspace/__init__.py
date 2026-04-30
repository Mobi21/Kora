"""Workspace capability pack — Google Workspace via MCP."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from kora_v2.capabilities.base import (
    CapabilityHealth,
    CapabilityPack,
    HealthStatus,
    StructuredFailure,
)
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

# ── JSON schemas for each action (keyed by full action name) ─────────────────
_WORKSPACE_ACTION_SCHEMAS: dict[str, dict] = {
    "workspace.gmail.search": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Gmail search query string"},
            "max_results": {"type": "integer", "default": 10, "minimum": 1, "maximum": 100},
        },
        "required": ["query"],
    },
    "workspace.gmail.get_message": {
        "type": "object",
        "properties": {
            "message_id": {"type": "string", "description": "Gmail message ID"},
        },
        "required": ["message_id"],
    },
    "workspace.gmail.draft": {
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "Recipient email address"},
            "subject": {"type": "string", "description": "Email subject"},
            "body": {"type": "string", "description": "Email body text"},
        },
        "required": ["to", "subject", "body"],
    },
    "workspace.gmail.send": {
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "Recipient email address"},
            "subject": {"type": "string", "description": "Email subject"},
            "body": {"type": "string", "description": "Email body text"},
        },
        "required": ["to", "subject", "body"],
    },
    "workspace.calendar.list": {
        "type": "object",
        "properties": {
            "time_min": {"type": "string", "description": "Start of time range (RFC3339)"},
            "time_max": {"type": "string", "description": "End of time range (RFC3339)"},
            "calendar_id": {"type": "string", "description": "Calendar ID (defaults to primary)"},
        },
    },
    "workspace.calendar.get_event": {
        "type": "object",
        "properties": {
            "event_id": {"type": "string", "description": "Calendar event ID"},
            "calendar_id": {"type": "string", "description": "Calendar ID (defaults to primary)"},
        },
        "required": ["event_id"],
    },
    "workspace.calendar.create_event": {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "Event title / summary"},
            "start": {"type": "string", "description": "Start time (RFC3339)"},
            "end": {"type": "string", "description": "End time (RFC3339)"},
            "description": {"type": "string", "description": "Event description"},
            "attendees": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of attendee email addresses",
            },
            "calendar_id": {"type": "string", "description": "Calendar ID (defaults to primary)"},
        },
        "required": ["summary", "start", "end"],
    },
    "workspace.calendar.update_event": {
        "type": "object",
        "properties": {
            "event_id": {"type": "string", "description": "Calendar event ID"},
            "updates": {"type": "object", "description": "Fields to update"},
            "calendar_id": {"type": "string", "description": "Calendar ID (defaults to primary)"},
        },
        "required": ["event_id", "updates"],
    },
    "workspace.calendar.delete_event": {
        "type": "object",
        "properties": {
            "event_id": {"type": "string", "description": "Calendar event ID"},
            "calendar_id": {"type": "string", "description": "Calendar ID (defaults to primary)"},
        },
        "required": ["event_id"],
    },
    "workspace.drive.search": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Drive search query string"},
            "max_results": {"type": "integer", "default": 10, "minimum": 1, "maximum": 100},
        },
        "required": ["query"],
    },
    "workspace.drive.get_file": {
        "type": "object",
        "properties": {
            "file_id": {"type": "string", "description": "Drive file ID"},
        },
        "required": ["file_id"],
    },
    "workspace.drive.upload": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Filename for the uploaded file"},
            "content": {"type": "string", "description": "File content (text)"},
            "mime_type": {"type": "string", "default": "text/plain", "description": "MIME type"},
        },
        "required": ["name", "content"],
    },
    "workspace.docs.read": {
        "type": "object",
        "properties": {
            "document_id": {"type": "string", "description": "Google Docs document ID"},
        },
        "required": ["document_id"],
    },
    "workspace.docs.create": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Document title"},
            "content": {"type": "string", "description": "Initial document content"},
        },
        "required": ["title"],
    },
    "workspace.docs.update": {
        "type": "object",
        "properties": {
            "document_id": {"type": "string", "description": "Google Docs document ID"},
            "updates": {"type": "object", "description": "Fields/requests to apply"},
        },
        "required": ["document_id", "updates"],
    },
    "workspace.tasks.list": {
        "type": "object",
        "properties": {
            "list_id": {"type": "string", "description": "Task list ID (defaults to primary)"},
        },
    },
    "workspace.tasks.create": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Task title"},
            "list_id": {"type": "string", "description": "Task list ID (defaults to primary)"},
            "notes": {"type": "string", "description": "Additional notes for the task"},
        },
        "required": ["title"],
    },
}

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
        configured = getattr(settings, "workspace", None)
        if isinstance(configured, WorkspaceConfig):
            self._config = configured
        elif isinstance(configured, dict):
            self._config = WorkspaceConfig(**configured)
        self._policy = build_default_policy(
            account=self._config.account,
            read_only=self._config.read_only,
        )

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
            def _make_handler(fn: Any, cap_instance: WorkspaceCapability, action_name: str) -> Any:
                async def _handler(
                    session: SessionState,
                    task: TaskState | None = None,
                    **kwargs: Any,
                ) -> Any:
                    if cap_instance._settings is None or cap_instance._mcp_manager is None:
                        return StructuredFailure(
                            capability="workspace",
                            action=action_name,
                            path="capability.unbound",
                            reason="capability_not_configured",
                            user_message=(
                                "The workspace capability is not yet configured. "
                                "Run the daemon doctor to see remediation."
                            ),
                            recoverable=False,
                        )
                    ctx = cap_instance.make_context(session=session, task=task)
                    return await fn(ctx, **kwargs)

                return _handler

            action = Action(
                name=full_name,
                description=description,
                capability=cap_name,
                input_schema=_WORKSPACE_ACTION_SCHEMAS.get(full_name, {"type": "object", "properties": {}}),
                requires_approval=requires_approval,
                read_only=read_only,
                handler=_make_handler(handler_fn, self, full_name) if handler_fn else None,
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
