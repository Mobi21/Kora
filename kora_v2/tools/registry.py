"""Tool registry for Kora V2's tool system.

Provides the @tool decorator and ToolRegistry class for registering,
looking up, and converting tools to Anthropic's tool calling format.

Usage:
    from kora_v2.tools.registry import tool
    from kora_v2.tools.types import AuthLevel, ToolCategory

    class SearchInput(BaseModel):
        query: str = Field(..., description="Search query")

    @tool(
        name="search_memories",
        description="Search Kora's memory layers",
        category=ToolCategory.MEMORY,
        auth_level=AuthLevel.ALWAYS_ALLOWED,
    )
    async def search_memories(input: SearchInput, context: ToolContext) -> str:
        results = await context.services.retrieval_service.search(input.query)
        return format_results(results)
"""

import inspect
from collections.abc import Callable
from typing import Any

import structlog
from pydantic import BaseModel

from kora_v2.tools.types import (
    AuthLevel,
    ToolCategory,
    ToolDefinition,
)

logger = structlog.get_logger()


class _RegisteredTool:
    """Internal wrapper holding a tool's definition and callable."""

    def __init__(
        self,
        definition: ToolDefinition,
        func: Callable,
        input_model: type[BaseModel],
    ):
        self.definition = definition
        self.func = func
        self.input_model = input_model


class ToolRegistry:
    """Central registry for all tools available to Kora.

    Singleton pattern -- all @tool decorators register into the global instance.

    Methods:
        register(): Register a tool (called by @tool decorator).
        get(): Look up a tool by name.
        get_all(): Get all registered tools.
        get_by_category(): Filter tools by category.
        get_anthropic_tools(): Convert all tools to Anthropic format.
        get_tool_choice(): Build Anthropic tool_choice config.
    """

    _instance: "ToolRegistry | None" = None
    _tools: dict[str, _RegisteredTool] = {}

    def __new__(cls) -> "ToolRegistry":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._tools = {}
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset the registry (for testing)."""
        cls._tools = {}

    # Prefixes that indicate read-only tools
    _READ_ONLY_PREFIXES = ("search_", "get_", "query_", "read_", "check_")
    _WRITE_PREFIXES = ("create_", "update_", "add_", "delete_", "remove_", "plan_")

    @classmethod
    def _infer_read_only(cls, name: str) -> bool:
        """Infer whether a tool is read-only from its name prefix."""
        if any(name.startswith(p) for p in cls._READ_ONLY_PREFIXES):
            return True
        if any(name.startswith(p) for p in cls._WRITE_PREFIXES):
            return False
        # Default: not read-only (safe default)
        return False

    @classmethod
    def register(
        cls,
        name: str,
        description: str,
        category: ToolCategory,
        auth_level: AuthLevel,
        func: Callable,
        input_model: type[BaseModel],
        internal: bool = True,
        is_read_only: bool | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        """Register a tool in the registry.

        Args:
            name: Unique tool name (snake_case).
            description: Human-readable description for the LLM.
            category: Logical grouping.
            auth_level: Authorization requirement.
            func: The async callable that implements the tool.
            input_model: Pydantic model for input validation.
            internal: True for Python functions, False for MCP.
            is_read_only: Override read-only flag (auto-detected from name if None).
            timeout_seconds: Optional per-tool timeout override in seconds.
        """
        if name in cls._tools:
            logger.warning("Tool already registered, overwriting", tool=name)

        # Generate JSON Schema from Pydantic model
        schema = input_model.model_json_schema()

        # Clean up schema -- resolve $ref and anyOf from Pydantic output
        clean_schema = _clean_schema(schema)

        # Auto-detect read-only from name if not explicitly set
        read_only = is_read_only if is_read_only is not None else cls._infer_read_only(name)

        definition = ToolDefinition(
            name=name,
            description=description,
            category=category,
            auth_level=auth_level,
            parameters_schema=clean_schema,
            internal=internal,
            is_read_only=read_only,
            timeout_seconds=timeout_seconds,
        )

        cls._tools[name] = _RegisteredTool(
            definition=definition,
            func=func,
            input_model=input_model,
        )
        logger.debug("Registered tool", tool=name, category=category.value, auth=auth_level.value)

    @classmethod
    def get(cls, name: str) -> _RegisteredTool | None:
        """Get a registered tool by name."""
        return cls._tools.get(name)

    @classmethod
    def get_definition(cls, name: str) -> ToolDefinition | None:
        """Get just the definition for a tool."""
        tool = cls._tools.get(name)
        return tool.definition if tool else None

    @classmethod
    def get_all(cls) -> list[ToolDefinition]:
        """Get definitions for all registered tools."""
        return [t.definition for t in cls._tools.values()]

    @classmethod
    def get_by_category(cls, category: ToolCategory) -> list[ToolDefinition]:
        """Get all tools in a category."""
        return [
            t.definition
            for t in cls._tools.values()
            if t.definition.category == category
        ]

    @classmethod
    def get_by_auth_level(cls, auth_level: AuthLevel) -> list[ToolDefinition]:
        """Get all tools with a specific auth level."""
        return [
            t.definition
            for t in cls._tools.values()
            if t.definition.auth_level == auth_level
        ]

    @classmethod
    def get_callable(cls, name: str) -> Callable | None:
        """Get the executable function for a tool."""
        tool = cls._tools.get(name)
        return tool.func if tool else None

    @classmethod
    def get_input_model(cls, name: str) -> type[BaseModel] | None:
        """Get the Pydantic input model for a tool."""
        tool = cls._tools.get(name)
        return tool.input_model if tool else None

    @classmethod
    def get_anthropic_tools(
        cls,
        categories: set[ToolCategory] | None = None,
    ) -> list[dict[str, Any]]:
        """Convert registered tools to Anthropic tool format.

        Args:
            categories: If provided, only include tools whose category
                is in this set. If None, include all tools.

        Returns:
            List of dicts with name, description, input_schema keys
            ready for the Anthropic API tools parameter.
        """
        tools = []
        for registered in cls._tools.values():
            if categories is not None and registered.definition.category not in categories:
                continue
            tools.append(registered.definition.to_anthropic_tool())
        return tools

    @classmethod
    def get_tool_choice(cls, mode: str = "AUTO") -> dict[str, str]:
        """Build Anthropic tool_choice config.

        Args:
            mode: One of:
                - "AUTO" -- model decides whether to use tools
                - "TOOL:<name>" -- force a specific tool
                - "ANY" -- model must use at least one tool
                - "NONE" -- text-only, no tool use

        Returns:
            Dict suitable for the Anthropic API tool_choice parameter.
        """
        if mode.startswith("TOOL:"):
            tool_name = mode[5:]
            return {"type": "tool", "name": tool_name}

        mode_map = {
            "AUTO": {"type": "auto"},
            "ANY": {"type": "any"},
            "NONE": {"type": "none"},
        }
        return mode_map.get(mode, {"type": "auto"})

    @classmethod
    def create_scoped_registry(cls, tool_names: list[str]) -> "ScopedToolRegistry":
        """Create a new registry containing only the specified tools.

        Returns a ScopedToolRegistry (non-singleton) with a subset of tools.
        Used by SubAgentRunner to give subagents limited tool access.

        Args:
            tool_names: Names of tools to include in the scoped registry.

        Returns:
            ScopedToolRegistry with only the specified tools.
        """
        scoped = ScopedToolRegistry()
        for name in tool_names:
            tool = cls._tools.get(name)
            if tool:
                scoped._tools[name] = tool
            else:
                logger.warning("Scoped registry: tool not found, skipping", tool=name)
        return scoped

    @classmethod
    def tool_count(cls) -> int:
        """Number of registered tools."""
        return len(cls._tools)

    @classmethod
    def tool_names(cls) -> list[str]:
        """List all registered tool names."""
        return list(cls._tools.keys())


class ScopedToolRegistry:
    """Non-singleton tool registry for subagent scoped tool access.

    Unlike ToolRegistry (singleton), each ScopedToolRegistry is an independent
    instance containing only a subset of tools. Created via
    ToolRegistry.create_scoped_registry().
    """

    def __init__(self) -> None:
        self._tools: dict[str, _RegisteredTool] = {}

    def get(self, name: str) -> _RegisteredTool | None:
        """Get a registered tool by name."""
        return self._tools.get(name)

    def get_definition(self, name: str) -> ToolDefinition | None:
        """Get just the definition for a tool."""
        tool = self._tools.get(name)
        return tool.definition if tool else None

    def get_all(self) -> list[ToolDefinition]:
        """Get definitions for all registered tools."""
        return [t.definition for t in self._tools.values()]

    def get_callable(self, name: str) -> Callable | None:
        """Get the executable function for a tool."""
        tool = self._tools.get(name)
        return tool.func if tool else None

    def get_input_model(self, name: str) -> type[BaseModel] | None:
        """Get the Pydantic input model for a tool."""
        tool = self._tools.get(name)
        return tool.input_model if tool else None

    def get_anthropic_tools(self) -> list[dict[str, Any]]:
        """Convert scoped tools to Anthropic tool format.

        Returns:
            List of dicts with name, description, input_schema keys
            ready for the Anthropic API tools parameter.
        """
        return [registered.definition.to_anthropic_tool() for registered in self._tools.values()]

    def tool_count(self) -> int:
        """Number of tools in this scoped registry."""
        return len(self._tools)

    def tool_names(self) -> list[str]:
        """List tool names in this scoped registry."""
        return list(self._tools.keys())


def get_schema_tool(name: str, description: str, schema: dict[str, Any]) -> dict[str, Any]:
    """Create a tool dict for structured output forcing (Tool-as-Schema pattern).

    This creates an Anthropic tool definition that can be used with
    tool_choice={"type": "tool", "name": name} to force the model to
    respond with a specific JSON structure.

    Args:
        name: Tool name (e.g. "structured_response").
        description: What the structured output represents.
        schema: JSON Schema dict describing the expected output structure.

    Returns:
        Dict with name, description, input_schema ready for Anthropic API.
    """
    return {
        "name": name,
        "description": description,
        "input_schema": _clean_schema(schema),
    }


def _clean_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Clean a Pydantic JSON Schema for API compatibility.

    Resolves $ref references and flattens anyOf for Optional types.
    Anthropic uses standard JSON Schema natively, so we keep all valid
    JSON Schema keys intact -- we only need to resolve Pydantic-specific
    constructs like $defs/$ref and anyOf-based Optional patterns.
    """
    defs = schema.get("$defs", {})
    return _resolve_property(schema, defs)


def _resolve_property(prop: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    """Resolve a single property schema.

    Handles $ref resolution, anyOf/oneOf flattening for Optional types,
    and recursion into nested objects and arrays. Preserves all standard
    JSON Schema keys (type, description, enum, default, items, properties,
    required, minItems, maxItems, minimum, maximum, pattern, format, etc.).
    """
    # Resolve $ref
    if "$ref" in prop:
        ref_path = prop["$ref"].split("/")[-1]
        if ref_path in defs:
            resolved = _resolve_property(defs[ref_path], defs)
            # Preserve description from the referencing property if the ref didn't have one
            if "description" in prop and "description" not in resolved:
                resolved["description"] = prop["description"]
            return resolved
        # If ref not found, return as-is minus the $ref
        return {k: v for k, v in prop.items() if k != "$ref"}

    # Handle anyOf (usually Optional types from Pydantic)
    if "anyOf" in prop:
        non_null = [s for s in prop["anyOf"] if s.get("type") != "null"]
        if len(non_null) == 1:
            result = _resolve_property(non_null[0], defs)
            if "description" in prop:
                result["description"] = prop["description"]
            if "default" in prop:
                result["default"] = prop["default"]
            return result
        # Multiple non-null types -- keep anyOf but resolve each variant
        resolved_variants = [_resolve_property(s, defs) for s in prop["anyOf"]]
        result = {k: v for k, v in prop.items() if k not in ("anyOf", "$defs")}
        result["anyOf"] = resolved_variants
        return result

    # Build cleaned property, preserving all standard JSON Schema keys
    cleaned: dict[str, Any] = {}

    # Copy all keys except $defs (top-level only) and $ref (already handled)
    skip_keys = {"$defs", "$ref", "properties", "items", "anyOf"}
    for key, value in prop.items():
        if key not in skip_keys:
            cleaned[key] = value

    # Recurse into properties
    if "properties" in prop:
        cleaned["properties"] = {
            k: _resolve_property(v, defs) for k, v in prop["properties"].items()
        }
        if "type" not in cleaned:
            cleaned["type"] = "object"

    # Recurse into items (arrays)
    if "items" in prop:
        cleaned["items"] = _resolve_property(prop["items"], defs)

    return cleaned


def tool(
    name: str,
    description: str,
    category: ToolCategory,
    auth_level: AuthLevel = AuthLevel.ASK_FIRST,
    internal: bool = True,
    is_read_only: bool | None = None,
    timeout_seconds: float | None = None,
) -> Callable:
    """Decorator to register a function as a Kora tool.

    The decorated function must:
    1. Be async.
    2. Accept exactly 2 args: (input: SomePydanticModel, context: ToolContext).
    3. Return a string.

    Args:
        name: Unique tool name.
        description: Description shown to the LLM.
        category: Tool category for grouping.
        auth_level: Authorization requirement (default: ASK_FIRST).
        internal: Whether this is an internal Python tool (default: True).
        is_read_only: Whether this tool only reads data (auto-detected if None).
        timeout_seconds: Optional per-tool timeout override in seconds.
    """
    def decorator(func: Callable) -> Callable:
        sig = inspect.signature(func)
        params = list(sig.parameters.values())

        if len(params) < 2:
            raise ValueError(
                f"Tool '{name}': function must accept (input: BaseModel, context: ToolContext), "
                f"got {len(params)} parameters"
            )

        # Extract the input model from type annotation
        input_param = params[0]
        input_type = input_param.annotation

        if input_type is inspect.Parameter.empty:
            raise ValueError(
                f"Tool '{name}': first parameter must have a type annotation (Pydantic BaseModel)"
            )

        if not (isinstance(input_type, type) and issubclass(input_type, BaseModel)):
            raise ValueError(
                f"Tool '{name}': first parameter must be a Pydantic BaseModel, got {input_type}"
            )

        if not inspect.iscoroutinefunction(func):
            raise ValueError(f"Tool '{name}': function must be async")

        ToolRegistry.register(
            name=name,
            description=description,
            category=category,
            auth_level=auth_level,
            func=func,
            input_model=input_type,
            internal=internal,
            is_read_only=is_read_only,
            timeout_seconds=timeout_seconds,
        )

        return func

    return decorator
