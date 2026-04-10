"""Signal Scanner for memory extraction queue prioritization.

Lightweight rule-based scanner that checks each conversation turn for
high-signal content and assigns a priority level + signal type metadata.
This data is stored in the memory queue so background extraction
processes the most important turns first.

Pure pattern matching, <10ms, no LLM calls.

Priority levels:
    1 - User corrections, contradictions (highest)
    2 - Life events, new people mentioned
    3 - Strong preferences, explicit facts, life management
    4 - General conversation with substance
    5 - Low-signal (greetings, short responses)
"""

import re
from enum import StrEnum

import structlog
from pydantic import BaseModel, ConfigDict, Field

logger = structlog.get_logger()


# ============================================================
# Models (self-contained -- no V1 imports needed)
# ============================================================


class SignalType(StrEnum):
    """Types of high-signal content detected by the SignalScanner."""

    CORRECTION = "correction"
    LIFE_EVENT = "life_event"
    STRONG_PREFERENCE = "strong_preference"
    NEW_PERSON = "new_person"
    CONTRADICTION = "contradiction"
    EXPLICIT_FACT = "explicit_fact"
    GENERAL = "general"

    # Life management signals
    MEDICATION = "medication"
    FINANCE = "finance"
    MEAL = "meal"
    TIME_BLOCK = "time_block"


class ScanResult(BaseModel):
    """Result of scanning a conversation turn for memory-relevant signals."""

    priority: int = Field(
        default=5, ge=1, le=5,
        description="Queue priority (1=highest, 5=lowest)",
    )
    signal_types: list[SignalType] = Field(
        default_factory=list,
        description="Detected signal types in this turn",
    )

    model_config = ConfigDict(use_enum_values=True)

    @property
    def has_signal(self) -> bool:
        """Whether any meaningful signal was detected."""
        return self.priority < 5 and len(self.signal_types) > 0


# ============================================================
# Negation detection
# ============================================================

NEGATION_WORDS = frozenset({
    "not", "no", "never", "neither", "nor", "don't", "doesn't",
    "didn't", "won't", "wouldn't", "can't", "couldn't", "shouldn't",
    "isn't", "aren't", "wasn't", "weren't", "haven't", "hasn't",
    "hadn't", "cannot", "nothing", "nowhere", "nobody", "none",
})


def _has_negation_in_window(text: str, match_start: int, window: int = 3) -> bool:
    """Check if any negation word appears in a window before the match.

    Splits the text before the match into words and checks the last
    *window* words for negation.

    Args:
        text: Full message text (lowered).
        match_start: Character index where the regex match begins.
        window: Number of words before the match to inspect.

    Returns:
        True if a negation word is found in the window.
    """
    preceding = text[:match_start].split()
    check_words = preceding[-window:] if len(preceding) >= window else preceding
    return any(w.strip(",.!?;:'\"") in NEGATION_WORDS for w in check_words)


# ============================================================
# Pattern definitions by signal type
# ============================================================

CORRECTION_PATTERNS = [
    r"actually[,\s]+(i|my|we|it)",
    r"no[,\s]+(i|it|that|my)",
    r"i\s+(don't|didn't|haven't|am not|'m not|no longer)",
    r"not\s+anymore",
    r"i\s+changed",
    r"that's\s+(not|wrong|incorrect)",
    r"i\s+meant",
    r"let me correct",
    r"to clarify",
    r"i\s+was\s+wrong",
]

LIFE_EVENT_PATTERNS = [
    r"i\s+(got|am)\s+(engaged|married|divorced|promoted|fired)",
    r"(having|had)\s+a\s+baby",
    r"i\s+(started|left|quit|lost)\s+(a\s+new\s+|my\s+)?(job|work|position)",
    r"i\s+moved\s+to",
    r"we\s+(bought|sold)\s+(a|our)(\s+\w+)?\s+(house|home|apartment)",
    r"i\s+graduated",
    r"someone\s+(passed|died)",
    r"i\s+was\s+(diagnosed|hospitalized)",
    r"i\s+(retired|am\s+retiring)",
    r"we\s+broke\s+up",
    r"i\s+(adopted|rescued)\s+(a\s+)?(dog|cat|pet)",
]

NEW_PERSON_PATTERNS = [
    r"my\s+(friend|sister|brother|mom|dad|mother|father|wife|husband|partner|"
    r"girlfriend|boyfriend|boss|coworker|colleague|neighbor|uncle|aunt|cousin"
    r"|grandma|grandpa|son|daughter|child)\s+\w+",
    r"(met|know|introduced)\s+(someone|a\s+(guy|girl|person|woman|man))",
    r"this\s+(guy|girl|person|woman|man)\s+named\s+\w+",
]

STRONG_PREFERENCE_PATTERNS = [
    r"i\s+(love|hate|adore|despise|can't stand)",
    r"my\s+favorite",
    r"i\s+(always|never)\s+\w+",
    r"i\s+(really|absolutely)\s+(love|hate|enjoy|dislike)",
    r"i\s+prefer\s+",
    r"i'm\s+(passionate|obsessed)\s+(about|with)",
]

EXPLICIT_FACT_PATTERNS = [
    r"i\s+am\s+(a|an)\s+\w+",
    r"i\s+(have|own)\s+(a|an|two|three|\d+)\s+\w+",
    r"i\s+(work|live|study)\s+(at|in|for)\s+",
    r"i'm\s+from\s+",
    r"i\s+was\s+born\s+",
    r"my\s+(name|age|birthday)\s+is",
    r"i\s+(speak|know)\s+(english|spanish|french|chinese|arabic|\w+)",
    r"i\s+majored\s+in",
]

MEDICATION_PATTERNS = [
    r"(took|take|taking|forgot|skipped|missed)\s+(my\s+)?(meds|medication|medicine|pills?|dose)",
    r"(adderall|ritalin|vyvanse|concerta|strattera|lexapro|zoloft|wellbutrin)",
    r"(refill|pharmacy|prescription|dosage)",
    r"(took|take)\s+my\s+\w+\s*(mg|milligram)",
]

FINANCE_PATTERNS = [
    r"(spent|paid|cost|bought)\s+\$?\d+",
    r"\$\d+",
    r"(budget|over\s*budget|under\s*budget)",
    r"(rent|bill|subscription|groceries|grocery)\s+(is|was|cost)",
    r"(got|received)\s+(paid|my\s+(salary|paycheck))",
]

MEAL_PATTERNS = [
    r"(had|ate|eating|eaten|grabbed|made)\s+(breakfast|lunch|dinner|a\s+snack|some\s+\w+)",
    r"(had|ate|eating|eaten|grabbed|made)\s+.+\s+for\s+(breakfast|lunch|dinner|a\s+snack)",
    r"(skipped|skipping)\s+(breakfast|lunch|dinner|a\s+meal)",
    r"(haven't|didn't)\s+(eat|eaten)",
    r"(hungry|starving|haven't\s+eaten)",
    r"drank\s+\d+\s+glass",
]

TIME_BLOCK_PATTERNS = [
    r"(starting|started|beginning|doing)\s+(deep\s+)?work",
    r"(can't|cannot|unable\s+to)\s+focus",
    r"(distracted|procrastinating|off\s+track)",
    r"taking\s+a\s+break",
    r"(done|finished|completed)\s+(working|the\s+\w+)",
    r"(pomodoro|focus\s+block|time\s+block)",
    r"hyperfocus",
]

LOW_SIGNAL_PATTERNS = [
    r"^(hi|hey|hello|yo|sup|morning|evening|night|bye|thanks|thank you|ok|okay|"
    r"sure|yeah|yep|nope|no|yes|lol|haha|hmm|wow|nice|cool|great|good|fine"
    r"|alright|right|gotcha|understood|k|kk)[\s!.?]*$",
]


# ============================================================
# Priority mapping
# ============================================================

_PRIORITY_MAP: dict[SignalType, int] = {
    SignalType.CORRECTION: 1,
    SignalType.CONTRADICTION: 1,
    SignalType.LIFE_EVENT: 2,
    SignalType.NEW_PERSON: 2,
    SignalType.STRONG_PREFERENCE: 3,
    SignalType.EXPLICIT_FACT: 3,
    SignalType.MEDICATION: 3,
    SignalType.FINANCE: 3,
    SignalType.MEAL: 3,
    SignalType.TIME_BLOCK: 3,
    SignalType.GENERAL: 4,
}


# ============================================================
# SignalScanner
# ============================================================


class SignalScanner:
    """Rule-based scanner for detecting memory-relevant signals in conversation turns.

    Assigns a priority (1-5) and signal type tags used to order the
    memory extraction queue. Designed for <10ms execution.

    Stateless with respect to external dependencies -- takes text in,
    returns signal hits out.
    """

    def __init__(self) -> None:
        self._correction = [re.compile(p, re.IGNORECASE) for p in CORRECTION_PATTERNS]
        self._life_event = [re.compile(p, re.IGNORECASE) for p in LIFE_EVENT_PATTERNS]
        self._new_person = [re.compile(p, re.IGNORECASE) for p in NEW_PERSON_PATTERNS]
        self._strong_pref = [re.compile(p, re.IGNORECASE) for p in STRONG_PREFERENCE_PATTERNS]
        self._explicit_fact = [re.compile(p, re.IGNORECASE) for p in EXPLICIT_FACT_PATTERNS]
        self._medication = [re.compile(p, re.IGNORECASE) for p in MEDICATION_PATTERNS]
        self._finance = [re.compile(p, re.IGNORECASE) for p in FINANCE_PATTERNS]
        self._meal = [re.compile(p, re.IGNORECASE) for p in MEAL_PATTERNS]
        self._time_block = [re.compile(p, re.IGNORECASE) for p in TIME_BLOCK_PATTERNS]
        self._low_signal = [re.compile(p, re.IGNORECASE) for p in LOW_SIGNAL_PATTERNS]
        self._scans_performed = 0
        self._signals_detected = 0

    @property
    def scans_performed(self) -> int:
        return self._scans_performed

    @property
    def signals_detected(self) -> int:
        return self._signals_detected

    def scan(self, user_message: str, assistant_response: str = "") -> ScanResult:
        """Scan a conversation turn and return priority + signal types.

        Only the user message is scanned for signals. The assistant response
        is accepted for future use but currently unused.

        Args:
            user_message: The user's message text.
            assistant_response: Kora's response (reserved for future use).

        Returns:
            ScanResult with priority (1-5) and list of detected signal types.
        """
        self._scans_performed += 1
        text = user_message.strip()

        if not text:
            return ScanResult(priority=5, signal_types=[])

        # Early exit: low-signal check
        for pattern in self._low_signal:
            if pattern.match(text):
                return ScanResult(priority=5, signal_types=[])

        signals: list[SignalType] = []

        # Priority 1 signals
        if self._match_any(self._correction, text):
            signals.append(SignalType.CORRECTION)

        # Priority 2 signals
        if self._match_any(self._life_event, text, check_negation=True):
            signals.append(SignalType.LIFE_EVENT)
        if self._match_any(self._new_person, text, check_negation=True):
            signals.append(SignalType.NEW_PERSON)

        # Priority 3 signals
        if self._match_any(self._strong_pref, text, check_negation=True):
            signals.append(SignalType.STRONG_PREFERENCE)
        if self._match_any(self._explicit_fact, text, check_negation=True):
            signals.append(SignalType.EXPLICIT_FACT)

        # Priority 3 life management signals
        if self._match_any(self._medication, text):
            signals.append(SignalType.MEDICATION)
        if self._match_any(self._finance, text):
            signals.append(SignalType.FINANCE)
        if self._match_any(self._meal, text):
            signals.append(SignalType.MEAL)
        if self._match_any(self._time_block, text):
            signals.append(SignalType.TIME_BLOCK)

        # Determine priority from highest-priority signal found
        if not signals:
            if len(text) > 50:
                return ScanResult(
                    priority=4,
                    signal_types=[SignalType.GENERAL],
                )
            return ScanResult(priority=5, signal_types=[])

        self._signals_detected += len(signals)
        priority = self._priority_for_signals(signals)
        return ScanResult(priority=priority, signal_types=signals)

    @staticmethod
    def _match_any(
        patterns: list[re.Pattern[str]],
        text: str,
        check_negation: bool = False,
    ) -> bool:
        """Check if any pattern matches the text.

        Args:
            patterns: Compiled regex patterns to try.
            text: Text to search.
            check_negation: If True, skip matches preceded by negation words.
        """
        text_lower = text.lower()
        for p in patterns:
            m = p.search(text)
            if m:
                if check_negation and _has_negation_in_window(text_lower, m.start()):
                    continue
                return True
        return False

    @staticmethod
    def _priority_for_signals(signals: list[SignalType]) -> int:
        """Return the highest (lowest number) priority among detected signals."""
        return min(_PRIORITY_MAP.get(s, 5) for s in signals)
