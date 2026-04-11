"""Structured MCP tool result types."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class MCPContentBlock:
    """One content block from an MCP tool result.

    MCP tool results are a list of typed blocks (text, image, resource, etc.).
    """

    type: str                           # "text", "image", "resource", "json", ...
    text: str | None = None             # present when type == "text" (or JSON-as-text)
    data: dict[str, Any] | None = None  # present when block is structured JSON or image/resource metadata
    mime_type: str | None = None

    @classmethod
    def from_mcp(cls, block: dict[str, Any]) -> MCPContentBlock:
        """Parse a raw MCP content block dict into MCPContentBlock.

        Handles text blocks, JSON-string text blocks (best-effort parse),
        and generic data blocks. Never raises — unknown shapes become a text
        block with the repr of the data.
        """
        if not isinstance(block, dict):
            return cls(type="text", text=repr(block))

        block_type = str(block.get("type", "text"))

        if block_type == "text":
            return cls(type="text", text=str(block.get("text", "")))

        if block_type == "json":
            # Native structured JSON block
            raw_data = block.get("data")
            if isinstance(raw_data, dict):
                return cls(type="json", data=raw_data)
            if isinstance(raw_data, list):
                return cls(type="json", data={"items": raw_data})
            # Fallback: treat data repr as text
            return cls(type="json", text=repr(raw_data))

        if block_type == "image":
            mime = block.get("mimeType") or block.get("mime_type")
            img_data = block.get("data")
            data_dict: dict[str, Any] = {}
            if img_data is not None:
                data_dict["data"] = img_data
            if mime:
                data_dict["mimeType"] = mime
            return cls(
                type="image",
                mime_type=mime,
                data=data_dict or None,
            )

        if block_type == "resource":
            resource = block.get("resource")
            if isinstance(resource, dict):
                mime = resource.get("mimeType") or resource.get("mime_type")
                text = resource.get("text")
                return cls(
                    type="resource",
                    text=text,
                    mime_type=mime,
                    data=resource,
                )
            return cls(type="resource", data=block)

        # Unknown block type — preserve as much as possible
        text_val = block.get("text")
        if text_val is not None:
            return cls(type=block_type, text=str(text_val))
        return cls(type=block_type, text=repr(block))


@dataclass
class MCPToolResult:
    """Structured result from an MCP tool call.

    Wraps the raw JSON-RPC result so callers can:
      - access joined text via .text (legacy-compatible)
      - access the first JSON block via .structured_data / .first_json
      - access the full raw payload via .raw (for ids, metadata, follow-up calls)
      - check .is_error for isError: true responses
    """

    server: str
    tool: str
    is_error: bool
    content: list[MCPContentBlock]
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        """Joined text from all text content blocks — backwards compatible with legacy call_tool."""
        parts: list[str] = []
        for block in self.content:
            if block.type == "text" and block.text is not None:
                parts.append(block.text)
        return "\n".join(parts)

    @property
    def structured_data(self) -> dict[str, Any] | None:
        """The first parsed-JSON block's data, or None.

        A block counts as structured JSON if its raw MCP block was:
          - type != "text" and has a `.data` dict
          - type == "text" but the text parses as a JSON object/array
        """
        for block in self.content:
            if block.data is not None:
                return block.data
            if block.type == "text" and block.text is not None:
                try:
                    parsed = json.loads(block.text)
                except (json.JSONDecodeError, ValueError):
                    continue
                if isinstance(parsed, dict):
                    return parsed
                if isinstance(parsed, list):
                    return {"items": parsed}
        return None

    @property
    def first_json(self) -> dict[str, Any] | None:
        """Alias for structured_data."""
        return self.structured_data

    @classmethod
    def from_mcp(
        cls,
        *,
        server: str,
        tool: str,
        raw: dict[str, Any],
    ) -> MCPToolResult:
        """Parse a raw MCP tool-call JSON-RPC ``result`` payload.

        Preserves:
          - isError flag
          - all content blocks (text, image, resource, structured)
          - the full raw dict for advanced use

        Never raises on unexpected shapes — falls back to a single text block
        containing the repr.
        """
        is_error = bool(raw.get("isError", False))

        raw_content = raw.get("content")
        if not isinstance(raw_content, list):
            # Unexpected shape: wrap whatever we got in a text block
            if raw_content is not None:
                blocks = [MCPContentBlock(type="text", text=repr(raw_content))]
            else:
                blocks = []
            return cls(
                server=server,
                tool=tool,
                is_error=is_error,
                content=blocks,
                raw=raw,
            )

        blocks = []
        for item in raw_content:
            try:
                if isinstance(item, dict):
                    blocks.append(MCPContentBlock.from_mcp(item))
                else:
                    blocks.append(MCPContentBlock(type="text", text=repr(item)))
            except Exception:  # noqa: BLE001
                blocks.append(MCPContentBlock(type="text", text=repr(item)))

        return cls(
            server=server,
            tool=tool,
            is_error=is_error,
            content=blocks,
            raw=raw,
        )
