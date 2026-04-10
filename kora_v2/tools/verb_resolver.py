"""Verb resolver — maps natural language verbs to tool sequences.

Used by the routing layer to suggest relevant tools based on
user intent expressed as verbs (remind, track, research, etc.).
"""

from __future__ import annotations


class DomainVerbResolver:
    """Resolves domain-specific verbs to tool names.

    Maps natural language verbs to specific tool names that can
    handle the expressed intent.
    """

    VERB_MAP: dict[str, list[str]] = {
        "remind": ["create_reminder"],
        "remember": ["recall", "store_memory"],
        "research": ["search_web", "fetch_url"],
        "plan": ["dispatch_worker"],
        "track": ["create_item", "update_item"],
        "log": ["log_medication", "log_meal"],
        "focus": ["start_focus_block"],
        "note": ["create_quick_note"],
        "routine": ["list_routines", "start_routine"],
        "search": ["search_web", "recall"],
        "find": ["recall", "search_web"],
        "schedule": ["create_reminder"],
        "check": ["routine_progress", "recall"],
    }

    def resolve(self, verb: str) -> list[str]:
        """Return tool names associated with a verb."""
        return self.VERB_MAP.get(verb.lower(), [])

    def suggest_tools(self, text: str) -> list[str]:
        """Extract verbs from text and suggest relevant tools."""
        words = set(text.lower().split())
        suggested: list[str] = []
        for verb, tools in self.VERB_MAP.items():
            if verb in words:
                for t in tools:
                    if t not in suggested:
                        suggested.append(t)
        return suggested
