#!/usr/bin/env python3
"""Start a clean Kora GUI acceptance run and write a run manifest."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
ACCEPTANCE_ROOT = Path("/tmp/claude/kora_acceptance")
GUI_ROOT = ACCEPTANCE_ROOT / "gui_acceptance"
MEMORY_ROOT = ACCEPTANCE_ROOT / "memory"


def run(command: list[str], *, env: dict[str, str] | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=check,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fast", action="store_true", help="Start the harness in fast mode.")
    parser.add_argument("--gui-url", default="", help="Local GUI URL to test, for example http://127.0.0.1:5177.")
    parser.add_argument(
        "--skip-start",
        action="store_true",
        help="Only prepare the GUI evidence directory and manifest; do not start the harness.",
    )
    args = parser.parse_args()

    env = os.environ.copy()
    env["KORA_MEMORY__KORA_MEMORY_PATH"] = str(MEMORY_ROOT)

    if not args.skip_start:
        run([sys.executable, "-m", "tests.acceptance.automated", "stop"], env=env, check=False)
        shutil.rmtree(ACCEPTANCE_ROOT, ignore_errors=True)

    GUI_ROOT.mkdir(parents=True, exist_ok=True)

    if not args.skip_start:
        command = [sys.executable, "-m", "tests.acceptance.automated", "start"]
        if args.fast:
            command.append("--fast")
        start_result = run(command, env=env)
    else:
        start_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="skip-start requested\n")

    manifest = {
        "started_at": datetime.now(UTC).isoformat(),
        "repo": str(ROOT),
        "gui_url": args.gui_url,
        "fast": args.fast,
        "memory_root": str(MEMORY_ROOT),
        "acceptance_root": str(ACCEPTANCE_ROOT),
        "gui_evidence_root": str(GUI_ROOT),
        "start_output_tail": start_result.stdout[-4000:],
    }
    (GUI_ROOT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"GUI acceptance manifest: {GUI_ROOT / 'manifest.json'}")
    if args.gui_url:
        print(f"Open GUI target with Browser Use/computer-use: {args.gui_url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
