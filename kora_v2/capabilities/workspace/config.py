"""Workspace capability config."""
from __future__ import annotations

from pydantic import BaseModel, Field


class WorkspaceConfig(BaseModel):
    """Configuration for the Google Workspace capability."""

    mcp_server_name: str = "workspace"   # key in settings.mcp.servers
    account: str = "personal"            # "personal" | "work" | user-defined label
    user_google_email: str = ""          # required by current workspace-mcp tools
    read_only: bool = False              # if True, force-deny all writes regardless of policy
    default_calendar_id: str = "primary"
    provenance_marker: str = "[Created by Kora]"
    provenance_metadata_key: str = "kora_origin"
    provenance_metadata_value: str = "kora-v2"

    # Tool name mappings — keep config-driven so the user can swap MCP servers.
    # Keys are stable Kora action names; values are the MCP tool names on the server.
    # Defaults target taylorwilsdon/google_workspace_mcp.
    tool_map: dict[str, str] = Field(default_factory=lambda: {
        "gmail.search":          "search_gmail_messages",
        "gmail.get_message":     "get_gmail_message",
        "gmail.draft":           "create_gmail_draft",
        "gmail.send":            "send_gmail_message",
        # Current taylorwilsdon/workspace-mcp calendar tools.
        "calendar.list":         "get_events",
        "calendar.get_event":    "get_events",
        "calendar.create_event": "manage_event",
        "calendar.update_event": "manage_event",
        "calendar.delete_event": "manage_event",
        "drive.search":          "search_drive_files",
        "drive.get_file":        "get_drive_file_content",
        "drive.upload":          "upload_drive_file",
        "docs.read":             "get_docs_content",
        "docs.create":           "create_doc",
        "docs.update":           "update_doc",
        "tasks.list":            "list_tasks",
        "tasks.create":          "create_task",
    })
