#!/usr/bin/env python3
"""Collect a lightweight index of GUI and harness evidence artifacts."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

ACCEPTANCE_ROOT = Path("/tmp/claude/kora_acceptance")
GUI_ROOT = ACCEPTANCE_ROOT / "gui_acceptance"
OUTPUT_ROOT = ACCEPTANCE_ROOT / "acceptance_output"


def file_entry(path: Path) -> dict[str, object]:
    return {
        "path": str(path),
        "exists": path.exists(),
        "size": path.stat().st_size if path.exists() else 0,
        "modified": datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat() if path.exists() else None,
    }


def main() -> int:
    GUI_ROOT.mkdir(parents=True, exist_ok=True)
    screenshots = sorted(str(path) for path in GUI_ROOT.glob("*.png"))
    text_artifacts = sorted(str(path) for path in GUI_ROOT.glob("*.txt")) + sorted(str(path) for path in GUI_ROOT.glob("*.jsonl"))

    index = {
        "collected_at": datetime.now(UTC).isoformat(),
        "gui_root": str(GUI_ROOT),
        "screenshots": screenshots,
        "text_artifacts": text_artifacts,
        "harness": {
            "acceptance_report": file_entry(OUTPUT_ROOT / "acceptance_report.md"),
            "test_log": file_entry(OUTPUT_ROOT / "test_log.jsonl"),
            "monitor": file_entry(OUTPUT_ROOT / "acceptance_monitor.md"),
            "coverage": file_entry(OUTPUT_ROOT / "coverage.md"),
        },
    }

    output = GUI_ROOT / "gui_evidence_index.json"
    output.write_text(json.dumps(index, indent=2) + "\n")
    print(f"GUI evidence index: {output}")
    print(f"screenshots={len(screenshots)} text_artifacts={len(text_artifacts)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
