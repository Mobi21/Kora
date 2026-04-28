"""Executor worker normalization tests."""

from __future__ import annotations

import json

from kora_v2.agents.workers.executor import _absolute_paths_in_text, _normalize_tool_call_records


def test_normalize_tool_call_records_accepts_json_string() -> None:
    raw = json.dumps(
        [
            {
                "tool_name": "write_file",
                "args": {"path": "/tmp/out.md"},
                "result": "wrote file",
            }
        ]
    )

    records = _normalize_tool_call_records(raw)

    assert records == [
        {
            "tool_name": "write_file",
            "args": {"path": "/tmp/out.md"},
            "result_summary": "wrote file",
            "success": True,
            "duration_ms": 0,
            "timestamp": records[0]["timestamp"],
        }
    ]


def test_normalize_tool_call_records_drops_invalid_shapes() -> None:
    assert _normalize_tool_call_records("not json") == []
    assert _normalize_tool_call_records({"tool_name": "write_file"}) == []


def test_absolute_paths_in_text_extracts_file_paths() -> None:
    paths = _absolute_paths_in_text(
        "Create /tmp/kora-background-smoke.md and ignore ordinary/slashed words."
    )

    assert paths == ["/tmp/kora-background-smoke.md"]
