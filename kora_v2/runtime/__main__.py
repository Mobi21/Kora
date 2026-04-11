"""``python -m kora_v2.runtime`` — offline inspector CLI.

Usage::

    python -m kora_v2.runtime doctor
    python -m kora_v2.runtime setup
    python -m kora_v2.runtime tools

Supported topics: doctor, setup, tools, workers, permissions, session, trace,
phase-audit.

The CLI constructs a minimal offline Container (settings only — no daemon
required) and calls RuntimeInspector.inspect().  Worker/MCP/memory fields will
show ``initialized: false`` because those subsystems are only wired at daemon
startup; all read-only checks (DB files, settings sanity, module imports) run
normally.

If the daemon is running, use the HTTP inspect endpoint instead:
    curl -H "Authorization: Bearer $(cat data/.api_token)" \
         http://127.0.0.1:<port>/inspect/<topic>
"""

from __future__ import annotations

import asyncio
import json
import sys


def _build_offline_container():
    """Build a settings-only Container without starting any services."""
    from kora_v2.core.di import Container
    from kora_v2.core.settings import get_settings

    return Container(get_settings())


async def _run(topic: str, *, as_json: bool = False) -> int:
    """Run the inspector for *topic* and print to stdout.

    For the ``doctor`` topic the output is human-readable by default;
    pass ``--json`` to get raw JSON instead.

    Returns:
        0 on success, 1 on error, 2 if doctor reports unhealthy.
    """
    try:
        container = _build_offline_container()
    except Exception as exc:
        print(
            f"error: could not construct settings container: {exc}",
            file=sys.stderr,
        )
        print(
            "hint: check that your .env / KORA_* environment variables are valid.",
            file=sys.stderr,
        )
        return 1

    from kora_v2.runtime.inspector import RuntimeInspector, doctor_report_lines

    inspector = RuntimeInspector(container)

    try:
        result = await inspector.inspect(topic)
    except Exception as exc:
        print(f"error: inspector raised an exception: {exc}", file=sys.stderr)
        return 1

    if topic == "doctor" and not as_json:
        lines = doctor_report_lines(result)
        print("\n".join(lines))
    else:
        print(json.dumps(result, indent=2, default=str))

    # Exit non-zero if the doctor topic reports unhealthy
    if topic in ("doctor",) and result.get("healthy") is False:
        return 2

    return 0


def main() -> None:
    valid_topics = (
        "setup",
        "tools",
        "workers",
        "permissions",
        "session",
        "trace",
        "doctor",
        "phase-audit",
    )

    if len(sys.argv) < 2:
        print(
            f"usage: python -m kora_v2.runtime <topic> [--json]\n"
            f"topics: {', '.join(valid_topics)}",
            file=sys.stderr,
        )
        sys.exit(1)

    # Parse args: topic is first positional; --json is an optional flag
    args = sys.argv[1:]
    as_json = "--json" in args
    positional = [a for a in args if not a.startswith("--")]

    if not positional:
        print(
            f"usage: python -m kora_v2.runtime <topic> [--json]\n"
            f"topics: {', '.join(valid_topics)}",
            file=sys.stderr,
        )
        sys.exit(1)

    topic = positional[0]
    if topic not in valid_topics:
        print(
            f"error: unknown topic '{topic}'\n"
            f"valid topics: {', '.join(valid_topics)}",
            file=sys.stderr,
        )
        sys.exit(1)

    rc = asyncio.run(_run(topic, as_json=as_json))
    sys.exit(rc)


if __name__ == "__main__":
    main()
