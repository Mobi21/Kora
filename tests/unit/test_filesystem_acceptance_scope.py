from __future__ import annotations

from pathlib import Path

from kora_v2.tools.filesystem import _resolve_safe


def test_acceptance_mode_blocks_tmp_writes_outside_acceptance_memory(monkeypatch) -> None:
    monkeypatch.setenv("KORA_ACCEPTANCE_DIR", "/tmp/claude/kora_acceptance")

    assert _resolve_safe("/tmp/kora_maya/local_schedules/doctor_portal_form.db") is None


def test_acceptance_mode_allows_memory_writes_and_auth_probe(monkeypatch) -> None:
    monkeypatch.setenv("KORA_ACCEPTANCE_DIR", "/tmp/claude/kora_acceptance")

    assert _resolve_safe(
        "/tmp/claude/kora_acceptance/memory/Life OS/Admin/doctor.md"
    ) is not None
    assert _resolve_safe("/tmp/claude/kora_acceptance/auth_probe.txt") is not None


def test_acceptance_mode_redirects_tmp_text_artifacts_to_memory(monkeypatch) -> None:
    monkeypatch.setenv("KORA_ACCEPTANCE_DIR", "/tmp/claude/kora_acceptance")
    resolved = _resolve_safe("/tmp/marcus_email_constraint.md")

    assert resolved is not None
    assert resolved == Path(
        "/tmp/claude/kora_acceptance/memory/Inbox/marcus_email_constraint.md"
    ).resolve()
