"""LLM-based PAD emotion assessor — async, high-accuracy tier.

Runs when the fast tier's confidence is low or when a large emotional
shift is detected. Sends recent messages to the LLM with a structured
JSON output request. Falls back to the current state (halved confidence)
on any parse error.
"""

from __future__ import annotations

import asyncio
import json
import re

import structlog

from kora_v2.core.models import EmotionalState

logger = structlog.get_logger()

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


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


class LLMEmotionAssessor:
    """Async LLM-based PAD emotion assessor.

    Used when fast tier confidence is low or a large PAD shift is detected.
    Falls back to the current_state (with halved confidence) on any error.
    """

    # Emotion assessment is supplementary — if the LLM is cold (first call)
    # we should fall back quickly rather than blocking the turn for 120s.
    DEFAULT_TIMEOUT: float = 15.0

    def __init__(self, llm, *, timeout: float | None = None) -> None:
        """Initialise with an LLM provider.

        Args:
            llm: Any provider with an async ``generate(messages, system_prompt, temperature)``
                 method (e.g. MiniMaxProvider or a mock).
            timeout: Maximum seconds to wait for the LLM response before
                     falling back.  Defaults to ``DEFAULT_TIMEOUT`` (15 s).
        """
        self.llm = llm
        self.timeout = timeout if timeout is not None else self.DEFAULT_TIMEOUT

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
        messages_text = "\n".join(
            f"Message {i + 1}: {msg}" for i, msg in enumerate(recent_messages[-5:])
        )
        user_message = f"Analyse the emotional state in these messages:\n\n{messages_text}"

        try:
            raw = await asyncio.wait_for(
                self.llm.generate(
                    messages=[{"role": "user", "content": user_message}],
                    system_prompt=_SYSTEM_PROMPT,
                    temperature=0.1,
                ),
                timeout=self.timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "LLMEmotionAssessor: LLM call timed out, falling back",
                timeout=self.timeout,
            )
            return self._fallback(current_state)
        except Exception as e:
            logger.warning("LLMEmotionAssessor: LLM call failed, falling back", error=str(e))
            return self._fallback(current_state)

        parsed = _parse_pad_json(raw)
        if parsed is None:
            logger.warning(
                "LLMEmotionAssessor: could not parse JSON response, falling back",
                response_preview=str(raw)[:120],
            )
            return self._fallback(current_state)

        try:
            valence = float(parsed["valence"])
            arousal = float(parsed["arousal"])
            dominance = float(parsed["dominance"])
            mood_label = str(parsed.get("mood_label", "neutral"))

            valence = _clamp(valence, -1.0, 1.0)
            arousal = _clamp(arousal, 0.0, 1.0)
            dominance = _clamp(dominance, 0.0, 1.0)

        except (KeyError, TypeError, ValueError) as e:
            logger.warning(
                "LLMEmotionAssessor: missing/invalid PAD fields, falling back",
                error=str(e),
                parsed=parsed,
            )
            return self._fallback(current_state)

        logger.debug(
            "LLMEmotionAssessor.assess",
            valence=round(valence, 3),
            arousal=round(arousal, 3),
            dominance=round(dominance, 3),
            mood_label=mood_label,
        )

        return EmotionalState(
            valence=valence,
            arousal=arousal,
            dominance=dominance,
            mood_label=mood_label,
            confidence=0.85,
            source="llm",
        )

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
