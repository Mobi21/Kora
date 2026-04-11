"""Fake agent-browser binary for unit tests.

Usage::

    from tests.fixtures.fake_agent_browser import make_fake_binary

    def test_something(tmp_path):
        binary_path = make_fake_binary(tmp_path)
        # Pass str(binary_path) to BrowserBinary(binary_path=...)

The fake binary is a Python script that emits canned JSON responses based
on the argv pattern, without spawning a real browser.
"""
from __future__ import annotations

import stat
from pathlib import Path

# ---------------------------------------------------------------------------
# The fake binary source.  This is embedded as a string so that make_fake_binary
# can write it to a tmp directory and make it executable.
# ---------------------------------------------------------------------------

FAKE_BINARY_SCRIPT = r"""#!/usr/bin/env python3
"""r"""
import json
import sys
import time

argv = sys.argv[1:]

# ── --version ─────────────────────────────────────────────────────────────
if argv == ["--version"]:
    # Return plain text (not JSON) to match real agent-browser behaviour.
    sys.stdout.write("agent-browser 0.0.1-fake\n")
    sys.exit(0)

# ── session open ──────────────────────────────────────────────────────────
if len(argv) >= 3 and argv[0] == "session" and argv[1] == "open":
    # Find --url value
    url = "https://example.com/"
    profile = ""
    for i, a in enumerate(argv):
        if a == "--url" and i + 1 < len(argv):
            url = argv[i + 1]
        if a == "--profile" and i + 1 < len(argv):
            profile = argv[i + 1]
    payload = {
        "session_id": "fake-session-1",
        "snapshot_id": "snap-1",
        "url": url,
        "title": "Fake Page",
        "refs": [
            {"ref": "ref-1", "role": "button", "text": "Click me", "html": "<button id='btn'>Click me</button>"},
            {"ref": "ref-2", "role": "input",  "text": "",           "html": "<input id='inp' />"},
        ],
    }
    print(json.dumps(payload))
    sys.exit(0)

# ── session snapshot ──────────────────────────────────────────────────────
if len(argv) >= 3 and argv[0] == "session" and argv[1] == "snapshot":
    session_id = argv[2]
    payload = {
        "session_id": session_id,
        "snapshot_id": "snap-2",
        "url": "https://example.com/",
        "title": "Fake Page",
        "text": "Hello world text content",
        "html": "<html><body><p>Hello world text content</p></body></html>",
        "refs": [
            {"ref": "ref-1", "role": "button", "text": "Click me",  "html": "<button>Click me</button>"},
            {"ref": "ref-2", "role": "input",  "text": "",           "html": "<input />"},
        ],
    }
    print(json.dumps(payload))
    sys.exit(0)

# ── session click ─────────────────────────────────────────────────────────
if len(argv) >= 3 and argv[0] == "session" and argv[1] == "click":
    session_id = argv[2]
    ref = ""
    for i, a in enumerate(argv):
        if a == "--ref" and i + 1 < len(argv):
            ref = argv[i + 1]
    print(json.dumps({"session_id": session_id, "ref": ref, "action": "click", "ok": True}))
    sys.exit(0)

# ── session type ──────────────────────────────────────────────────────────
if len(argv) >= 3 and argv[0] == "session" and argv[1] == "type":
    session_id = argv[2]
    ref = text = ""
    for i, a in enumerate(argv):
        if a == "--ref" and i + 1 < len(argv):
            ref = argv[i + 1]
        if a == "--text" and i + 1 < len(argv):
            text = argv[i + 1]
    print(json.dumps({"session_id": session_id, "ref": ref, "text": text, "action": "type", "ok": True}))
    sys.exit(0)

# ── session fill ──────────────────────────────────────────────────────────
if len(argv) >= 3 and argv[0] == "session" and argv[1] == "fill":
    session_id = argv[2]
    ref = value = ""
    for i, a in enumerate(argv):
        if a == "--ref" and i + 1 < len(argv):
            ref = argv[i + 1]
        if a == "--value" and i + 1 < len(argv):
            value = argv[i + 1]
    print(json.dumps({"session_id": session_id, "ref": ref, "value": value, "action": "fill", "ok": True}))
    sys.exit(0)

# ── session screenshot ────────────────────────────────────────────────────
if len(argv) >= 3 and argv[0] == "session" and argv[1] == "screenshot":
    session_id = argv[2]
    out = ""
    for i, a in enumerate(argv):
        if a == "--out" and i + 1 < len(argv):
            out = argv[i + 1]
    # Write a dummy PNG header so the path is non-empty
    if out:
        try:
            with open(out, "wb") as f:
                f.write(b"\x89PNG\r\n\x1a\n")
        except OSError:
            pass
    print(json.dumps({"session_id": session_id, "out": out, "action": "screenshot", "ok": True}))
    sys.exit(0)

# ── session close ─────────────────────────────────────────────────────────
if len(argv) >= 3 and argv[0] == "session" and argv[1] == "close":
    session_id = argv[2]
    print(json.dumps({"session_id": session_id, "action": "close", "ok": True}))
    sys.exit(0)

# ── unknown / slow command (for timeout testing) ──────────────────────────
if len(argv) >= 1 and argv[0] == "--slow":
    time.sleep(999)
    sys.exit(0)

# ── error command ─────────────────────────────────────────────────────────
if len(argv) >= 1 and argv[0] == "--fail":
    sys.stderr.write("fatal: intentional failure\n")
    sys.exit(1)

sys.stderr.write(f"fake-agent-browser: unknown command: {argv!r}\n")
sys.exit(2)
"""


def make_fake_binary(tmp_path: Path) -> Path:
    """Create a fake agent-browser executable under *tmp_path* and return its path."""
    script_path = tmp_path / "agent-browser"
    script_path.write_text(FAKE_BINARY_SCRIPT)
    mode = script_path.stat().st_mode
    script_path.chmod(mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return script_path


def make_slow_binary(tmp_path: Path) -> Path:
    """Create a variant that always sleeps (for timeout tests)."""
    script = "#!/usr/bin/env python3\nimport time\ntime.sleep(999)\n"
    script_path = tmp_path / "agent-browser-slow"
    script_path.write_text(script)
    mode = script_path.stat().st_mode
    script_path.chmod(mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return script_path


def make_failing_binary(tmp_path: Path) -> Path:
    """Create a variant that always exits non-zero (for error tests)."""
    script = (
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "sys.stderr.write('fatal: simulated error\\n')\n"
        "sys.exit(1)\n"
    )
    script_path = tmp_path / "agent-browser-fail"
    script_path.write_text(script)
    mode = script_path.stat().st_mode
    script_path.chmod(mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return script_path
