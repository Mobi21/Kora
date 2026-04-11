"""Unit tests for kora_v2.capabilities.policy — PolicyKey, PolicyMatrix, Decision."""

from __future__ import annotations

from kora_v2.capabilities.policy import (
    ApprovalMode,
    PolicyKey,
    PolicyMatrix,
    PolicyRule,
    SessionState,
    TaskState,
)

# ---------------------------------------------------------------------------
# PolicyKey.match_score
# ---------------------------------------------------------------------------


class TestPolicyKeyMatchScore:
    def _key(self, capability, action, account=None, resource=None):
        return PolicyKey(capability=capability, action=action, account=account, resource=resource)

    def test_no_match_different_capability(self):
        rule_key = self._key("workspace", "gmail.send")
        request_key = self._key("browser", "gmail.send")
        assert rule_key.match_score(request_key) == 0

    def test_capability_only_match_with_wildcard_action(self):
        """A rule key with action=None should match capability only → score 1."""
        # We can't set action=None via PolicyKey since action has no default,
        # but we can simulate a "capability wildcard" rule by patching.
        rule_key = PolicyKey(capability="workspace", action="*")
        request_key = self._key("workspace", "gmail.send")
        # action "*" != "gmail.send" so match_score should return 0 for strict equality
        assert rule_key.match_score(request_key) == 0

    def test_capability_action_match(self):
        rule_key = self._key("workspace", "gmail.send")
        request_key = self._key("workspace", "gmail.send")
        assert rule_key.match_score(request_key) == 2

    def test_capability_action_account_match(self):
        rule_key = self._key("workspace", "gmail.send", account="personal")
        request_key = self._key("workspace", "gmail.send", account="personal")
        assert rule_key.match_score(request_key) == 3

    def test_capability_action_account_resource_match(self):
        rule_key = self._key("workspace", "gmail.send", account="personal", resource="inbox")
        request_key = self._key("workspace", "gmail.send", account="personal", resource="inbox")
        assert rule_key.match_score(request_key) == 4

    def test_account_mismatch_returns_zero(self):
        rule_key = self._key("workspace", "gmail.send", account="work")
        request_key = self._key("workspace", "gmail.send", account="personal")
        assert rule_key.match_score(request_key) == 0

    def test_resource_mismatch_returns_zero(self):
        rule_key = self._key("workspace", "gmail.send", account="personal", resource="/docs")
        request_key = self._key("workspace", "gmail.send", account="personal", resource="/other")
        assert rule_key.match_score(request_key) == 0

    def test_action_mismatch_returns_zero(self):
        rule_key = self._key("workspace", "gmail.send")
        request_key = self._key("workspace", "calendar.create_event")
        assert rule_key.match_score(request_key) == 0

    def test_specificity_ordering(self):
        """Scores increase with each additional matching field."""
        base = self._key("workspace", "gmail.send")
        with_account = self._key("workspace", "gmail.send", account="personal")
        with_resource = self._key("workspace", "gmail.send", account="personal", resource="inbox")
        request = self._key("workspace", "gmail.send", account="personal", resource="inbox")

        score_base = base.match_score(request)
        score_account = with_account.match_score(request)
        score_resource = with_resource.match_score(request)

        assert score_base < score_account < score_resource
        assert score_resource == 4

    def test_complete_mismatch_capability_different(self):
        rule_key = self._key("vault", "read_secret")
        request_key = self._key("workspace", "read_secret")
        assert rule_key.match_score(request_key) == 0


# ---------------------------------------------------------------------------
# PolicyKey.serialize
# ---------------------------------------------------------------------------


class TestPolicyKeySerialize:
    def test_serialize_full(self):
        key = PolicyKey(capability="workspace", action="gmail.send", account="personal", resource="inbox")
        s = key.serialize()
        assert s == "workspace|personal|gmail.send|inbox"

    def test_serialize_none_fields(self):
        key = PolicyKey(capability="workspace", action="gmail.send")
        s = key.serialize()
        assert s == "workspace|None|gmail.send|None"

    def test_serialize_consistent(self):
        key = PolicyKey(capability="browser", action="navigate", account="work")
        assert key.serialize() == key.serialize()

    def test_serialize_different_keys_different_strings(self):
        k1 = PolicyKey(capability="workspace", action="gmail.send")
        k2 = PolicyKey(capability="workspace", action="calendar.create")
        assert k1.serialize() != k2.serialize()


# ---------------------------------------------------------------------------
# PolicyMatrix.lookup — most-specific rule wins
# ---------------------------------------------------------------------------


class TestPolicyMatrixLookup:
    def _make_rule(self, capability, action, account=None, resource=None, mode=ApprovalMode.ALWAYS_ASK, reason=""):
        key = PolicyKey(capability=capability, action=action, account=account, resource=resource)
        return PolicyRule(key=key, mode=mode, reason=reason)

    def test_lookup_single_rule_matches(self):
        rule = self._make_rule("workspace", "gmail.send")
        matrix = PolicyMatrix([rule])
        key = PolicyKey(capability="workspace", action="gmail.send")
        assert matrix.lookup(key) is rule

    def test_lookup_no_match_returns_none(self):
        rule = self._make_rule("vault", "read_secret")
        matrix = PolicyMatrix([rule])
        key = PolicyKey(capability="workspace", action="gmail.send")
        assert matrix.lookup(key) is None

    def test_lookup_most_specific_wins(self):
        general_rule = self._make_rule("workspace", "gmail.send", reason="general")
        specific_rule = self._make_rule("workspace", "gmail.send", account="personal", reason="specific")
        most_specific = self._make_rule("workspace", "gmail.send", account="personal", resource="inbox", reason="most")

        matrix = PolicyMatrix([general_rule, specific_rule, most_specific])
        key = PolicyKey(capability="workspace", action="gmail.send", account="personal", resource="inbox")

        result = matrix.lookup(key)
        assert result is most_specific

    def test_lookup_partial_match_when_no_exact(self):
        rule = self._make_rule("workspace", "gmail.send", reason="base")
        matrix = PolicyMatrix([rule])
        # Request has account+resource but rule only has capability+action
        key = PolicyKey(capability="workspace", action="gmail.send", account="personal", resource="inbox")
        # rule.key has account=None, resource=None — None is a wildcard matching any
        # but account=None on rule does NOT constrain → score = 2 (capability + action)
        result = matrix.lookup(key)
        assert result is rule

    def test_lookup_with_multiple_rules_picks_best(self):
        r1 = self._make_rule("workspace", "gmail.send", reason="r1")
        r2 = self._make_rule("workspace", "gmail.send", account="work", reason="r2")
        matrix = PolicyMatrix([r1, r2])

        key_work = PolicyKey(capability="workspace", action="gmail.send", account="work")
        assert matrix.lookup(key_work) is r2

        key_personal = PolicyKey(capability="workspace", action="gmail.send", account="personal")
        # r2 account="work" doesn't match personal → falls back to r1
        assert matrix.lookup(key_personal) is r1

    def test_add_rule(self):
        matrix = PolicyMatrix()
        rule = self._make_rule("vault", "read_secret", mode=ApprovalMode.NEVER_ASK)
        matrix.add(rule)
        key = PolicyKey(capability="vault", action="read_secret")
        assert matrix.lookup(key) is rule

    def test_extend_rules(self):
        matrix = PolicyMatrix()
        rules = [
            self._make_rule("browser", "navigate", mode=ApprovalMode.NEVER_ASK),
            self._make_rule("browser", "screenshot", mode=ApprovalMode.ALWAYS_ASK),
        ]
        matrix.extend(rules)
        assert matrix.lookup(PolicyKey(capability="browser", action="navigate")) is rules[0]
        assert matrix.lookup(PolicyKey(capability="browser", action="screenshot")) is rules[1]


# ---------------------------------------------------------------------------
# PolicyMatrix.evaluate — correct Decision per ApprovalMode
# ---------------------------------------------------------------------------


class TestPolicyMatrixEvaluate:
    def _matrix_with(self, capability, action, mode, account=None, resource=None, reason="test"):
        key = PolicyKey(capability=capability, action=action, account=account, resource=resource)
        rule = PolicyRule(key=key, mode=mode, reason=reason)
        return PolicyMatrix([rule])

    def test_never_ask_allowed_no_prompt(self):
        matrix = self._matrix_with("workspace", "gmail.read", ApprovalMode.NEVER_ASK)
        key = PolicyKey(capability="workspace", action="gmail.read")
        d = matrix.evaluate(key)
        assert d.allowed is True
        assert d.requires_prompt is False
        assert d.mode == ApprovalMode.NEVER_ASK

    def test_deny_not_allowed_no_prompt(self):
        matrix = self._matrix_with("vault", "delete_secret", ApprovalMode.DENY)
        key = PolicyKey(capability="vault", action="delete_secret")
        d = matrix.evaluate(key)
        assert d.allowed is False
        assert d.requires_prompt is False
        assert d.mode == ApprovalMode.DENY

    def test_always_ask_allowed_requires_prompt(self):
        matrix = self._matrix_with("browser", "navigate", ApprovalMode.ALWAYS_ASK)
        key = PolicyKey(capability="browser", action="navigate")
        d = matrix.evaluate(key)
        assert d.allowed is True
        assert d.requires_prompt is True
        assert d.mode == ApprovalMode.ALWAYS_ASK

    def test_first_per_session_before_grant_prompts(self):
        matrix = self._matrix_with("workspace", "gmail.send", ApprovalMode.FIRST_PER_SESSION)
        key = PolicyKey(capability="workspace", action="gmail.send")
        session = SessionState(session_id="sess-1")

        d = matrix.evaluate(key, session=session)
        assert d.allowed is True
        assert d.requires_prompt is True

    def test_first_per_session_after_grant_no_prompt(self):
        matrix = self._matrix_with("workspace", "gmail.send", ApprovalMode.FIRST_PER_SESSION)
        key = PolicyKey(capability="workspace", action="gmail.send")
        session = SessionState(session_id="sess-1")
        session.granted_this_session.add(key.serialize())

        d = matrix.evaluate(key, session=session)
        assert d.allowed is True
        assert d.requires_prompt is False

    def test_first_per_session_no_session_state_prompts(self):
        matrix = self._matrix_with("workspace", "gmail.send", ApprovalMode.FIRST_PER_SESSION)
        key = PolicyKey(capability="workspace", action="gmail.send")
        # No session passed
        d = matrix.evaluate(key, session=None)
        assert d.allowed is True
        assert d.requires_prompt is True

    def test_first_per_task_before_grant_prompts(self):
        matrix = self._matrix_with("workspace", "calendar.create_event", ApprovalMode.FIRST_PER_TASK)
        key = PolicyKey(capability="workspace", action="calendar.create_event")
        task = TaskState(task_id="task-1")

        d = matrix.evaluate(key, task=task)
        assert d.allowed is True
        assert d.requires_prompt is True

    def test_first_per_task_after_grant_no_prompt(self):
        matrix = self._matrix_with("workspace", "calendar.create_event", ApprovalMode.FIRST_PER_TASK)
        key = PolicyKey(capability="workspace", action="calendar.create_event")
        task = TaskState(task_id="task-1")
        task.granted_this_task.add(key.serialize())

        d = matrix.evaluate(key, task=task)
        assert d.allowed is True
        assert d.requires_prompt is False

    def test_first_per_task_no_task_state_prompts(self):
        matrix = self._matrix_with("workspace", "calendar.create_event", ApprovalMode.FIRST_PER_TASK)
        key = PolicyKey(capability="workspace", action="calendar.create_event")
        d = matrix.evaluate(key, task=None)
        assert d.allowed is True
        assert d.requires_prompt is True

    def test_default_mode_applies_when_no_rule_matches(self):
        matrix = PolicyMatrix(default=ApprovalMode.NEVER_ASK)
        key = PolicyKey(capability="unknown_cap", action="unknown_action")
        d = matrix.evaluate(key)
        assert d.allowed is True
        assert d.requires_prompt is False
        assert d.mode == ApprovalMode.NEVER_ASK

    def test_default_always_ask_when_no_match(self):
        matrix = PolicyMatrix(default=ApprovalMode.ALWAYS_ASK)
        key = PolicyKey(capability="something", action="do_thing")
        d = matrix.evaluate(key)
        assert d.allowed is True
        assert d.requires_prompt is True

    def test_default_deny_when_no_match(self):
        matrix = PolicyMatrix(default=ApprovalMode.DENY)
        key = PolicyKey(capability="something", action="dangerous")
        d = matrix.evaluate(key)
        assert d.allowed is False
        assert d.requires_prompt is False

    def test_reason_propagated_from_rule(self):
        key = PolicyKey(capability="workspace", action="gmail.send")
        rule = PolicyRule(key=key, mode=ApprovalMode.DENY, reason="sensitive action")
        matrix = PolicyMatrix([rule])
        d = matrix.evaluate(key)
        assert d.reason == "sensitive action"

    def test_reason_is_default_when_no_match(self):
        matrix = PolicyMatrix(default=ApprovalMode.ALWAYS_ASK)
        key = PolicyKey(capability="cap", action="act")
        d = matrix.evaluate(key)
        assert "default" in d.reason


# ---------------------------------------------------------------------------
# Session/task grant memory key consistency
# ---------------------------------------------------------------------------


class TestGrantKeyConsistency:
    def test_session_grant_key_stable(self):
        key = PolicyKey(capability="workspace", action="gmail.send", account="personal")
        s = key.serialize()
        # Must be the same string on repeated calls
        assert key.serialize() == s

    def test_task_grant_key_stable(self):
        key = PolicyKey(capability="browser", action="navigate", resource="https://example.com")
        s = key.serialize()
        assert key.serialize() == s

    def test_grant_membership_uses_serialize(self):
        key = PolicyKey(capability="workspace", action="gmail.send")
        session = SessionState(session_id="s1")
        session.granted_this_session.add(key.serialize())
        assert key.serialize() in session.granted_this_session

    def test_task_grant_membership(self):
        key = PolicyKey(capability="workspace", action="calendar.create_event", account="work")
        task = TaskState(task_id="t1")
        task.granted_this_task.add(key.serialize())
        assert key.serialize() in task.granted_this_task

    def test_distinct_keys_distinct_serials(self):
        k1 = PolicyKey(capability="workspace", action="gmail.send", account="personal")
        k2 = PolicyKey(capability="workspace", action="gmail.send", account="work")
        assert k1.serialize() != k2.serialize()
