#!/usr/bin/env python3
"""Create a GUI/runtime reconciliation shell from collected acceptance artifacts."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

ACCEPTANCE_ROOT = Path("/tmp/claude/kora_acceptance")
GUI_ROOT = ACCEPTANCE_ROOT / "gui_acceptance"
OUTPUT_ROOT = ACCEPTANCE_ROOT / "acceptance_output"


SURFACES = (
    "Global chat",
    "Today",
    "Calendar",
    "Repair",
    "Memory/Vault/Context",
    "Settings/support",
    "Notifications/proactivity",
    "Auth/error",
    "Restart continuity",
)


SCENARIOS = (
    "Calendar/state",
    "ADHD/executive dysfunction",
    "Autism/sensory",
    "Burnout/anxiety/low energy",
    "Proactivity",
    "Safety and trusted support",
)


def load_json(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def main() -> int:
    GUI_ROOT.mkdir(parents=True, exist_ok=True)
    index = load_json(GUI_ROOT / "gui_evidence_index.json")
    report_path = OUTPUT_ROOT / "acceptance_report.md"
    test_log_path = OUTPUT_ROOT / "test_log.jsonl"
    monitor_path = OUTPUT_ROOT / "acceptance_monitor.md"

    lines: list[str] = [
        "# Kora GUI Acceptance Reconciliation",
        "",
        f"Generated: {datetime.now(UTC).isoformat()}",
        "",
        "## Source Artifacts",
        "",
        f"- GUI evidence root: `{GUI_ROOT}`",
        f"- GUI evidence index: `{GUI_ROOT / 'gui_evidence_index.json'}`",
        f"- Acceptance report: `{report_path}` ({'present' if report_path.exists() else 'missing'})",
        f"- Test log: `{test_log_path}` ({'present' if test_log_path.exists() else 'missing'})",
        f"- Acceptance monitor: `{monitor_path}` ({'present' if monitor_path.exists() else 'missing'})",
        "",
        "## GUI Surface Verdicts",
        "",
    ]

    for surface in SURFACES:
        lines.extend(
            [
                f"### {surface}",
                "",
                "- Verdict: not proven",
                "- GUI evidence: pending",
                "- Runtime evidence: pending",
                "- Conflicts: pending",
                "",
            ]
        )

    lines.extend(["## Scenario Verdicts", ""])
    for scenario in SCENARIOS:
        lines.extend(
            [
                f"### {scenario}",
                "",
                "- Verdict: not proven",
                "- Visible user outcome: pending",
                "- Durable proof: pending",
                "- Gaps: pending",
                "",
            ]
        )

    screenshot_count = len(index.get("screenshots", [])) if isinstance(index.get("screenshots"), list) else 0
    text_count = len(index.get("text_artifacts", [])) if isinstance(index.get("text_artifacts"), list) else 0
    lines.extend(
        [
            "## Evidence Inventory",
            "",
            f"- Screenshots indexed: {screenshot_count}",
            f"- Text artifacts indexed: {text_count}",
            "",
            "## Non-Proof / Gaps",
            "",
            "- Any unchecked row in this reconciliation remains not proven.",
            "- Screenshots prove visibility only; use DB/log/file evidence for persistence claims.",
            "- `coverage.md` is a tracker, not final truth.",
            "",
        ]
    )

    output = GUI_ROOT / "gui_reconciliation.md"
    output.write_text("\n".join(lines))
    print(f"GUI reconciliation: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
