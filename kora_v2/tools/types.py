"""Tool system data models for Kora V2.

Defines all types used by Kora's tool system: tool definitions,
calls, results, authorization, and error categories.
"""

import uuid
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class AuthLevel(StrEnum):
    """Authorization level for tool execution."""

    ALWAYS_ALLOWED = "always_allowed"  # No check needed
    ASK_FIRST = "ask_first"  # Pause and confirm with user
    NEVER = "never"  # Reject, suggest manual action


class ToolCategory(StrEnum):
    """Logical grouping of tools."""

    MEMORY = "memory"
    TASKS = "tasks"
    USER_MODEL = "user_model"
    ENTITIES = "entities"
    SELF = "self"
    WORKFLOWS = "workflows"
    FILESYSTEM = "filesystem"
    WEB = "web"
    CALENDAR = "calendar"
    MESSAGING = "messaging"
    AGENTS = "agents"
    SHELL = "shell"
    LIFE_MANAGEMENT = "life_management"
    SCREEN = "screen"


class ErrorCategory(StrEnum):
    """Classification of tool execution errors."""

    TRANSIENT = "transient"  # Network timeout, rate limit -> retry
    NOT_FOUND = "not_found"  # File/resource doesn't exist -> inform LLM
    PERMISSION = "permission"  # Access denied -> escalate to user
    VALIDATION = "validation"  # Bad arguments -> inform LLM to fix
    FATAL = "fatal"  # Unrecoverable -> stop and report


class ToolCall(BaseModel):
    """A tool invocation requested by the LLM.

    Represents a single function call extracted from the LLM response.
    The id is used to correlate calls with results.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str = Field(..., description="Tool function name")
    arguments: dict[str, Any] = Field(
        default_factory=dict, description="Tool arguments as key-value pairs"
    )


class ToolResult(BaseModel):
    """Result of executing a tool.

    Contains either content (success) or error info (failure).
    error_category helps the LLM decide how to handle failures.
    """

    tool_call_id: str = Field(..., description="ID of the ToolCall this responds to")
    tool_name: str = Field(..., description="Name of the tool that was called")
    content: str | None = Field(None, description="Successful result content")
    error: str | None = Field(None, description="Error message if failed")
    error_category: ErrorCategory | None = Field(None, description="Error classification")
    success: bool = Field(True, description="Whether execution succeeded")
    details: dict[str, Any] | None = Field(
        None, description="Raw structured data preserved for re-query. Never truncated."
    )
    truncated: bool = Field(
        False, description="True if content was truncated from the original result"
    )
    total_count: int | None = Field(
        None, description="Total items available when result is a list (e.g., '5 of 20 shown')"
    )

    @property
    def display_content(self) -> str:
        """Get content for display (result or error)."""
        if self.content:
            return self.content
        if self.error:
            return f"Error ({self.error_category.value if self.error_category else 'unknown'}): {self.error}"
        return "No result"


class AuthorizationRequest(BaseModel):
    """Request for user authorization to execute a tool.

    Sent when a tool has auth_level=ASK_FIRST.
    """

    tool_call: ToolCall = Field(..., description="The tool call awaiting authorization")
    tool_description: str = Field(..., description="Human-readable description of the tool")
    question: str = Field(..., description="Question to ask the user")
    context: str = Field("", description="Additional context about what the tool will do")


class AuthorizationResponse(BaseModel):
    """User's response to an authorization request."""

    tool_call_id: str = Field(..., description="ID of the tool call being authorized")
    decision: str = Field(..., description="'allow_once', 'allow_always', or 'reject'")
    reason: str | None = Field(None, description="User's reason for the decision")


class ToolPermission(BaseModel):
    """A stored permission grant for a tool.

    When user selects 'allow_always', we store this so future
    calls to the same tool don't require re-authorization.
    """

    tool_name: str = Field(..., description="Tool that was granted permission")
    granted_at: str = Field(..., description="ISO timestamp of when permission was granted")
    scope: str = Field("global", description="Scope of permission: 'global' or specific context")
    conditions: dict[str, Any] = Field(
        default_factory=dict, description="Optional conditions (e.g., path prefix)"
    )


class ToolExecutionMetrics(BaseModel):
    """Metrics for a single tool execution.

    Used for monitoring, debugging, and circuit breaker logic.
    """

    tool_name: str
    call_id: str
    started_at: str
    completed_at: str | None = None
    duration_ms: int | None = None
    success: bool = True
    error_category: ErrorCategory | None = None
    retry_count: int = 0


class ToolDefinition(BaseModel):
    """Complete definition of a registered tool.

    Created by the @tool decorator and stored in the ToolRegistry.
    Contains everything needed to present the tool to the LLM and execute it.
    """

    name: str = Field(..., description="Unique tool name (snake_case)")
    description: str = Field(..., description="Human-readable description for the LLM")
    category: ToolCategory = Field(..., description="Logical grouping")
    auth_level: AuthLevel = Field(AuthLevel.ASK_FIRST, description="Authorization requirement")
    parameters_schema: dict[str, Any] = Field(..., description="JSON Schema for parameters")
    internal: bool = Field(True, description="True for Python functions, False for MCP")
    is_read_only: bool = Field(
        False,
        description="True for read-only tools (search_*, get_*, query_*) that can run in parallel",
    )
    timeout_seconds: float | None = Field(
        None,
        description="Optional per-tool timeout override in seconds",
    )

    def to_anthropic_tool(self) -> dict:
        """Convert to Anthropic tool format.

        Returns a dict with name, description, input_schema for the Anthropic API.
        """
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters_schema,
        }
