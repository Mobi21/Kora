"""Abstract base class for LLM providers.

Defines the contract that all LLM implementations must implement.
Provides a unified interface for the rest of the codebase.
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

import structlog

from kora_v2.llm.types import GenerationResult, ModelTier, StreamChunk

logger = structlog.get_logger()


class LLMProviderBase(ABC):
    """Abstract base class for LLM provider implementations.

    All LLM providers must implement these methods to be usable
    by the orchestration graph, extraction pipeline, and other callers.
    """

    @abstractmethod
    async def generate(
        self,
        messages: list,
        system_prompt: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        tier: ModelTier = ModelTier.CONVERSATION,
    ) -> str:
        """Generate a complete text response.

        Args:
            messages: Conversation history (dict format).
            system_prompt: Optional system prompt.
            temperature: Generation temperature.
            max_tokens: Maximum tokens to generate.
            tier: Model tier for routing.

        Returns:
            Generated response text.
        """
        ...

    @abstractmethod
    async def generate_stream(
        self,
        messages: list,
        system_prompt: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        tier: ModelTier = ModelTier.CONVERSATION,
    ) -> AsyncIterator[str]:
        """Stream response tokens as they're generated.

        Args:
            messages: Conversation history.
            system_prompt: Optional system prompt.
            temperature: Generation temperature.
            max_tokens: Maximum tokens to generate.
            tier: Model tier for routing.

        Yields:
            Response tokens.
        """
        ...

    @abstractmethod
    async def generate_with_thinking(
        self,
        messages: list,
        system_prompt: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        thinking_enabled: bool = True,
        tier: ModelTier = ModelTier.CONVERSATION,
    ) -> GenerationResult:
        """Generate with thinking/reasoning content.

        Args:
            messages: Conversation history.
            system_prompt: Optional system prompt.
            temperature: Generation temperature.
            max_tokens: Maximum tokens to generate.
            thinking_enabled: Whether to enable thinking/reasoning.
            tier: Model tier for routing.

        Returns:
            GenerationResult with content, thought_text, and token counts.
        """
        ...

    @abstractmethod
    async def generate_stream_with_thinking(
        self,
        messages: list,
        system_prompt: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        thinking_enabled: bool = True,
        tier: ModelTier = ModelTier.CONVERSATION,
    ) -> AsyncIterator[StreamChunk]:
        """Stream response with thinking content blocks.

        Args:
            messages: Conversation history.
            system_prompt: Optional system prompt.
            temperature: Generation temperature.
            max_tokens: Maximum tokens to generate.
            thinking_enabled: Whether to enable thinking.
            tier: Model tier for routing.

        Yields:
            StreamChunk with type="thinking" or type="text".
        """
        ...

    @abstractmethod
    async def generate_with_tools(
        self,
        messages: list,
        tools: list[dict[str, Any]],
        system_prompt: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        thinking_enabled: bool = True,
        tier: ModelTier = ModelTier.CONVERSATION,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> GenerationResult:
        """Generate with tool calling support.

        Args:
            messages: Conversation history.
            tools: Tool definitions in Anthropic format.
            system_prompt: Optional system prompt.
            temperature: Generation temperature.
            max_tokens: Maximum tokens to generate.
            thinking_enabled: Whether to enable thinking.
            tier: Model tier for routing.
            tool_choice: Optional tool-choice override. Accepted values:
                ``"auto"`` (default — LLM may return prose),
                ``"any"`` (force a tool call, LLM picks which),
                ``{"type": "tool", "name": "x"}`` (force a specific tool).
                If ``None``, providers apply their default (typically
                ``"auto"`` with >1 tool, or forced single-tool with 1).

        Returns:
            GenerationResult with content, tool_calls, and thinking.
        """
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """Check if the provider is available and responding.

        Returns:
            True if healthy, False otherwise.
        """
        ...

    @abstractmethod
    async def create_cache(
        self,
        system_prompt: str,
        ttl_seconds: int = 3600,
    ) -> str | None:
        """Create a cached content entry for static system prompts.

        Args:
            system_prompt: The system prompt to cache.
            ttl_seconds: Cache TTL in seconds.

        Returns:
            Cache identifier if successful, None otherwise.
        """
        ...

    @abstractmethod
    def invalidate_cache(self) -> None:
        """Invalidate the current cache."""
        ...

    @abstractmethod
    async def analyze_image(
        self,
        image_data: bytes,
        prompt: str,
        media_type: str = "image/png",
        temperature: float = 0.5,
        max_tokens: int = 1024,
        tier: ModelTier = ModelTier.CONVERSATION,
    ) -> str:
        """Analyze an image and return a text description.

        Args:
            image_data: Raw image bytes.
            prompt: Instruction for what to analyze in the image.
            media_type: MIME type (image/png, image/jpeg, image/gif, image/webp).
            temperature: Generation temperature.
            max_tokens: Maximum response tokens.
            tier: Model tier for routing.

        Returns:
            Text analysis of the image.
        """
        ...

    async def analyze_screenshot(
        self,
        screenshot_path: str,
        prompt: str,
        temperature: float = 0.5,
        max_tokens: int = 1024,
        tier: ModelTier = ModelTier.CONVERSATION,
    ) -> str:
        """Analyze a screenshot file and return text analysis.

        Convenience wrapper around analyze_image() that reads from a file path.

        Args:
            screenshot_path: Path to the screenshot file.
            prompt: Instruction for what to analyze.
            temperature: Generation temperature.
            max_tokens: Maximum response tokens.
            tier: Model tier for routing.

        Returns:
            Text analysis of the screenshot.
        """
        import mimetypes
        from pathlib import Path

        path = Path(screenshot_path)
        if not path.exists():
            raise FileNotFoundError(f"Screenshot not found: {screenshot_path}")

        image_data = path.read_bytes()
        mime_type = mimetypes.guess_type(str(path))[0] or "image/png"

        return await self.analyze_image(
            image_data=image_data,
            prompt=prompt,
            media_type=mime_type,
            temperature=temperature,
            max_tokens=max_tokens,
            tier=tier,
        )

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Get the name of the current model."""
        ...

    @property
    @abstractmethod
    def context_window(self) -> int:
        """Get the context window size in tokens."""
        ...

    async def probe_availability(self) -> bool:
        """Lightweight transport-level availability probe.

        Tests whether the LLM API endpoint is reachable WITHOUT consuming
        any model tokens. Used for periodic polling.

        Default implementation delegates to health_check() for backward
        compatibility. Providers SHOULD override this with a lighter probe.

        Returns:
            True if the API endpoint is reachable, False otherwise.
        """
        return await self.health_check()

    async def set_mode(self, mode: str) -> None:
        """Set the operating mode (conversation, reflection, etc.).

        No-op by default. Providers that support mode switching
        can override this.
        """
