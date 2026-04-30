from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tests.acceptance.persona_agent_runner import (
    RunnerConfig,
    _trusted_support_name,
    run_persona_agent,
)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_trusted_support_name_uses_privacy_boundary_fallback() -> None:
    assert (
        _trusted_support_name(
            {"privacy": {"support_boundary": "do not contact Talia automatically"}}
        )
        == "Talia Chen"
    )


def test_persona_runner_dry_run_exports_events(tmp_path: Path) -> None:
    summary = run_persona_agent(
        RunnerConfig(
            fast=True,
            phases=("fresh_setup_and_schedule_import",),
            max_turns=1,
            dry_run=True,
            include_snapshots=False,
            output_dir=tmp_path,
        )
    )

    events_path = Path(summary["events_path"])
    summary_path = Path(summary["summary_path"])

    assert summary["turns_sent"] == 1
    assert events_path.exists()
    assert summary_path.exists()

    events = _read_jsonl(events_path)
    assert [event["event"] for event in events] == [
        "run_start",
        "phase_start",
        "persona_turn_selected",
        "kora_response_observed",
        "phase_complete",
        "limit_reached",
        "run_complete",
    ]
    turn_event = next(event for event in events if event["event"] == "persona_turn_selected")
    assert "local-first" in turn_event["turn"]["text"]
    assert turn_event["phase_name"] == "fresh_setup_and_schedule_import"


def test_persona_runner_sends_through_harness_and_adapts(tmp_path: Path) -> None:
    commands: list[dict[str, Any]] = []

    def fake_harness(payload: dict[str, Any]) -> dict[str, Any]:
        commands.append(payload)
        if payload["cmd"] == "send":
            return {
                "response": (
                    "Here is a very large plan.\n"
                    + "\n".join(f"- Do thing {i}" for i in range(12))
                ),
                "tool_calls": [],
                "trace_id": "trace-1",
                "latency_ms": 12,
            }
        if payload["cmd"] == "snapshot":
            return {"path": str(tmp_path / f"{payload['name']}.json")}
        raise AssertionError(f"unexpected command: {payload}")

    summary = run_persona_agent(
        RunnerConfig(
            fast=True,
            phases=("messy_day_repair",),
            max_turns=2,
            turns_per_phase=2,
            include_snapshots=True,
            output_dir=tmp_path,
        ),
        harness_cmd=fake_harness,
    )

    assert summary["turns_sent"] == 2
    send_payloads = [command for command in commands if command["cmd"] == "send"]
    assert len(send_payloads) == 2
    assert "Reality update" in send_payloads[0]["message"]
    assert "That is too much for my brain right now" in send_payloads[1]["message"]
    assert any(command["cmd"] == "snapshot" for command in commands)
