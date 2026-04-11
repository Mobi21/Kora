"""Tests for kora_v2.mcp.results structured result types."""
from __future__ import annotations

import json

from kora_v2.mcp.results import MCPContentBlock, MCPToolResult

# ---------------------------------------------------------------------------
# MCPContentBlock.from_mcp
# ---------------------------------------------------------------------------


class TestMCPContentBlockFromMcp:
    """MCPContentBlock.from_mcp handles all block shapes gracefully."""

    def test_text_block(self) -> None:
        block = MCPContentBlock.from_mcp({"type": "text", "text": "hello"})
        assert block.type == "text"
        assert block.text == "hello"
        assert block.data is None

    def test_text_block_missing_text_key(self) -> None:
        block = MCPContentBlock.from_mcp({"type": "text"})
        assert block.type == "text"
        assert block.text == ""

    def test_json_as_text_block_stays_as_text(self) -> None:
        """A text block carrying a JSON string stays as a text block.

        Parsing JSON-embedded-in-text is done at the MCPToolResult level
        via structured_data, not at the block level.
        """
        payload = json.dumps({"key": "value"})
        block = MCPContentBlock.from_mcp({"type": "text", "text": payload})
        assert block.type == "text"
        assert block.text == payload

    def test_native_json_block(self) -> None:
        block = MCPContentBlock.from_mcp({"type": "json", "data": {"answer": 42}})
        assert block.type == "json"
        assert block.data == {"answer": 42}
        assert block.text is None

    def test_native_json_block_list_data(self) -> None:
        block = MCPContentBlock.from_mcp({"type": "json", "data": [1, 2, 3]})
        assert block.type == "json"
        assert block.data == {"items": [1, 2, 3]}

    def test_image_block(self) -> None:
        block = MCPContentBlock.from_mcp({
            "type": "image",
            "data": "base64abc==",
            "mimeType": "image/png",
        })
        assert block.type == "image"
        assert block.mime_type == "image/png"
        assert block.data is not None
        assert block.data["mimeType"] == "image/png"

    def test_resource_block(self) -> None:
        block = MCPContentBlock.from_mcp({
            "type": "resource",
            "resource": {
                "uri": "file:///foo.txt",
                "mimeType": "text/plain",
                "text": "file contents",
            },
        })
        assert block.type == "resource"
        assert block.text == "file contents"
        assert block.mime_type == "text/plain"
        assert isinstance(block.data, dict)
        assert block.data["uri"] == "file:///foo.txt"

    def test_unknown_block_type_graceful(self) -> None:
        """An unrecognised type should not raise."""
        block = MCPContentBlock.from_mcp({"type": "mystery", "text": "data"})
        assert block.type == "mystery"
        assert block.text == "data"

    def test_unknown_block_type_no_text(self) -> None:
        block = MCPContentBlock.from_mcp({"type": "mystery", "other": 99})
        assert block.type == "mystery"
        assert block.text is not None  # repr fallback

    def test_non_dict_input_graceful(self) -> None:
        """A non-dict input should not raise."""
        block = MCPContentBlock.from_mcp("unexpected_string")  # type: ignore[arg-type]
        assert block.type == "text"
        assert block.text is not None


# ---------------------------------------------------------------------------
# MCPToolResult.from_mcp
# ---------------------------------------------------------------------------


class TestMCPToolResultFromMcp:
    """MCPToolResult.from_mcp builds correct structures from raw payloads."""

    def test_text_only_content(self) -> None:
        raw = {"content": [{"type": "text", "text": "response text"}]}
        result = MCPToolResult.from_mcp(server="srv", tool="t", raw=raw)
        assert result.server == "srv"
        assert result.tool == "t"
        assert result.is_error is False
        assert len(result.content) == 1
        assert result.text == "response text"

    def test_is_error_true(self) -> None:
        raw = {
            "isError": True,
            "content": [{"type": "text", "text": "something went wrong"}],
        }
        result = MCPToolResult.from_mcp(server="srv", tool="t", raw=raw)
        assert result.is_error is True
        assert "something went wrong" in result.text

    def test_is_error_false_by_default(self) -> None:
        raw = {"content": []}
        result = MCPToolResult.from_mcp(server="srv", tool="t", raw=raw)
        assert result.is_error is False

    def test_missing_content_field_gives_empty_list(self) -> None:
        """A payload without a 'content' key must not crash."""
        raw: dict = {}
        result = MCPToolResult.from_mcp(server="srv", tool="t", raw=raw)
        assert result.content == []
        assert result.text == ""

    def test_json_string_text_block_structured_data(self) -> None:
        """structured_data should parse JSON embedded in a text block."""
        payload = json.dumps({"web": {"results": []}})
        raw = {"content": [{"type": "text", "text": payload}]}
        result = MCPToolResult.from_mcp(server="srv", tool="t", raw=raw)
        assert result.structured_data == {"web": {"results": []}}

    def test_native_json_block_structured_data(self) -> None:
        """structured_data should surface native json-type block data."""
        raw = {"content": [{"type": "json", "data": {"items": [1, 2]}}]}
        result = MCPToolResult.from_mcp(server="srv", tool="t", raw=raw)
        assert result.structured_data == {"items": [1, 2]}

    def test_raw_preserved(self) -> None:
        raw = {"content": [], "extra": "metadata"}
        result = MCPToolResult.from_mcp(server="srv", tool="t", raw=raw)
        assert result.raw is raw

    def test_non_list_content_fallback(self) -> None:
        """content field that is not a list should not crash."""
        raw = {"content": "unexpected string"}
        result = MCPToolResult.from_mcp(server="srv", tool="t", raw=raw)
        assert isinstance(result.content, list)
        assert len(result.content) == 1


# ---------------------------------------------------------------------------
# MCPToolResult properties
# ---------------------------------------------------------------------------


class TestMCPToolResultProperties:
    """Property behaviour: .text, .structured_data, .first_json."""

    def _result(self, content: list[MCPContentBlock]) -> MCPToolResult:
        return MCPToolResult(
            server="srv",
            tool="t",
            is_error=False,
            content=content,
            raw={},
        )

    def test_text_joins_multiple_text_blocks(self) -> None:
        result = self._result([
            MCPContentBlock(type="text", text="line 1"),
            MCPContentBlock(type="text", text="line 2"),
        ])
        assert result.text == "line 1\nline 2"

    def test_text_skips_non_text_blocks(self) -> None:
        result = self._result([
            MCPContentBlock(type="image", mime_type="image/png"),
            MCPContentBlock(type="text", text="caption"),
        ])
        assert result.text == "caption"

    def test_text_empty_when_no_text_blocks(self) -> None:
        result = self._result([MCPContentBlock(type="image", mime_type="image/png")])
        assert result.text == ""

    def test_structured_data_returns_none_when_no_json(self) -> None:
        result = self._result([MCPContentBlock(type="text", text="plain text")])
        assert result.structured_data is None

    def test_structured_data_parses_json_text(self) -> None:
        result = self._result([
            MCPContentBlock(type="text", text='{"key": "val"}'),
        ])
        assert result.structured_data == {"key": "val"}

    def test_structured_data_wraps_json_array(self) -> None:
        result = self._result([
            MCPContentBlock(type="text", text="[1, 2, 3]"),
        ])
        assert result.structured_data == {"items": [1, 2, 3]}

    def test_structured_data_prefers_data_block_over_text(self) -> None:
        result = self._result([
            MCPContentBlock(type="json", data={"from": "data_block"}),
            MCPContentBlock(type="text", text='{"from": "text_block"}'),
        ])
        assert result.structured_data == {"from": "data_block"}

    def test_first_json_is_alias_for_structured_data(self) -> None:
        result = self._result([MCPContentBlock(type="text", text='{"x": 1}')])
        assert result.first_json == result.structured_data

    def test_first_json_none_when_no_json(self) -> None:
        result = self._result([MCPContentBlock(type="text", text="hi")])
        assert result.first_json is None
