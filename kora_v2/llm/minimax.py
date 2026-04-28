"""MiniMax LLM provider via Anthropic SDK.

Implements the LLMProviderBase interface using MiniMax's M2.7 model
through the Anthropic-compatible API endpoint.

Key features:
- Anthropic SDK for API communication
- Thinking block preservation in conversation history
- Content moderation flag extraction
- Cache-optimized system prompts (cache_control:ephemeral)
- Model tier routing (conversation vs background)
- Temperature clamping to (0.0, 1.0]
- Resilient JSON parsing for LLM output quirks
"""

import asyncio
import base64
import hashlib
import json
import re
from collections.abc import AsyncIterator
from typing import Any

import anthropic
import httpx
import structlog

from kora_v2.context.budget import count_messages_tokens, count_tokens
from kora_v2.core.exceptions import (
    LLMConnectionError,
    LLMGenerationError,
    LLMTimeoutError,
)
from kora_v2.core.settings import LLMSettings
from kora_v2.llm.base import LLMProviderBase
from kora_v2.llm.types import GenerationResult, ModelTier, StreamChunk, ToolCall

logger = structlog.get_logger()

# MiniMax API domains that need /anthropic suffix
MINIMAX_DOMAINS = ("api.minimax.io", "api.minimaxi.com")


# Unicode ranges covering CJK ideographs + common punctuation variants that
# occasionally leak into English MiniMax output (observed in acceptance:
# "往下翻", "成功"). Fullwidth punctuation (U+FF00-U+FFEF) and CJK symbols
# are also covered to catch "，。！" style leaks.
_CJK_LEAK_RE = re.compile(
    r"[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef]+"
)


def _strip_cjk_leaks(text: str) -> tuple[str, int]:
    """Remove stray CJK runs from model output.

    MiniMax M2.7 is trained on a large Chinese corpus and occasionally
    code-switches into Mandarin mid-sentence in English replies. This is
    cosmetic but jarring. Strip the run and drop any now-empty
    surrounding whitespace / quote pair. Preserve fenced code blocks and
    inline backticks untouched — users may legitimately request non-Latin
    content inside code.

    Returns ``(clean_text, replacement_count)``. Replacement count is a
    telemetry signal (logged by _parse_response) rather than a behavioural
    flag.
    """
    if not text:
        return text, 0
    # Split on fenced code blocks and inline backticks so we don't touch
    # anything inside them.
    parts = re.split(r"(```[\s\S]*?```|`[^`]*`)", text)
    count = 0
    for i, part in enumerate(parts):
        if i % 2 == 1:
            # Odd indices are the delimiters themselves (code blocks /
            # inline code) — leave untouched.
            continue
        new_part, n = _CJK_LEAK_RE.subn("", part)
        if n:
            # Clean up orphan delimiters left by the replacement
            # (e.g. empty quote pairs ``""`` / ``''``) and double spaces.
            new_part = re.sub(r"\s{2,}", " ", new_part)
            new_part = re.sub(r"\s+([,.;!?])", r"\1", new_part)
            count += n
            parts[i] = new_part
    return "".join(parts), count


def _build_base_url(base_url: str) -> str:
    """Build the full API URL, auto-appending /anthropic for MiniMax domains."""
    base_url = base_url.rstrip("/")
    is_minimax = any(domain in base_url for domain in MINIMAX_DOMAINS)
    if is_minimax:
        # Strip existing suffixes, then add /anthropic
        base_url = base_url.replace("/anthropic", "").replace("/v1", "")
        return f"{base_url}/anthropic"
    return base_url


def _clamp_temperature(temperature: float) -> float:
    """Clamp temperature to MiniMax's valid range (0.0 excluded, 1.0 included)."""
    if temperature <= 0.0:
        return 0.01
    if temperature > 1.0:
        return 1.0
    return temperature


def _msg_has_tool_use(msg: dict) -> bool:
    """Check if an assistant message contains tool_use blocks."""
    for key in ("content", "content_blocks"):
        val = msg.get(key)
        if isinstance(val, list):
            if any(isinstance(b, dict) and b.get("type") == "tool_use" for b in val):
                return True
    return False


def _extract_tool_use_ids(msg: dict) -> set[str]:
    """Extract all tool_use IDs from an assistant message."""
    ids: set[str] = set()
    for key in ("content", "content_blocks"):
        val = msg.get(key)
        if isinstance(val, list):
            for b in val:
                if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("id"):
                    ids.add(b["id"])
    return ids


def _extract_tool_result_ids(msg: dict) -> set[str]:
    """Extract all tool_result tool_use_ids from a user message."""
    ids: set[str] = set()
    for key in ("content", "content_blocks"):
        content = msg.get(key)
        if isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id"):
                    ids.add(b["tool_use_id"])
    return ids


class MiniMaxProvider(LLMProviderBase):
    """MiniMax LLM provider using Anthropic SDK.

    Uses MiniMax's M2.7 model via the Anthropic-compatible endpoint.
    Supports thinking, tool calling, streaming, and prompt caching.
    """

    def __init__(self, settings: LLMSettings) -> None:
        """Initialize the MiniMax provider.

        Args:
            settings: LLM configuration from V2 settings.
        """
        self._settings = settings
        self._full_base_url = _build_base_url(settings.api_base)

        # trust_env=False prevents httpx from picking up SOCKS/system proxies
        _http_client = httpx.AsyncClient(trust_env=False)
        self._client = anthropic.AsyncAnthropic(
            base_url=self._full_base_url,
            api_key=settings.api_key,
            default_headers={"Authorization": f"Bearer {settings.api_key}"},
            timeout=settings.timeout,
            max_retries=settings.retry_attempts,
            http_client=_http_client,
        )

        # Metrics
        self._call_count = 0
        self._total_prompt_tokens = 0
        self._total_completion_tokens = 0
        self._total_thinking_tokens = 0

        # Cache state
        self._cache_hash: str | None = None

        logger.info(
            "MiniMaxProvider initialized",
            base_url=self._full_base_url,
            model=settings.model,
        )

    # =========================================================================
    # PUBLIC API -- LLMProviderBase implementations
    # =========================================================================

    async def generate(
        self,
        messages: list,
        system_prompt: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        tier: ModelTier = ModelTier.CONVERSATION,
    ) -> str:
        """Generate a complete text response."""
        result = await self._call_api(
            messages=messages,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            tier=tier,
            thinking_enabled=False,
        )
        return result.content

    async def generate_stream(
        self,
        messages: list,
        system_prompt: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        tier: ModelTier = ModelTier.CONVERSATION,
    ) -> AsyncIterator[str]:
        """Stream response tokens."""
        async for chunk in self._stream_api(
            messages=messages,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            tier=tier,
            thinking_enabled=False,
        ):
            if chunk.type == "text":
                yield chunk.text

    async def generate_with_thinking(
        self,
        messages: list,
        system_prompt: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        thinking_enabled: bool = True,
        tier: ModelTier = ModelTier.CONVERSATION,
    ) -> GenerationResult:
        """Generate with thinking content blocks."""
        return await self._call_api(
            messages=messages,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            tier=tier,
            thinking_enabled=thinking_enabled,
        )

    async def generate_stream_with_thinking(
        self,
        messages: list,
        system_prompt: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        thinking_enabled: bool = True,
        tier: ModelTier = ModelTier.CONVERSATION,
    ) -> AsyncIterator[StreamChunk]:
        """Stream response with thinking content blocks."""
        async for chunk in self._stream_api(
            messages=messages,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            tier=tier,
            thinking_enabled=thinking_enabled,
        ):
            yield chunk

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
        """Generate with tool calling support."""
        return await self._call_api(
            messages=messages,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            tier=tier,
            thinking_enabled=thinking_enabled,
            tools=tools,
            tool_choice=tool_choice,
        )

    async def health_check(self) -> bool:
        """Check provider health with a minimal API call."""
        try:
            response = await self._client.messages.create(
                model=self._settings.model,
                max_tokens=10,
                messages=[{"role": "user", "content": "hi"}],
            )
            return response is not None
        except Exception as e:
            logger.warning("MiniMax health check failed", error=str(e))
            return False

    async def probe_availability(self) -> bool:
        """Lightweight transport-level availability probe.

        Sends an HTTP GET to the MiniMax API base URL to check reachability
        without consuming any model tokens.
        """
        try:
            http_client = self._client._client  # httpx.AsyncClient
            await http_client.get(self._full_base_url, timeout=5.0)
            return True
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.TimeoutException) as e:
            logger.debug("MiniMax probe_availability failed (connection)", error=str(e))
            return False
        except Exception as e:
            logger.debug("MiniMax probe_availability failed", error=str(e))
            return False

    async def create_cache(
        self,
        system_prompt: str,
        ttl_seconds: int = 3600,
    ) -> str | None:
        """Create cache via inline cache_control markers.

        Anthropic SDK uses inline cache_control on message/system blocks
        rather than a separate cache API. We track the hash to avoid
        re-marking unchanged prompts.
        """
        content_hash = hashlib.md5(system_prompt.encode()).hexdigest()
        if self._cache_hash == content_hash:
            return self._cache_hash
        self._cache_hash = content_hash
        logger.info("System prompt cache hash updated", hash=content_hash[:8])
        return content_hash

    def invalidate_cache(self) -> None:
        """Invalidate the current cache."""
        if self._cache_hash:
            logger.info("Invalidating system prompt cache")
        self._cache_hash = None

    @property
    def model_name(self) -> str:
        return self._settings.model

    @property
    def context_window(self) -> int:
        return self._settings.context_window

    # =========================================================================
    # INTERNAL -- API call implementation
    # =========================================================================

    def _select_model(self, tier: ModelTier) -> str:
        """Select model based on tier."""
        if tier == ModelTier.BACKGROUND and self._settings.background_model:
            return self._settings.background_model
        return self._settings.model

    async def _call_api(
        self,
        messages: list,
        system_prompt: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        tier: ModelTier = ModelTier.CONVERSATION,
        thinking_enabled: bool = False,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> GenerationResult:
        """Core API call -- handles all generation variants.

        Args:
            messages: Conversation messages.
            system_prompt: System prompt (extracted separately for Anthropic).
            temperature: Generation temperature.
            max_tokens: Max output tokens.
            tier: Model routing tier.
            thinking_enabled: Enable thinking blocks.
            tools: Optional tool definitions.

        Returns:
            GenerationResult with content, tool_calls, thinking, and usage.
        """
        system_blocks, api_messages = self._format_messages(messages)
        api_messages = self.cleanup_incomplete_messages(api_messages)
        if not api_messages:
            raise LLMGenerationError("message list is empty after cleanup; cannot call API")

        # System prompt from parameter takes precedence
        if system_prompt:
            system_blocks = self._build_system_blocks(system_prompt)

        effective_max_tokens = max_tokens or self._settings.max_tokens

        # Pre-call safety: estimate input tokens and refuse/trim if near limit.
        effective_thinking = thinking_enabled
        try:
            input_estimate = count_messages_tokens(api_messages)
            if system_blocks:
                for block in system_blocks:
                    input_estimate += count_tokens(block.get("text", ""))
            if tools:
                input_estimate += count_tokens(json.dumps(tools))

            context_limit = self._settings.context_window
            safety_threshold = int(context_limit * 0.95)

            if input_estimate > safety_threshold:
                logger.warning(
                    "Pre-call safety: estimated tokens exceeds safety threshold, disabling thinking",
                    estimated=input_estimate,
                    threshold=safety_threshold,
                    context_window=context_limit,
                )
                effective_thinking = False
                remaining = context_limit - input_estimate
                if remaining < 2000:
                    raise LLMGenerationError(
                        f"Context overflow: estimated {input_estimate} input tokens "
                        f"leaves only {remaining} for output (context_window={context_limit}). "
                        f"Compaction required before this call."
                    )
                effective_max_tokens = min(effective_max_tokens, max(1024, remaining - 1000))
        except LLMGenerationError:
            raise
        except Exception as e:
            logger.debug("Pre-call token estimation failed (non-fatal)", error=str(e))

        params = self._build_params(
            model=self._select_model(tier),
            system=system_blocks,
            messages=api_messages,
            temperature=temperature,
            max_tokens=effective_max_tokens,
            thinking_enabled=effective_thinking,
            tools=tools,
            tool_choice=tool_choice,
        )

        try:
            response = await asyncio.wait_for(
                self._client.messages.create(**params),
                timeout=self._settings.timeout,
            )
            self._call_count += 1
            result = self._parse_response(response)

            # Detect empty response with max_tokens finish -- model burned all
            # tokens on thinking with nothing left for content/tool_calls.
            # Retry ONCE with thinking disabled.
            if (
                result.finish_reason == "max_tokens"
                and not result.content.strip()
                and not result.tool_calls
                and effective_thinking
            ):
                logger.warning(
                    "Empty response with finish_reason=max_tokens and thinking enabled, "
                    "retrying with thinking disabled",
                    prompt_tokens=result.prompt_tokens,
                    completion_tokens=result.completion_tokens,
                )
                params["thinking"] = {"type": "disabled"}
                params["max_tokens"] = max_tokens or self._settings.max_tokens
                response = await asyncio.wait_for(
                    self._client.messages.create(**params),
                    timeout=self._settings.timeout,
                )
                self._call_count += 1
                result = self._parse_response(response)

            return result

        except TimeoutError as e:
            raise LLMTimeoutError(
                f"MiniMax request timed out after {self._settings.timeout:.0f}s"
            ) from e
        except anthropic.AuthenticationError as e:
            raise LLMConnectionError(f"MiniMax authentication failed: {e}") from e
        except anthropic.RateLimitError as e:
            raise LLMGenerationError(f"MiniMax rate limit exceeded: {e}") from e
        except anthropic.APITimeoutError as e:
            raise LLMTimeoutError(f"MiniMax request timed out: {e}") from e
        except anthropic.APIConnectionError as e:
            raise LLMConnectionError(f"MiniMax connection error: {e}") from e
        except anthropic.APIStatusError as e:
            raise LLMGenerationError(f"MiniMax API error ({e.status_code}): {e.message}") from e
        except Exception as e:
            raise LLMGenerationError(f"MiniMax generation failed: {e}") from e

    async def _stream_api(
        self,
        messages: list,
        system_prompt: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        tier: ModelTier = ModelTier.CONVERSATION,
        thinking_enabled: bool = False,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Streaming API call -- yields StreamChunk events."""
        system_blocks, api_messages = self._format_messages(messages)
        api_messages = self.cleanup_incomplete_messages(api_messages)
        if not api_messages:
            raise LLMGenerationError("message list is empty after cleanup; cannot stream")

        if system_prompt:
            system_blocks = self._build_system_blocks(system_prompt)

        effective_max_tokens = max_tokens or self._settings.max_tokens
        effective_thinking = thinking_enabled

        # Pre-call safety for streaming too
        try:
            input_estimate = count_messages_tokens(api_messages)
            if system_blocks:
                for block in system_blocks:
                    input_estimate += count_tokens(block.get("text", ""))
            if tools:
                input_estimate += count_tokens(json.dumps(tools))

            context_limit = self._settings.context_window
            safety_threshold = int(context_limit * 0.95)

            if input_estimate > safety_threshold:
                logger.warning(
                    "Pre-call safety (stream): estimated tokens exceeds threshold, disabling thinking",
                    estimated=input_estimate,
                    threshold=safety_threshold,
                )
                effective_thinking = False
                remaining = context_limit - input_estimate
                if remaining < 2000:
                    raise LLMGenerationError(
                        f"Context overflow (stream): estimated {input_estimate} input tokens "
                        f"leaves only {remaining} for output. Compaction required."
                    )
                effective_max_tokens = min(effective_max_tokens, max(1024, remaining - 1000))
        except LLMGenerationError:
            raise
        except Exception as e:
            logger.debug("Pre-call token estimation failed (stream, non-fatal)", error=str(e))

        params = self._build_params(
            model=self._select_model(tier),
            system=system_blocks,
            messages=api_messages,
            temperature=temperature,
            max_tokens=effective_max_tokens,
            thinking_enabled=effective_thinking,
            tools=tools,
        )

        try:
            async with self._client.messages.stream(**params) as stream:
                async for event in stream:
                    event_type = getattr(event, "type", "")

                    if event_type == "content_block_start":
                        pass  # Block type tracking not needed for yield logic

                    elif event_type == "content_block_delta":
                        delta = getattr(event, "delta", None)
                        if delta:
                            delta_type = getattr(delta, "type", "")
                            if delta_type == "thinking_delta":
                                text = getattr(delta, "thinking", "")
                                if text:
                                    yield StreamChunk(type="thinking", text=text)
                            elif delta_type == "text_delta":
                                text = getattr(delta, "text", "")
                                if text:
                                    yield StreamChunk(type="text", text=text)

            self._call_count += 1

        except anthropic.AuthenticationError as e:
            raise LLMConnectionError(f"MiniMax authentication failed: {e}") from e
        except anthropic.APITimeoutError as e:
            raise LLMTimeoutError(f"MiniMax stream timed out: {e}") from e
        except anthropic.APIConnectionError as e:
            raise LLMConnectionError(f"MiniMax stream connection error: {e}") from e
        except Exception as e:
            raise LLMGenerationError(f"MiniMax stream failed: {e}") from e

    # =========================================================================
    # MESSAGE FORMATTING
    # =========================================================================

    def _format_messages(
        self, messages: list
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Convert messages to Anthropic format.

        Handles:
        - Plain dicts (primary V2 format)
        - ToolCall dicts -> tool_use content blocks
        - tool role -> user role with tool_result content blocks

        Returns:
            Tuple of (system_blocks, api_messages).
        """
        system_blocks: list[dict[str, Any]] = []
        api_messages: list[dict[str, Any]] = []

        # Index-based loop so we can batch consecutive tool results.
        idx = 0
        while idx < len(messages):
            msg = messages[idx]
            if not isinstance(msg, dict):
                idx += 1
                continue

            role = msg.get("role", "user")
            content = msg.get("content", "")
            tool_calls_data = msg.get("tool_calls")
            thinking_data = msg.get("thinking")
            content_blocks_data = msg.get("content_blocks")

            # System messages -> separate system parameter
            if role == "system":
                system_blocks.append({"type": "text", "text": content})
                idx += 1
                continue

            # Assistant messages with preserved content_blocks (raw Anthropic format)
            if role == "assistant" and content_blocks_data:
                # Normalize: LangGraph may use "tool_call" internally;
                # MiniMax/Anthropic API expects "tool_use".
                sanitized_blocks = []
                for blk in content_blocks_data:
                    if not isinstance(blk, dict):
                        continue
                    btype = blk.get("type", "")
                    if btype == "tool_call":
                        # Convert LangGraph tool_call -> Anthropic tool_use
                        sanitized_blocks.append({
                            "type": "tool_use",
                            "id": blk.get("id", ""),
                            "name": blk.get("name", ""),
                            "input": blk.get("args", blk.get("arguments", blk.get("input", {}))),
                        })
                    elif btype in ("text", "thinking", "tool_use", "tool_result"):
                        sanitized_blocks.append(blk)
                    # Drop unsupported block types silently
                if sanitized_blocks:
                    api_messages.append(
                        {
                            "role": "assistant",
                            "content": sanitized_blocks,
                        }
                    )
                elif content:
                    # Fallback: use plain text if all blocks were dropped
                    api_messages.append({"role": "assistant", "content": content})
                idx += 1
                continue

            # Assistant messages with tool calls
            if role == "assistant" and tool_calls_data:
                blocks: list[dict[str, Any]] = []
                if thinking_data:
                    blocks.append({"type": "thinking", "thinking": thinking_data})
                if content:
                    blocks.append({"type": "text", "text": content})
                for tc in tool_calls_data:
                    if isinstance(tc, dict):
                        blocks.append(
                            {
                                "type": "tool_use",
                                "id": tc.get("id", ""),
                                "name": tc.get("name", ""),
                                "input": tc.get("args", tc.get("arguments", tc.get("input", {}))),
                            }
                        )
                    elif isinstance(tc, ToolCall):
                        blocks.append(
                            {
                                "type": "tool_use",
                                "id": tc.id,
                                "name": tc.name,
                                "input": tc.arguments,
                            }
                        )
                api_messages.append({"role": "assistant", "content": blocks})
                idx += 1
                continue

            # Tool result messages — batch ALL consecutive role="tool" dicts
            # into a single user message with multiple tool_result blocks.
            # The Anthropic API requires all tool_results for a tool_use batch
            # to be in one user message.
            if role == "tool":
                tool_result_blocks: list[dict[str, Any]] = []
                while idx < len(messages) and isinstance(messages[idx], dict) and messages[idx].get("role") == "tool":
                    # A tool_result without a tool_use_id is malformed and
                    # will later explode LangChain's ToolMessage validator
                    # (KeyError on 'tool_call_id'). Skip it — pair integrity
                    # is handled by ensure_tool_pair_integrity upstream, so
                    # the matching tool_use will also be stripped.
                    tc_id = messages[idx].get("tool_call_id")
                    if not tc_id:
                        logger.warning(
                            "dropping_tool_result_missing_tool_call_id",
                            content_preview=str(messages[idx].get("content", ""))[:80],
                        )
                        idx += 1
                        continue
                    tool_result_blocks.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tc_id,
                            "content": messages[idx].get("content", ""),
                        }
                    )
                    idx += 1
                if tool_result_blocks:
                    api_messages.append(
                        {
                            "role": "user",
                            "content": tool_result_blocks,
                        }
                    )
                continue

            # Regular user/assistant messages
            if role in ("user", "assistant"):
                api_messages.append({"role": role, "content": content})

            idx += 1

        return system_blocks, api_messages

    def _build_system_blocks(self, system_prompt: str) -> list[dict[str, Any]]:
        """Build system prompt blocks with cache_control markers.

        Uses Anthropic's inline cache_control for prompt caching.
        """
        if not system_prompt:
            return []

        block: dict[str, Any] = {
            "type": "text",
            "text": system_prompt,
        }
        if self._settings.enable_caching:
            block["cache_control"] = {"type": "ephemeral"}

        return [block]

    # =========================================================================
    # RESPONSE PARSING
    # =========================================================================

    def _parse_response(self, response: anthropic.types.Message) -> GenerationResult:
        """Parse Anthropic response into GenerationResult.

        Extracts text, thinking blocks, tool calls, usage, and
        content moderation flags from the response.
        """
        text_content = ""
        thinking_content = ""
        tool_calls: list[ToolCall] = []
        content_blocks: list[dict[str, Any]] = []

        cjk_leaks = 0
        for block in response.content:
            block_type = getattr(block, "type", "")

            if block_type == "text":
                clean_text, leak_count = _strip_cjk_leaks(block.text)
                cjk_leaks += leak_count
                text_content += clean_text
                content_blocks.append({"type": "text", "text": clean_text})

            elif block_type == "thinking":
                thinking_content += block.thinking
                block_dict: dict[str, Any] = {
                    "type": "thinking",
                    "thinking": block.thinking,
                }
                if hasattr(block, "signature") and block.signature:
                    block_dict["signature"] = block.signature
                content_blocks.append(block_dict)

            elif block_type == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=block.id,
                        name=block.name,
                        arguments=block.input if isinstance(block.input, dict) else {},
                    )
                )
                content_blocks.append(
                    {
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input if isinstance(block.input, dict) else {},
                    }
                )

        # Extract usage
        usage = response.usage if hasattr(response, "usage") else None
        prompt_tokens = 0
        completion_tokens = 0
        cache_creation = 0
        cache_read = 0

        if usage:
            prompt_tokens = getattr(usage, "input_tokens", 0) or 0
            completion_tokens = getattr(usage, "output_tokens", 0) or 0
            cache_creation = getattr(usage, "cache_creation_input_tokens", 0) or 0
            cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0

        # Update running totals
        self._total_prompt_tokens += prompt_tokens
        self._total_completion_tokens += completion_tokens

        if cjk_leaks:
            logger.info("cjk_leak_sanitized", runs_stripped=cjk_leaks)

        # Content moderation flags
        input_sensitive = getattr(response, "input_sensitive", False)
        output_sensitive = getattr(response, "output_sensitive", False)
        if input_sensitive:
            logger.warning("MiniMax content moderation: input flagged as sensitive")
        if output_sensitive:
            logger.warning("MiniMax content moderation: output flagged as sensitive")

        return GenerationResult(
            content=text_content,
            tool_calls=tool_calls,
            finish_reason=response.stop_reason or "stop",
            thought_text=thinking_content,
            thought_tokens=0,  # MiniMax lumps thinking into output_tokens
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            content_blocks=content_blocks,
            cache_creation_input_tokens=cache_creation,
            cache_read_input_tokens=cache_read,
            input_sensitive=input_sensitive,
            output_sensitive=output_sensitive,
        )

    # =========================================================================
    # REQUEST BUILDING
    # =========================================================================

    def _build_params(
        self,
        model: str,
        system: list[dict[str, Any]],
        messages: list[dict[str, Any]],
        temperature: float,
        max_tokens: int,
        thinking_enabled: bool = False,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build API request parameters.

        Handles thinking configuration, tool definitions, tool_choice
        override, and temperature clamping for MiniMax.

        Tool-choice precedence:

        1. Explicit ``tool_choice`` argument (normalized to Anthropic
           dict form) always wins.
        2. Otherwise, a single tool is force-selected (structured-output
           pattern).
        3. Otherwise, default to ``{"type": "auto"}``.
        """
        params: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
            "temperature": _clamp_temperature(temperature),
        }

        if system:
            params["system"] = system

        if thinking_enabled:
            # Anthropic spec requires budget_tokens < max_tokens
            # Allocate half for thinking, ensure max_tokens covers both
            thinking_budget = max_tokens
            total_max = thinking_budget * 2
            params["thinking"] = {"type": "enabled", "budget_tokens": thinking_budget}
            params["max_tokens"] = total_max

        if tools:
            # Work on a local copy to avoid mutating the caller's list
            tools = list(tools)
            if self._settings.enable_caching:
                for i, t in enumerate(tools):
                    if i == len(tools) - 1:
                        tool_copy = dict(t)
                        tool_copy["cache_control"] = {"type": "ephemeral"}
                        tools[i] = tool_copy
            params["tools"] = tools

            # Normalise tool_choice override into Anthropic dict form.
            resolved_choice: dict[str, Any] | None = None
            if isinstance(tool_choice, str):
                if tool_choice in ("auto", "any"):
                    resolved_choice = {"type": tool_choice}
                # Unknown string → ignore, fall through to default.
            elif isinstance(tool_choice, dict):
                resolved_choice = tool_choice

            if resolved_choice is not None:
                params["tool_choice"] = resolved_choice
            elif len(tools) == 1:
                tool_name = tools[0].get("name")
                if tool_name:
                    params["tool_choice"] = {"type": "tool", "name": tool_name}
            else:
                params["tool_choice"] = {"type": "auto"}

        return params

    # =========================================================================
    # IMAGE UNDERSTANDING
    # =========================================================================

    async def analyze_image(
        self,
        image_data: bytes,
        prompt: str,
        media_type: str = "image/png",
        temperature: float = 0.5,
        max_tokens: int = 1024,
        tier: ModelTier = ModelTier.CONVERSATION,
    ) -> str:
        """Analyze an image via M2.7's Anthropic vision support."""
        image_b64 = base64.standard_b64encode(image_data).decode("utf-8")

        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": prompt,
                    },
                ],
            }
        ]

        try:
            response = await self._client.messages.create(
                model=self._select_model(tier),
                max_tokens=max_tokens,
                temperature=_clamp_temperature(temperature),
                messages=messages,
            )
            self._call_count += 1

            text = ""
            for block in response.content:
                if getattr(block, "type", "") == "text":
                    text += block.text

            usage = getattr(response, "usage", None)
            if usage:
                self._total_prompt_tokens += getattr(usage, "input_tokens", 0) or 0
                self._total_completion_tokens += getattr(usage, "output_tokens", 0) or 0

            return text

        except anthropic.AuthenticationError as e:
            raise LLMConnectionError(f"MiniMax authentication failed: {e}") from e
        except anthropic.APITimeoutError as e:
            raise LLMTimeoutError(f"MiniMax image analysis timed out: {e}") from e
        except anthropic.APIConnectionError as e:
            raise LLMConnectionError(f"MiniMax connection error: {e}") from e
        except anthropic.APIStatusError as e:
            raise LLMGenerationError(
                f"MiniMax image analysis error ({e.status_code}): {e.message}"
            ) from e
        except Exception as e:
            raise LLMGenerationError(f"MiniMax image analysis failed: {e}") from e

    # =========================================================================
    # MESSAGE CLEANUP
    # =========================================================================

    @staticmethod
    def cleanup_incomplete_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Remove dangling assistant messages without corresponding tool results.

        Scans the full message list for unpaired tool_use / tool_result blocks.
        Every assistant message containing tool_use blocks MUST be followed by
        a user message containing the matching tool_result blocks. If the
        pair is incomplete, both the orphaned assistant and any following
        user messages that belong to the same broken pair are removed.

        This prevents MiniMax API error 2013 which occurs when the message
        history contains tool_use IDs without matching tool_result IDs.
        """
        if not messages:
            return messages

        cleaned = list(messages)

        # Pass 1: strip trailing dangling assistant messages
        while cleaned:
            last = cleaned[-1]
            if last.get("role") != "assistant":
                break
            if _msg_has_tool_use(last):
                logger.warning(
                    "Removing trailing assistant message with tool_use (no tool_result follows)"
                )
                cleaned.pop()
            else:
                break

        # Pass 2: scan full list for orphaned tool_use / tool_result pairs.
        # Collect ALL consecutive user messages with tool_result blocks when
        # checking for a match — tool results may be split across multiple
        # user messages (one per tool call) rather than batched into one.
        i = 0
        result: list[dict[str, Any]] = []
        while i < len(cleaned):
            msg = cleaned[i]
            if msg.get("role") == "assistant" and _msg_has_tool_use(msg):
                use_ids = _extract_tool_use_ids(msg)
                # Collect all consecutive user messages that carry tool_result
                # blocks. These may be individual (one per tool) or batched.
                j = i + 1
                result_ids: set[str] = set()
                while j < len(cleaned):
                    candidate = cleaned[j]
                    if candidate.get("role") != "user":
                        break
                    candidate_ids = _extract_tool_result_ids(candidate)
                    if not candidate_ids:
                        break
                    result_ids |= candidate_ids
                    j += 1

                if use_ids <= result_ids:
                    # Complete pair — keep the assistant and all result messages
                    result.append(msg)
                    for k in range(i + 1, j):
                        result.append(cleaned[k])
                    i = j
                    continue

                if result_ids:
                    logger.warning(
                        "Removing orphaned tool pair",
                        use_ids=use_ids,
                        result_ids=result_ids,
                    )
                else:
                    logger.warning(
                        "Removing orphaned assistant tool_use message",
                        ids=use_ids,
                    )
                i = j  # Skip past assistant + any partial results
            elif msg.get("role") == "user" and _extract_tool_result_ids(msg):
                logger.warning(
                    "Removing orphaned user tool_result message",
                    ids=_extract_tool_result_ids(msg),
                )
                i += 1
            else:
                result.append(msg)
                i += 1

        return result

    # =========================================================================
    # STATUS / METRICS
    # =========================================================================

    async def get_status(self) -> dict:
        """Get provider status for observability."""
        return {
            "provider": "minimax",
            "model": self._settings.model,
            "base_url": self._full_base_url,
            "call_count": self._call_count,
            "total_prompt_tokens": self._total_prompt_tokens,
            "total_completion_tokens": self._total_completion_tokens,
            "caching_enabled": self._settings.enable_caching,
        }

    # =========================================================================
    # JSON PARSING RESILIENCE
    # =========================================================================

    @staticmethod
    def _safe_parse_json(text: str, default: Any = None) -> Any:
        """Parse JSON with resilience to LLM formatting quirks.

        MiniMax M2.7 sometimes wraps JSON in markdown code blocks,
        includes preamble text before the JSON, or adds trailing
        commentary. This method handles all common patterns.
        """
        if not text or not text.strip():
            return default

        # Try 1: Direct parse
        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            pass

        # Try 2: Extract JSON from markdown code blocks
        match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
        if match:
            try:
                return json.loads(match.group(1))
            except (json.JSONDecodeError, ValueError):
                pass

        # Try 3: Find first balanced { ... } or [ ... ]
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except (json.JSONDecodeError, ValueError):
                pass

        # Try array form
        start = text.find("[")
        end = text.rfind("]")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except (json.JSONDecodeError, ValueError):
                pass

        logger.warning(
            "MiniMax returned unparseable JSON, using default",
            length=len(text),
        )
        return default
