"""Rule-based PAD emotion assessor — fast, synchronous, <1ms per call.

Computes Pleasure-Arousal-Dominance values from text signals:
- Sentiment lexicon (positive/negative word lists + emoji mapping)
- Topic valence (emotional topic keyword detection)
- Arousal signals (length, caps ratio, punctuation density, emoji density)
- Dominance signals (agency vs helplessness language)
- Trajectory (compared to recent messages)
- Momentum (continuity weight on prior state)
"""

from __future__ import annotations

import re

import structlog

from kora_v2.core.models import EmotionalState

logger = structlog.get_logger()

# ── Sentiment Lexicon ─────────────────────────────────────────────────────────

# Positive words → contribute +1 each (normalised later)
_POSITIVE_WORDS: frozenset[str] = frozenset(
    [
        "happy", "happiness", "joy", "joyful", "joyous", "great", "amazing", "wonderful",
        "excellent", "fantastic", "love", "loved", "loving", "beautiful", "lovely",
        "awesome", "terrific", "brilliant", "good", "nice", "pleased",
        "cheerful", "delighted", "thrilled", "excited", "elated", "grateful",
        "thankful", "blessed", "fortunate", "lucky", "positive", "optimistic",
        "hopeful", "inspired", "motivated", "energized", "confident", "proud",
        "calm", "peaceful", "relaxed", "content", "satisfied", "comfortable",
        "safe", "secure", "appreciated", "supported", "encouraged",
        "productive", "successful", "accomplished", "winning", "triumph",
        "victory", "perfect", "glad", "ecstatic", "overjoyed", "radiant",
        "vibrant", "glorious", "celebrate", "celebration", "fun", "enjoy",
        "enjoying", "enjoyed", "laugh", "laughing", "smile", "smiling",
        # Expanded lexicon (Phase 4)
        "helpful", "ready", "relieved", "refreshed",
    ]
)

# Negative words → contribute -1 each (normalised later)
_NEGATIVE_WORDS: frozenset[str] = frozenset(
    [
        "sad", "sadness", "unhappy", "miserable", "depressed", "depression",
        "hopeless", "helpless", "hopelessness", "despair", "desperate",
        "frustrated", "frustration", "angry", "anger", "furious", "rage",
        "anxious", "anxiety", "worried", "worry", "fear", "scared", "terrified",
        "panic", "dread", "terrible", "horrible", "awful", "dreadful", "hideous",
        "disgusting", "disgusted", "hate", "hated", "hating", "loathe", "loathing",
        "bad", "worst", "failure", "failed", "failing", "useless", "worthless",
        "broken", "ruined", "destroy", "destroyed", "lost", "losing", "miss",
        "missing", "alone", "lonely", "isolated", "abandoned", "rejected",
        "shame", "ashamed", "embarrassed", "guilty", "regret", "remorse",
        "hurt", "hurting", "pain", "painful", "suffering", "suffer",
        "tired", "exhausted", "drained", "burnt", "overwhelmed", "stressed",
        "stuck", "trapped", "powerless", "unable", "struggling", "struggle",
        "difficult", "hard", "impossible", "crying", "cry", "tears",
        "heartbreak", "heartbroken", "devastated", "tragic", "tragedy",
        # Expanded lexicon (Phase 4)
        "confused", "burned", "irritated", "annoyed", "disappointed",
        "discouraged", "panicked", "hopeless",
    ]
)

# Emoji sentiment mapping
_EMOJI_SENTIMENT: dict[str, float] = {
    # Strong positive
    "😊": 0.8, "😄": 0.9, "😃": 0.9, "😁": 0.9, "😂": 0.7, "🤣": 0.7,
    "😍": 0.9, "🥰": 0.9, "😎": 0.6, "🥳": 0.9, "🎉": 0.8, "✨": 0.5,
    "💕": 0.8, "❤️": 0.9, "💖": 0.9, "💗": 0.8, "💓": 0.8, "💝": 0.9,
    "🙏": 0.5, "👏": 0.6, "🎊": 0.8, "🌟": 0.6, "⭐": 0.5, "🌈": 0.6,
    "😀": 0.8, "🤩": 0.9, "🥹": 0.6, "😇": 0.7, "🤗": 0.7, "😌": 0.5,
    # Negative
    "😢": -0.8, "😭": -0.9, "😞": -0.7, "😔": -0.6, "😟": -0.6,
    "😠": -0.8, "😡": -0.9, "🤬": -0.9, "😤": -0.7, "😩": -0.7,
    "😰": -0.7, "😨": -0.7, "😱": -0.8, "😣": -0.7, "😖": -0.7,
    "💔": -0.9, "😿": -0.8, "🙁": -0.5, "☹️": -0.6, "😫": -0.7,
    "🤮": -0.8, "😒": -0.4, "🥺": -0.3, "😓": -0.5, "😥": -0.6,
    # Neutral / slight
    "🤔": 0.0, "😶": 0.0, "😐": 0.0, "😑": -0.1, "🙄": -0.3,
    "😴": -0.1, "🤷": 0.0, "😅": 0.1,
}

# ── Topic Valence ─────────────────────────────────────────────────────────────

_TOPIC_VALENCE: dict[str, float] = {
    # Negative topics
    "grief": -0.9, "bereave": -0.9, "loss": -0.6, "death": -0.7, "die": -0.7,
    "died": -0.7, "dying": -0.7, "cancer": -0.7, "illness": -0.6, "sick": -0.5,
    "disease": -0.6, "injury": -0.6, "accident": -0.6, "crash": -0.6,
    "stress": -0.6, "burnout": -0.7, "overwhelm": -0.7,
    "anxiety": -0.7, "depression": -0.8, "trauma": -0.8,
    "failure": -0.7, "fail": -0.6, "failed": -0.6, "mistake": -0.5,
    "problem": -0.4, "issue": -0.3, "trouble": -0.5, "crisis": -0.7,
    "emergency": -0.6, "danger": -0.7, "threat": -0.6,
    "money": -0.2,  # Neutral-slight (contextual)
    "debt": -0.6, "broke": -0.7, "bankruptcy": -0.8, "financial": -0.2,
    "divorce": -0.7, "breakup": -0.7, "fight": -0.5, "argument": -0.4,
    # Positive topics
    "celebration": 0.8, "birthday": 0.6, "wedding": 0.7, "party": 0.6,
    "vacation": 0.7, "holiday": 0.6, "achievement": 0.8, "success": 0.8,
    "promotion": 0.8, "raise": 0.6, "bonus": 0.6, "win": 0.7, "won": 0.7,
    "graduate": 0.8, "graduation": 0.8, "degree": 0.5,
    "baby": 0.7, "pregnant": 0.6, "born": 0.7, "birth": 0.7,
    "health": 0.4, "healthy": 0.6, "recovered": 0.7, "recovery": 0.6,
    "better": 0.4, "improved": 0.5, "healed": 0.7,
}

# ── Dominance Signals ─────────────────────────────────────────────────────────

_AGENCY_PATTERNS: list[str] = [
    r"\bi will\b", r"\bi can\b", r"\bi decided\b", r"\bi'm going to\b",
    r"\bi chose\b", r"\bi choose\b", r"\bi made\b", r"\bi created\b",
    r"\bi built\b", r"\bi finished\b", r"\bi completed\b", r"\bi achieved\b",
    r"\bi handled\b", r"\bi solved\b", r"\bi fixed\b", r"\bi did it\b",
    r"\bi got this\b", r"\bi have it\b", r"\bi'm in control\b",
]

_HELPLESSNESS_PATTERNS: list[str] = [
    r"\bi can't\b", r"\bi cannot\b", r"\bi don't know\b",
    r"\bi feel stuck\b", r"\bi'm lost\b", r"\bi'm helpless\b",
    r"\bi give up\b", r"\bi can't handle\b", r"\bi can't do\b",
    r"\bnothing works\b", r"\bno idea\b", r"\bcompletely lost\b",
    r"\bhave no control\b", r"\bout of my hands\b", r"\bi'm powerless\b",
    r"\bdon't know what to do\b", r"\bno way out\b", r"\bi'm failing\b",
]


# ── PAD → Mood Label ──────────────────────────────────────────────────────────


def _pad_to_mood(valence: float, arousal: float, dominance: float) -> str:
    """Map PAD coordinates to a mood label string.

    Thresholds:
    - High valence: > 0.2
    - Low valence: < -0.2
    - High arousal: > 0.4
    - Low arousal: <= 0.4
    - High dominance: > 0.5
    - Low dominance: <= 0.5
    - Near neutral: valence in (-0.2, 0.2) and arousal <= 0.3
    """
    near_neutral_v = -0.2 < valence < 0.2
    near_neutral_a = arousal <= 0.3
    high_v = valence > 0.2
    low_v = valence < -0.2
    high_a = arousal > 0.4
    high_d = dominance > 0.5

    if near_neutral_v and near_neutral_a:
        return "neutral" if dominance > 0.4 else "relaxed"

    if high_v and high_a and high_d:
        return "excited"
    if high_v and high_a:
        return "happy"
    if high_v and not high_a and high_d:
        return "content"
    if high_v and not high_a:
        return "calm"

    if low_v and high_a and high_d:
        return "angry"
    if low_v and high_a and not high_d:
        return "anxious"
    if low_v and not high_a and not high_d:
        return "sad"
    if low_v and not high_a and high_d:
        return "tired"

    # Fallback for weak signals
    if near_neutral_v:
        return "neutral"
    return "neutral"


# ── FastEmotionAssessor ───────────────────────────────────────────────────────


class FastEmotionAssessor:
    """Synchronous, rule-based PAD emotion assessor.

    All computation is pure Python with no I/O. Target: <1ms per call.
    """

    def assess(
        self,
        message: str,
        recent_messages: list[str],
        current_state: EmotionalState | None,
    ) -> EmotionalState:
        """Assess the emotional content of a message.

        Args:
            message: The current message to assess.
            recent_messages: Up to last 3 prior messages for trajectory.
            current_state: Previous EmotionalState for momentum (optional).

        Returns:
            EmotionalState with PAD values, mood label, confidence, source="fast".
        """
        text_lower = message.lower()
        signal_count = 0
        max_signals = 6  # sentiment, topic, length, caps, punctuation, emoji

        # 1. Sentiment from lexicon
        sentiment, sentiment_signal_count = self._score_sentiment(text_lower, message)
        signal_count += min(1, sentiment_signal_count)

        # 2. Topic valence
        topic_val, topic_found = self._score_topic_valence(text_lower)
        signal_count += 1 if topic_found else 0

        # 3. Arousal signals
        arousal, arousal_signals = self._compute_arousal(message)
        signal_count += min(1, arousal_signals)

        # 4. Dominance signals
        dominance = self._compute_dominance(text_lower)

        # 5. Emoji sentiment contribution
        emoji_sentiment = self._score_emoji_sentiment(message)

        # 6. Trajectory from recent messages
        trajectory_delta = self._compute_trajectory(message, recent_messages)

        # ── PAD calculation ──

        # Valence: weighted blend of sentiment + topic + emoji + trajectory
        # Momentum: 0.2 weight on prior state if available
        if current_state is not None:
            valence = (
                0.5 * sentiment
                + 0.15 * topic_val
                + 0.15 * emoji_sentiment
                + 0.2 * current_state.valence
            )
        else:
            valence = (
                0.6 * sentiment
                + 0.2 * topic_val
                + 0.2 * emoji_sentiment
            )

        # Clamp to [-1, 1]
        valence = max(-1.0, min(1.0, valence))

        # Arousal: already 0–1 from _compute_arousal; clamp for safety
        arousal = max(0.0, min(1.0, arousal))

        # Dominance: already 0–1 from _compute_dominance; clamp for safety
        dominance = max(0.0, min(1.0, dominance))

        # ── Confidence ──
        abs_sentiment = abs(sentiment)
        signal_ratio = signal_count / max_signals
        trajectory_consistency = self._trajectory_consistency(trajectory_delta)

        confidence = (abs_sentiment + signal_ratio + trajectory_consistency) / 3.0
        confidence = max(0.0, min(1.0, confidence))

        mood_label = _pad_to_mood(valence, arousal, dominance)

        logger.debug(
            "FastEmotionAssessor.assess",
            valence=round(valence, 3),
            arousal=round(arousal, 3),
            dominance=round(dominance, 3),
            mood_label=mood_label,
            confidence=round(confidence, 3),
        )

        return EmotionalState(
            valence=valence,
            arousal=arousal,
            dominance=dominance,
            mood_label=mood_label,
            confidence=confidence,
            source="fast",
        )

    # ── Private helpers ───────────────────────────────────────────────────────

    def _score_sentiment(self, text_lower: str, original: str) -> tuple[float, int]:
        """Score sentiment from lexicon. Returns (score [-1,1], signal_count).

        Normalization uses the number of *matched* words (not total words) so
        that a single sentiment word in a long sentence still produces a strong
        signal.  The raw ratio is then scaled by 3x to push values closer to
        [-1, 1] for typical inputs.
        """
        words = re.findall(r"\b\w+\b", text_lower)
        if not words:
            return 0.0, 0

        pos_count = sum(1 for w in words if w in _POSITIVE_WORDS)
        neg_count = sum(1 for w in words if w in _NEGATIVE_WORDS)
        total_signals = pos_count + neg_count

        if total_signals == 0:
            return 0.0, 0

        # Normalise by number of matched words so that even one hit in a
        # long sentence produces a meaningful score (not diluted by word count).
        score = (pos_count - neg_count) / max(total_signals, 1)
        # Scale to [-1, 1] — a 3x multiplier pushes typical ratios into a
        # usable range while clamping prevents overflow.
        score = max(-1.0, min(1.0, score * 3.0))
        return score, total_signals

    def _score_topic_valence(self, text_lower: str) -> tuple[float, bool]:
        """Score topic valence from keyword detection. Returns (score, found)."""
        scores: list[float] = []
        for keyword, val in _TOPIC_VALENCE.items():
            if keyword in text_lower:
                scores.append(val)
        if not scores:
            return 0.0, False
        # Use strongest signal (max absolute value)
        strongest = max(scores, key=abs)
        return strongest, True

    def _compute_arousal(self, message: str) -> tuple[float, int]:
        """Compute arousal from structural signals. Returns (arousal 0-1, signal_count)."""
        if not message:
            return 0.3, 0  # Neutral baseline

        signal_count = 0
        components: list[float] = []

        # Length signal: longer messages = higher arousal (up to ~200 chars = max)
        length_score = min(len(message) / 200.0, 1.0)
        components.append(length_score)
        if length_score > 0.1:
            signal_count += 1

        # Caps ratio: ratio of uppercase alpha chars
        alpha_chars = [c for c in message if c.isalpha()]
        if alpha_chars:
            caps_ratio = sum(1 for c in alpha_chars if c.isupper()) / len(alpha_chars)
            components.append(caps_ratio * 1.5)  # Amplify caps signal
            if caps_ratio > 0.3:
                signal_count += 1

        # Punctuation density: ! and ? per 100 chars
        punct_count = message.count("!") + message.count("?")
        punct_density = punct_count / max(len(message) / 100.0, 1.0)
        punct_score = min(punct_density / 5.0, 1.0)  # 5 per 100 chars = max
        components.append(punct_score)
        if punct_count > 0:
            signal_count += 1

        # Emoji density
        emoji_count = sum(1 for c in message if c in _EMOJI_SENTIMENT)
        emoji_density = emoji_count / max(len(message) / 20.0, 1.0)
        emoji_score = min(emoji_density, 1.0)
        if emoji_count > 0:
            components.append(emoji_score)
            signal_count += 1

        if not components:
            return 0.3, 0

        raw = sum(components) / len(components)
        # Baseline 0.3 + scaled contribution
        arousal = 0.3 + 0.7 * min(raw, 1.0)
        return max(0.0, min(1.0, arousal)), signal_count

    def _compute_dominance(self, text_lower: str) -> float:
        """Compute dominance from agency/helplessness language. Returns 0-1."""
        agency_hits = sum(
            1 for pattern in _AGENCY_PATTERNS if re.search(pattern, text_lower)
        )
        helpless_hits = sum(
            1 for pattern in _HELPLESSNESS_PATTERNS if re.search(pattern, text_lower)
        )

        total = agency_hits + helpless_hits
        if total == 0:
            return 0.5  # Neutral baseline

        # Ratio: all agency = 1.0, all helpless = 0.0
        raw_dominance = agency_hits / total
        # Blend toward neutral: 70% raw + 30% neutral (0.5)
        # Range: [0.15, 0.85] — helplessness can drive below 0.5
        return 0.7 * raw_dominance + 0.15

    def _score_emoji_sentiment(self, message: str) -> float:
        """Compute sentiment contribution from emojis. Returns [-1, 1]."""
        scores = [_EMOJI_SENTIMENT[c] for c in message if c in _EMOJI_SENTIMENT]
        if not scores:
            return 0.0
        return max(-1.0, min(1.0, sum(scores) / len(scores)))

    def _compute_trajectory(self, message: str, recent_messages: list[str]) -> float:
        """Compare current message sentiment to recent messages. Returns delta."""
        if not recent_messages:
            return 0.0
        msgs = recent_messages[-3:]
        recent_sentiments = [self._score_sentiment(m.lower(), m)[0] for m in msgs]
        if not recent_sentiments:
            return 0.0
        avg_recent = sum(recent_sentiments) / len(recent_sentiments)
        current_sentiment, _ = self._score_sentiment(message.lower(), message)
        return current_sentiment - avg_recent

    def _trajectory_consistency(self, delta: float) -> float:
        """Convert trajectory delta to a consistency score 0-1.

        Small delta → consistent (high score). Large delta → inconsistent (low score).
        """
        return max(0.0, 1.0 - abs(delta))
