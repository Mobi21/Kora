"""LLM type definitions for Kora V2.

Provider-agnostic models for generation results, streaming chunks,
content blocks, and model tier routing. Used by LLMProviderBase and
all provider implementations.
"""

import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class LLMMode(StrEnum):
    """Operating mode for the LLM service."""

    CONVERSATION = "conversation"
    REFLECTION = "reflection"
    BACKGROUND = "background"


class ModelTier(StrEnum):
    """Model tier for routing to appropriate model variant."""

    CONVERSATION = "conversation"  # Primary model for user-facing responses
    BACKGROUND = "background"  # Cheaper model for background/extraction tasks


# ── Content Blocks ────────────────────────────────────────────────────────


class ThinkingBlock(BaseModel):
    """A thinking content block preserved in conversation history."""

    type: str = "thinking"
    thinking: str = ""
    signature: str | None = None  # MiniMax stores signature on thinking blocks


class ToolUseBlock(BaseModel):
    """An Anthropic-format tool use content block."""

    type: str = "tool_use"
    id: str = ""
    name: str = ""
    input: dict[str, Any] = Field(default_factory=dict)


class ToolResultBlock(BaseModel):
    """An Anthropic-format tool result content block."""

    type: str = "tool_result"
    tool_use_id: str = ""
    content: str = ""
    is_error: bool = False


class TextBlock(BaseModel):
    """A text content block."""

    type: str = "text"
    text: str = ""


# ── Tool Call ─────────────────────────────────────────────────────────────


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


# ── Generation Result ─────────────────────────────────────────────────────


class GenerationResult(BaseModel):
    """Result from LLM generation that may include tool calls.

    Provider-agnostic result model used by all LLM implementations.
    When the LLM wants to use tools, content may be empty/partial
    and tool_calls will contain the requested invocations.
    """

    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    finish_reason: str = "stop"
    thought_text: str = ""
    thought_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0

    # Full content blocks list for history preservation
    content_blocks: list[dict[str, Any]] = Field(default_factory=list)

    # Cache token tracking (Anthropic/MiniMax)
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    # Content moderation flags (MiniMax)
    input_sensitive: bool = False
    output_sensitive: bool = False

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0


# ── Streaming ─────────────────────────────────────────────────────────────


@dataclass
class StreamChunk:
    """A chunk from streaming LLM generation.

    Attributes:
        type: "thinking" for thought tokens, "text" for response tokens.
        text: The token content.
    """

    type: str  # "thinking" or "text"
    text: str


@dataclass
class StreamEvent:
    """Event emitted by graph nodes via get_stream_writer().

    Attributes:
        type: Event type -- "status", "token", "thinking", "tool_status".
        text: Display text or token content.
        phase: Node name that emitted this event.
        metadata: Optional extra data (e.g., thinking tokens count).
    """

    type: str
    text: str
    phase: str = ""
    metadata: dict[str, Any] | None = None
