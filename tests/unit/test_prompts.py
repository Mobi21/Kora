"""Tests for kora_v2.graph.prompts -- frozen prefix and dynamic suffix."""

from __future__ import annotations

from kora_v2.graph.prompts import build_dynamic_suffix, build_frozen_prefix


class TestBuildFrozenPrefix:
    """Verify frozen prefix content and structure."""

    def test_returns_nonempty_string(self) -> None:
        prefix = build_frozen_prefix()
        assert isinstance(prefix, str)
        assert len(prefix) > 100

    def test_contains_identity_section(self) -> None:
        prefix = build_frozen_prefix()
        assert "Identity" in prefix
        assert "Kora" in prefix
        assert "ADHD" in prefix

    def test_contains_core_principles(self) -> None:
        prefix = build_frozen_prefix()
        assert "Radical Honesty" in prefix
        assert "Genuine Warmth" in prefix
        assert "Fierce Reliability" in prefix
        assert "8 Core Principles" in prefix

    def test_contains_delegation_prompt(self) -> None:
        prefix = build_frozen_prefix()
        assert "Delegate" in prefix
        assert "recall()" in prefix
        assert "Planner Worker" in prefix
        assert "Executor Worker" in prefix
        assert "Reviewer Worker" in prefix
        # V2: direct tool sections
        assert "life management tools" in prefix
        assert "filesystem tools" in prefix
        # Grounding rule
        assert "NEVER confirm a tool action" in prefix

    def test_contains_failure_protocol(self) -> None:
        prefix = build_frozen_prefix()
        assert "Workers Fail" in prefix
        assert "Radical honesty" in prefix
        assert "timeout" in prefix

    def test_deterministic(self) -> None:
        """Two calls produce identical output."""
        a = build_frozen_prefix()
        b = build_frozen_prefix()
        assert a == b

    # ------------------------------------------------------------------
    # New tests: ADHD awareness, user knowledge, skill index
    # ------------------------------------------------------------------

    def test_frozen_prefix_has_adhd_section(self) -> None:
        prefix = build_frozen_prefix()
        assert "## ADHD Awareness" in prefix

    def test_frozen_prefix_has_user_knowledge_slot(self) -> None:
        prefix = build_frozen_prefix(user_model_snapshot=None)
        assert "## User Knowledge" in prefix
        assert "No user data loaded yet" in prefix

    def test_frozen_prefix_with_user_snapshot(self) -> None:
        snapshot = {"name": "Jordan", "medications": "Adderall 20mg"}
        prefix = build_frozen_prefix(user_model_snapshot=snapshot)
        assert "Jordan" in prefix
        assert "Adderall" in prefix

    def test_frozen_prefix_with_skills(self) -> None:
        skills = ["web_research", "code_work", "life_management"]
        prefix = build_frozen_prefix(skill_index=skills)
        assert "## Available Skills" in prefix
        assert "web_research" in prefix

    def test_frozen_prefix_no_skill_section_when_empty(self) -> None:
        prefix = build_frozen_prefix(skill_index=None)
        assert "## Available Skills" not in prefix

    def test_frozen_prefix_backward_compatible(self) -> None:
        """Existing callers with no args still work."""
        prefix = build_frozen_prefix()
        assert "# Identity" in prefix
        assert "# 8 Core Principles" in prefix


class TestBuildDynamicSuffix:
    """Verify dynamic suffix construction from state."""

    def test_minimal_state(self) -> None:
        suffix = build_dynamic_suffix({"turn_count": 1, "session_id": "s1"})
        assert "Turn: 1" in suffix
        assert "s1" in suffix

    def test_defaults_for_missing_keys(self) -> None:
        suffix = build_dynamic_suffix({})
        assert "0" in suffix
        assert "unknown" in suffix

    def test_includes_emotional_state_when_present(self) -> None:
        state = {
            "turn_count": 3,
            "session_id": "s2",
            "emotional_state": {"mood_label": "happy", "valence": 0.8},
        }
        suffix = build_dynamic_suffix(state)
        assert "Mood: happy" in suffix

    def test_includes_energy_when_present(self) -> None:
        state = {
            "turn_count": 2,
            "session_id": "s3",
            "energy_estimate": {"level": "high", "focus": "locked_in"},
        }
        suffix = build_dynamic_suffix(state)
        assert "Energy: high" in suffix
        assert "Focus: locked_in" in suffix

    def test_includes_pending_items_section(self) -> None:
        """Pending items are rendered as a section with individual lines."""
        state = {
            "turn_count": 1,
            "session_id": "s4",
            "pending_items": [
                {"content": "item a", "source": "bridge"},
                {"content": "item b", "source": "bridge"},
            ],
        }
        suffix = build_dynamic_suffix(state)
        assert "## Pending Items" in suffix
        assert "item a" in suffix
        assert "item b" in suffix

    def test_no_pending_section_when_empty(self) -> None:
        suffix = build_dynamic_suffix({"turn_count": 1, "session_id": "s5"})
        assert "Pending" not in suffix

    # ------------------------------------------------------------------
    # New tests: compaction, session bridge, recitation, rich rendering
    # ------------------------------------------------------------------

    def test_dynamic_suffix_full(self) -> None:
        state = {
            "turn_count": 5,
            "session_id": "test123",
            "emotional_state": {
                "valence": 0.6, "arousal": 0.4, "dominance": 0.7,
                "mood_label": "content", "confidence": 0.8,
            },
            "energy_estimate": {
                "level": "high", "focus": "locked_in",
            },
            "pending_items": [
                {"source": "bridge", "content": "Pick alarm time", "priority": 1},
            ],
            "compaction_summary": "",
            "session_bridge": {
                "summary": "Last session we discussed routines",
                "open_threads": ["alarm time"],
            },
        }
        suffix = build_dynamic_suffix(state)
        assert "content" in suffix.lower()      # mood label
        assert "high" in suffix.lower()          # energy
        assert "Pick alarm time" in suffix
        assert "routines" in suffix.lower()
        assert "alarm time" in suffix            # open thread in recitation

    def test_dynamic_suffix_with_compaction(self) -> None:
        state = {
            "turn_count": 50,
            "session_id": "test123",
            "compaction_summary": (
                "## Goal\nPlan morning routine\n## Progress\n### Done\n- Discussed meds"
            ),
        }
        suffix = build_dynamic_suffix(state)
        assert "Plan morning routine" in suffix

    def test_dynamic_suffix_minimal(self) -> None:
        """With minimal state, should still produce valid suffix."""
        state = {"turn_count": 1, "session_id": "abc"}
        suffix = build_dynamic_suffix(state)
        assert "abc" in suffix
        assert "1" in suffix

    def test_dynamic_suffix_session_and_turn_on_one_line(self) -> None:
        """Session and turn are combined on one line in the new format."""
        suffix = build_dynamic_suffix({"turn_count": 7, "session_id": "xyz"})
        assert "Session: xyz | Turn: 7" in suffix

    def test_dynamic_suffix_confidence_formatted(self) -> None:
        """Emotional confidence is rendered with one decimal place."""
        state = {
            "turn_count": 1,
            "session_id": "s",
            "emotional_state": {"mood_label": "calm", "confidence": 0.75},
        }
        suffix = build_dynamic_suffix(state)
        assert "0.8" in suffix  # 0.75 rounds to 0.8 with :.1f

    def test_dynamic_suffix_pending_capped_at_five(self) -> None:
        """Only the first 5 pending items are rendered."""
        items = [{"content": f"item{i}", "source": "x"} for i in range(8)]
        state = {"turn_count": 1, "session_id": "s", "pending_items": items}
        suffix = build_dynamic_suffix(state)
        assert "item4" in suffix
        assert "item5" not in suffix

    def test_dynamic_suffix_recitation_block_present_with_pending(self) -> None:
        state = {
            "turn_count": 1,
            "session_id": "s",
            "pending_items": [{"content": "do thing", "source": "x"}],
        }
        suffix = build_dynamic_suffix(state)
        assert "## Remember" in suffix
        assert "1 pending item(s)" in suffix

    def test_dynamic_suffix_no_remember_block_without_context(self) -> None:
        """No recitation block when there are no pending items or bridge."""
        state = {"turn_count": 1, "session_id": "s"}
        suffix = build_dynamic_suffix(state)
        assert "## Remember" not in suffix

    def test_dynamic_suffix_renders_prefetched_orchestration_tasks(self) -> None:
        state = {
            "turn_count": 3,
            "session_id": "s",
            "_orchestration_tasks": [
                {
                    "stage": "research",
                    "state": "completed",
                    "goal": "local-first productivity tools",
                    "result_summary": "report written with 5 sources",
                }
            ],
        }

        suffix = build_dynamic_suffix(state)

        assert "## Relevant Background Work" in suffix
        assert "Mention completed/failed items" in suffix
        assert "research: completed" in suffix
        assert "local-first productivity tools" in suffix
        assert "report written with 5 sources" in suffix

    def test_dynamic_suffix_object_emotional_state(self) -> None:
        """Supports object-style (non-dict) emotional state."""
        class EmotionObj:
            mood_label = "curious"
            confidence = 0.9

        state = {
            "turn_count": 1,
            "session_id": "s",
            "emotional_state": EmotionObj(),
        }
        suffix = build_dynamic_suffix(state)
        assert "curious" in suffix

    def test_dynamic_suffix_object_energy(self) -> None:
        """Supports object-style (non-dict) energy estimate."""
        class EnergyObj:
            level = "medium"
            focus = "scattered"

        state = {
            "turn_count": 1,
            "session_id": "s",
            "energy_estimate": EnergyObj(),
        }
        suffix = build_dynamic_suffix(state)
        assert "medium" in suffix
        assert "scattered" in suffix
