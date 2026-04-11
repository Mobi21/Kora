"""Phase 9 MCP result round-trip fuzz tests.

Generates 50 different raw MCP result dicts with random combinations of
text/json/image/resource/error shapes, feeds each through
MCPToolResult.from_mcp, and asserts invariants.

Uses random.seed(0) for determinism.
"""
from __future__ import annotations

import random
from typing import Any

import pytest

from kora_v2.mcp.results import MCPToolResult

random.seed(0)


# ---------------------------------------------------------------------------
# Fuzz helpers
# ---------------------------------------------------------------------------


def _rand_text() -> str:
    words = ["hello", "world", "kora", "mcp", "tool", "result", "data", "42"]
    return " ".join(random.choices(words, k=random.randint(1, 6)))


def _rand_text_block() -> dict[str, Any]:
    return {"type": "text", "text": _rand_text()}


def _rand_json_text_block() -> dict[str, Any]:
    """Text block carrying a JSON string."""
    payload: dict[str, Any] = {
        "key": _rand_text(),
        "value": random.randint(0, 100),
    }
    import json
    return {"type": "text", "text": json.dumps(payload)}


def _rand_native_json_block() -> dict[str, Any]:
    return {
        "type": "json",
        "data": {"field": _rand_text(), "n": random.randint(0, 1000)},
    }


def _rand_image_block() -> dict[str, Any]:
    return {
        "type": "image",
        "data": "base64abc==",
        "mimeType": random.choice(["image/png", "image/jpeg", "image/gif"]),
    }


def _rand_resource_block() -> dict[str, Any]:
    return {
        "type": "resource",
        "resource": {
            "uri": f"file:///tmp/kora/{_rand_text().replace(' ', '_')}.txt",
            "mimeType": "text/plain",
            "text": _rand_text(),
        },
    }


def _rand_error_block() -> dict[str, Any]:
    return {"type": "text", "text": f"Error: {_rand_text()}"}


def _rand_content_list(is_error: bool) -> list[dict[str, Any]]:
    """Generate a random list of content blocks."""
    block_makers = [
        _rand_text_block,
        _rand_json_text_block,
        _rand_native_json_block,
        _rand_image_block,
        _rand_resource_block,
    ]
    if is_error:
        block_makers = [_rand_error_block, _rand_text_block]

    count = random.randint(0, 4)
    return [random.choice(block_makers)() for _ in range(count)]


def _generate_raw_results(n: int) -> list[dict[str, Any]]:
    """Generate n varied raw MCP result dicts."""
    results = []
    for i in range(n):
        is_error = random.random() < 0.2  # 20% error rate
        raw: dict[str, Any] = {
            "isError": is_error,
            "content": _rand_content_list(is_error),
        }
        # Some have extra fields
        if random.random() < 0.3:
            raw["metadata"] = {"idx": i, "tag": _rand_text()}
        results.append(raw)
    return results


_FUZZ_CASES: list[dict[str, Any]] = _generate_raw_results(50)


# ---------------------------------------------------------------------------
# Fuzz tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", _FUZZ_CASES, ids=[f"case_{i}" for i in range(50)])
def test_from_mcp_never_raises(raw: dict[str, Any]) -> None:
    """MCPToolResult.from_mcp must never raise on any well-formed dict."""
    result = MCPToolResult.from_mcp(server="fuzz-srv", tool="fuzz-tool", raw=raw)
    assert result is not None


@pytest.mark.parametrize("raw", _FUZZ_CASES, ids=[f"case_{i}" for i in range(50)])
def test_is_error_matches_raw(raw: dict[str, Any]) -> None:
    """result.is_error must match the raw isError field."""
    result = MCPToolResult.from_mcp(server="fuzz-srv", tool="fuzz-tool", raw=raw)
    expected_error = bool(raw.get("isError", False))
    assert result.is_error == expected_error


@pytest.mark.parametrize("raw", _FUZZ_CASES, ids=[f"case_{i}" for i in range(50)])
def test_text_is_string(raw: dict[str, Any]) -> None:
    """.text must always be a string (possibly empty)."""
    result = MCPToolResult.from_mcp(server="fuzz-srv", tool="fuzz-tool", raw=raw)
    assert isinstance(result.text, str), (
        f"result.text must be str, got {type(result.text)}"
    )


@pytest.mark.parametrize("raw", _FUZZ_CASES, ids=[f"case_{i}" for i in range(50)])
def test_structured_data_is_none_or_dict(raw: dict[str, Any]) -> None:
    """.structured_data must be None or a dict."""
    result = MCPToolResult.from_mcp(server="fuzz-srv", tool="fuzz-tool", raw=raw)
    assert result.structured_data is None or isinstance(result.structured_data, dict), (
        f"structured_data must be None or dict, got {type(result.structured_data)}"
    )


@pytest.mark.parametrize("raw", _FUZZ_CASES, ids=[f"case_{i}" for i in range(50)])
def test_raw_equals_input(raw: dict[str, Any]) -> None:
    """.raw must be the same object as the input dict."""
    result = MCPToolResult.from_mcp(server="fuzz-srv", tool="fuzz-tool", raw=raw)
    assert result.raw is raw, ".raw must be the exact input dict object"


# ---------------------------------------------------------------------------
# Additional edge cases: non-list content and empty dict
# ---------------------------------------------------------------------------


def test_empty_raw_does_not_raise() -> None:
    raw: dict[str, Any] = {}
    result = MCPToolResult.from_mcp(server="srv", tool="t", raw=raw)
    assert result.is_error is False
    assert isinstance(result.text, str)


def test_non_list_content_does_not_raise() -> None:
    raw: dict[str, Any] = {"content": "unexpected string", "isError": False}
    result = MCPToolResult.from_mcp(server="srv", tool="t", raw=raw)
    assert isinstance(result.text, str)
    assert isinstance(result.content, list)


def test_all_block_types_in_single_result() -> None:
    """A result containing all known block types should parse without raising."""
    raw: dict[str, Any] = {
        "isError": False,
        "content": [
            {"type": "text", "text": "plain text"},
            {"type": "json", "data": {"k": "v"}},
            {"type": "image", "data": "abc==", "mimeType": "image/png"},
            {
                "type": "resource",
                "resource": {
                    "uri": "file:///tmp/test.txt",
                    "mimeType": "text/plain",
                    "text": "resource content",
                },
            },
        ],
    }
    result = MCPToolResult.from_mcp(server="srv", tool="t", raw=raw)
    assert not result.is_error
    assert isinstance(result.text, str)
    assert result.structured_data is not None
    assert result.raw is raw
