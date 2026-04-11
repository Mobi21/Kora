"""Capability/account/action/resource-aware policy matrix.

WebSocket ``auth_request`` payload shape (stable contract)::

    {
        "type":        "auth_request",
        "request_id":  str,           # opaque hex id
        "capability":  str,           # e.g. "workspace"
        "account":     str | None,    # e.g. "personal" / None
        "action":      str,           # e.g. "gmail.send"
        "resource":    str | None,    # optional narrowing id/path/pattern
        "description": str,           # human-readable description
        "args":        dict | None,   # raw tool args (may be None)
        "mode":        str,           # ApprovalMode.value used to decide
    }
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum


class ApprovalMode(StrEnum):
    NEVER_ASK = "never_ask"                     # always allowed, no prompt
    FIRST_PER_SESSION = "first_per_session"     # prompt once per session
    FIRST_PER_TASK = "first_per_task"           # prompt once per autonomous task/turn group
    ALWAYS_ASK = "always_ask"                   # prompt every time
    DENY = "deny"                               # never allowed


@dataclass(frozen=True)
class PolicyKey:
    """Identifies a specific capability/account/action/resource combination."""

    capability: str             # "workspace", "browser", "vault", ...
    action: str                 # "gmail.send", "calendar.create_event", ...
    account: str | None = None  # e.g. "personal" / "work" / None
    resource: str | None = None # optional: id, path, or URL pattern

    def match_score(self, other: PolicyKey) -> int:
        """Return specificity score against *other*.

        0  = no match (capability differs)
        1  = capability matches only (action wildcard or differs)
        2  = capability + action match
        3  = capability + action + account match
        4  = capability + action + account + resource match (most specific)

        A rule's key is compared against a request key (``other``).
        ``None`` on the *rule* side is treated as a wildcard for that field.
        """
        if self.capability != other.capability:
            return 0

        score = 1

        # Action: None on self = wildcard matches anything on other
        if self.action is not None and self.action != other.action:
            return 0
        if self.action is not None and self.action == other.action:
            score = 2
        # If self.action is None it stays at 1 (wildcard)

        # Account
        if self.account is not None:
            if self.account != other.account:
                return 0
            score = max(score, 3)

        # Resource
        if self.resource is not None:
            if self.resource != other.resource:
                return 0
            score = max(score, 4)

        return score

    def serialize(self) -> str:
        """Stable string key for membership in granted sets."""
        return f"{self.capability}|{self.account}|{self.action}|{self.resource}"


@dataclass(frozen=True)
class PolicyRule:
    """A single rule binding a PolicyKey to an ApprovalMode."""

    key: PolicyKey
    mode: ApprovalMode
    reason: str = ""  # human-readable rationale


@dataclass
class Decision:
    """Result of a policy evaluation."""

    allowed: bool               # False means the action is denied outright
    requires_prompt: bool       # True means caller must request user approval
    mode: ApprovalMode          # the mode used
    reason: str                 # human-readable rationale


@dataclass
class SessionState:
    """What the policy layer needs to know about the current session."""

    session_id: str
    granted_this_session: set[str] = field(default_factory=set)  # serialized PolicyKeys already approved


@dataclass
class TaskState:
    """What the policy layer needs to know about the current autonomous task / turn group."""

    task_id: str | None
    granted_this_task: set[str] = field(default_factory=set)


class PolicyMatrix:
    """Ordered rule list. Most-specific match wins; if no match, default applies."""

    def __init__(
        self,
        rules: Iterable[PolicyRule] = (),
        default: ApprovalMode = ApprovalMode.ALWAYS_ASK,
    ) -> None:
        self._rules: list[PolicyRule] = list(rules)
        self._default = default

    def add(self, rule: PolicyRule) -> None:
        """Append a single rule."""
        self._rules.append(rule)

    def extend(self, rules: Iterable[PolicyRule]) -> None:
        """Append multiple rules."""
        self._rules.extend(rules)

    def lookup(self, key: PolicyKey) -> PolicyRule | None:
        """Return the most specific rule that matches *key*, or None."""
        best_rule: PolicyRule | None = None
        best_score = 0

        for rule in self._rules:
            score = rule.key.match_score(key)
            if score > best_score:
                best_score = score
                best_rule = rule

        return best_rule

    def evaluate(
        self,
        key: PolicyKey,
        session: SessionState | None = None,
        task: TaskState | None = None,
    ) -> Decision:
        """Evaluate *key* against the rule set and return a Decision.

        Precedence:
        - DENY  → denied immediately
        - NEVER_ASK → allowed, no prompt
        - FIRST_PER_SESSION → allowed; prompt only if not already in session grants
        - FIRST_PER_TASK → allowed; prompt only if not already in task grants
        - ALWAYS_ASK → allowed, always prompt
        - (no match) → apply default mode with the same logic
        """
        rule = self.lookup(key)
        if rule is not None:
            mode = rule.mode
            reason = rule.reason
        else:
            mode = self._default
            reason = "default policy"

        serial = key.serialize()

        if mode == ApprovalMode.DENY:
            return Decision(allowed=False, requires_prompt=False, mode=mode, reason=reason)

        if mode == ApprovalMode.NEVER_ASK:
            return Decision(allowed=True, requires_prompt=False, mode=mode, reason=reason)

        if mode == ApprovalMode.FIRST_PER_SESSION:
            if session is not None and serial in session.granted_this_session:
                return Decision(allowed=True, requires_prompt=False, mode=mode, reason=reason)
            return Decision(allowed=True, requires_prompt=True, mode=mode, reason=reason)

        if mode == ApprovalMode.FIRST_PER_TASK:
            if task is not None and serial in task.granted_this_task:
                return Decision(allowed=True, requires_prompt=False, mode=mode, reason=reason)
            return Decision(allowed=True, requires_prompt=True, mode=mode, reason=reason)

        # ALWAYS_ASK (also the fallthrough for any unknown future mode)
        return Decision(allowed=True, requires_prompt=True, mode=mode, reason=reason)
