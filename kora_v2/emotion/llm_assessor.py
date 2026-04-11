"""LLM-based PAD emotion assessor — async, high-accuracy tier.

Runs when the fast tier's confidence is low or when a large emotional
shift is detected. Sends recent messages to the LLM with a structured
JSON output request. Falls back to the current state (halved confidence)
on any parse error.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections import OrderedDict

import structlog
from pydantic import BaseModel, Field, ValidationError

from kora_v2.core.models import EmotionalState

logger = structlog.get_logger()


class PADResponse(BaseModel):
    """Structured PAD output expected from the LLM.

    Field bounds mirror ``EmotionalState`` so the LLM's response matches
    the downstream contract. Extra keys are ignored so the model tolerates
    optional fields like ``reasoning``.
    """
    valence: float = Field(ge=-1.0, le=1.0)
    arousal: float = Field(ge=0.0, le=1.0)
    dominance: float = Field(ge=0.0, le=1.0)
    mood_label: str = "neutral"

_SYSTEM_PROMPT = """You are an emotion analysis system. Analyse the emotional state expressed in the conversation messages provided.

Respond with ONLY a JSON object — no preamble, no explanation outside the JSON.

Required fields:
- "valence": float in [-1.0, 1.0]  (negative = unpleasant, positive = pleasant)
- "arousal": float in [0.0, 1.0]   (0 = calm/low energy, 1 = excited/high energy)
- "dominance": float in [0.0, 1.0] (0 = helpless/controlled, 1 = in control/empowered)
- "mood_label": string — one of: excited, elated, happy, surprised, calm, content, neutral, relaxed, anxious, distressed, angry, frustrated, sad, helpless, bored, tired
- "reasoning": string — one sentence explaining your assessment

Example:
{"valence": 0.6, "arousal": 0.7, "dominance": 0.6, "mood_label": "excited", "reasoning": "The user expressed enthusiasm about an upcoming event."}"""


def _parse_pad_json(text: str) -> dict | None:
    """Parse PAD JSON from LLM response with resilience to formatting quirks."""
    if not text or not text.strip():
        return None

    # Try 1: direct parse
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass

    # Try 2: extract from markdown code block
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if match:
        try:
            return json.loads(match.group(1))
        except (json.JSONDecodeError, ValueError):
            pass

    # Try 3: find first balanced { ... }
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except (json.JSONDecodeError, ValueError):
            pass

    return None


class LLMEmotionAssessor:
    """Async LLM-based PAD emotion assessor.

    Used when fast tier confidence is low or a large PAD shift is detected.
    Falls back to the current_state (with halved confidence) on any error.

    Includes an in-process LRU cache keyed on the hash of the message
    window so the 40% timeout rate observed in the 2026-04-11 acceptance
    run is bounded: repeated assessments over the same recent-message
    window (common in harness scenarios and during rapid-fire turns)
    reuse the previous result instead of re-paying a 30s LLM call.
    """

    # Default timeout — raised from 15s after the 2026-04-11 acceptance
    # run saw ~40% of calls time out at 15s on MiniMax cold-start.
    # Emotion assessment is supplementary so a longer wait is acceptable
    # as long as the cache keeps repeat-call cost near zero.
    DEFAULT_TIMEOUT: float = 30.0

    # Cache capacity — 32 windows covers the typical harness scenario
    # without consuming meaningful memory (~3 KB total).
    CACHE_CAPACITY: int = 32

    def __init__(
        self,
        llm,
        *,
        timeout: float | None = None,
        cache_capacity: int | None = None,
    ) -> None:
        """Initialise with an LLM provider.

        Args:
            llm: Any provider with an async ``generate(messages, system_prompt, temperature)``
                 method (e.g. MiniMaxProvider or a mock).
            timeout: Maximum seconds to wait for the LLM response before
                     falling back.  Defaults to ``DEFAULT_TIMEOUT`` (30 s).
            cache_capacity: Max number of cached message-window → PAD
                     entries. Defaults to ``CACHE_CAPACITY`` (32).
        """
        self.llm = llm
        self.timeout = timeout if timeout is not None else self.DEFAULT_TIMEOUT
        self._cache_capacity = (
            cache_capacity if cache_capacity is not None else self.CACHE_CAPACITY
        )
        self._cache: OrderedDict[str, EmotionalState] = OrderedDict()

    @staticmethod
    def _window_key(recent_messages: list[str]) -> str:
        """Stable hash of the last-5-message window used as cache key."""
        joined = "\u0001".join(recent_messages[-5:])
        return hashlib.sha256(joined.encode("utf-8", errors="replace")).hexdigest()

    def _cache_get(self, key: str) -> EmotionalState | None:
        entry = self._cache.get(key)
        if entry is None:
            return None
        self._cache.move_to_end(key)
        return entry

    def _cache_put(self, key: str, value: EmotionalState) -> None:
        self._cache[key] = value
        self._cache.move_to_end(key)
        while len(self._cache) > self._cache_capacity:
            self._cache.popitem(last=False)

    async def assess(
        self,
        recent_messages: list[str],
        current_state: EmotionalState,
    ) -> EmotionalState:
        """Assess emotional state using the LLM.

        Args:
            recent_messages: Last 3-5 conversation messages.
            current_state: Current best-guess EmotionalState (used as fallback).

        Returns:
            EmotionalState with source="llm".
        """
        cache_key = self._window_key(recent_messages)
        cached = self._cache_get(cache_key)
        if cached is not None:
            logger.debug("LLMEmotionAssessor.cache_hit", key=cache_key[:12])
            return cached

        messages_text = "\n".join(
            f"Message {i + 1}: {msg}" for i, msg in enumerate(recent_messages[-5:])
        )

        raw = await self._call_llm(
            user_message=f"Analyse the emotional state in these messages:\n\n{messages_text}",
            repair_hint=None,
        )
        if raw is None:
            return self._fallback(current_state)

        pad = self._parse_and_validate(raw)
        if pad is None:
            # Single repair retry with explicit schema correction.
            retry_user = (
                f"Your previous response did not match the required schema. "
                f"Return ONLY a JSON object with keys valence, arousal, "
                f"dominance, mood_label. Analyse these messages:\n\n{messages_text}"
            )
            raw_retry = await self._call_llm(
                user_message=retry_user,
                repair_hint="schema_repair",
            )
            if raw_retry is None:
                return self._fallback(current_state)
            pad = self._parse_and_validate(raw_retry)
            if pad is None:
                logger.warning(
                    "LLMEmotionAssessor: repair retry failed, falling back",
                    response_preview=str(raw_retry)[:120],
                )
                return self._fallback(current_state)

        result = EmotionalState(
            valence=pad.valence,
            arousal=pad.arousal,
            dominance=pad.dominance,
            mood_label=pad.mood_label,
            confidence=0.85,
            source="llm",
        )
        self._cache_put(cache_key, result)
        logger.debug(
            "LLMEmotionAssessor.assess",
            valence=round(pad.valence, 3),
            arousal=round(pad.arousal, 3),
            dominance=round(pad.dominance, 3),
            mood_label=pad.mood_label,
        )
        return result

    async def _call_llm(
        self, *, user_message: str, repair_hint: str | None
    ) -> str | None:
        """Invoke the LLM with the standard system prompt and timeout.

        Returns the raw response text, or ``None`` on timeout/error.
        """
        try:
            return await asyncio.wait_for(
                self.llm.generate(
                    messages=[{"role": "user", "content": user_message}],
                    system_prompt=_SYSTEM_PROMPT,
                    temperature=0.1,
                ),
                timeout=self.timeout,
            )
        except TimeoutError:
            logger.warning(
                "LLMEmotionAssessor: LLM call timed out, falling back",
                timeout=self.timeout,
                repair=repair_hint,
            )
            return None
        except Exception as e:
            logger.warning(
                "LLMEmotionAssessor: LLM call failed, falling back",
                error=str(e),
                repair=repair_hint,
            )
            return None

    @staticmethod
    def _parse_and_validate(raw: str) -> PADResponse | None:
        """Parse the LLM response and validate against ``PADResponse``.

        Returns ``None`` if either parse or schema validation fails; the
        caller decides whether to retry or fall back.
        """
        parsed = _parse_pad_json(raw)
        if parsed is None:
            return None
        # Coerce a common drift case: ``{"messages": [{...}, ...]}`` batched
        # analysis → take the first entry. This handles the 2026-04-11
        # audit's observed schema drift without a repair round-trip.
        if (
            "valence" not in parsed
            and isinstance(parsed.get("messages"), list)
            and parsed["messages"]
            and isinstance(parsed["messages"][0], dict)
        ):
            parsed = parsed["messages"][0]
        try:
            return PADResponse.model_validate(parsed)
        except ValidationError as exc:
            logger.warning(
                "LLMEmotionAssessor: PAD validation failed",
                error=str(exc)[:200],
                parsed_keys=list(parsed.keys())
                if isinstance(parsed, dict)
                else type(parsed).__name__,
            )
            return None

    def _fallback(self, current_state: EmotionalState) -> EmotionalState:
        """Return current_state with halved confidence and source='llm'."""
        return EmotionalState(
            valence=current_state.valence,
            arousal=current_state.arousal,
            dominance=current_state.dominance,
            mood_label=current_state.mood_label,
            confidence=current_state.confidence / 2.0,
            source="llm",
        )


def should_trigger_llm_assessment(
    current: EmotionalState,
    previous: EmotionalState | None,
    turns_since_last_llm: int = 0,
) -> bool:
    """Decide whether to invoke the LLM assessor.

    Triggers when:
    - confidence < 0.5 (fast assessor uncertain), OR
    - any PAD axis delta > 0.4 compared to previous (raised from 0.3).

    Suppresses when:
    - turns_since_last_llm is between 1 and 2 inclusive (cooldown — don't
      re-assess for 3 turns after the last LLM assessment).

    Exception: Always triggers if confidence < 0.3 regardless of cooldown.

    Args:
        current: The fast-tier EmotionalState.
        previous: The prior EmotionalState (or None on first turn).
        turns_since_last_llm: Number of turns since the last LLM emotion
            assessment.  0 means no prior LLM assessment (first assessment).

    Returns:
        True if LLM assessment should run, False otherwise.
    """
    # Very low confidence always triggers (override cooldown)
    if current.confidence < 0.3:
        return True

    # Cooldown: skip if we just did an LLM assessment recently
    if turns_since_last_llm > 0 and turns_since_last_llm < 3:
        return False

    # Low confidence triggers
    if current.confidence < 0.5:
        return True

    # Big shift triggers (raised threshold from 0.3 to 0.4)
    if previous is not None:
        if (abs(current.valence - previous.valence) > 0.4
                or abs(current.arousal - previous.arousal) > 0.4
                or abs(current.dominance - previous.dominance) > 0.4):
            return True

    return False
