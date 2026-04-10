"""Tests for kora_v2.tools.registry — Tool registration, lookup, and Anthropic format."""

import pytest
from pydantic import BaseModel, Field

from kora_v2.tools.registry import ToolRegistry, _clean_schema, get_schema_tool, tool
from kora_v2.tools.types import AuthLevel, ToolCategory


@pytest.fixture(autouse=True)
def _reset_registry():
    """Reset the ToolRegistry singleton before each test to avoid pollution."""
    ToolRegistry.reset()
    yield
    ToolRegistry.reset()


class SearchInput(BaseModel):
    """Input model for test tool."""
    query: str = Field(..., description="Search query")
    limit: int = Field(10, description="Max results")


class CreateInput(BaseModel):
    """Input model for a write tool."""
    title: str = Field(..., description="Item title")
    body: str = Field("", description="Item body")


class TestToolRegistration:
    """Test the @tool decorator and ToolRegistry.register."""

    def test_tool_registration_via_decorator(self):
        """Register a tool via @tool decorator, verify it's in registry."""

        @tool(
            name="search_test",
            description="Test search tool",
            category=ToolCategory.MEMORY,
            auth_level=AuthLevel.ALWAYS_ALLOWED,
        )
        async def search_test(input: SearchInput, context: object) -> str:
            return "results"

        reg = ToolRegistry.get("search_test")
        assert reg is not None
        assert reg.definition.name == "search_test"
        assert reg.definition.category == ToolCategory.MEMORY
        assert reg.definition.auth_level == AuthLevel.ALWAYS_ALLOWED
        assert reg.definition.is_read_only is True  # inferred from "search_" prefix

    def test_tool_registration_direct(self):
        """Register a tool directly via ToolRegistry.register."""

        async def my_tool(input: SearchInput, context: object) -> str:
            return "ok"

        ToolRegistry.register(
            name="direct_tool",
            description="A direct tool",
            category=ToolCategory.TASKS,
            auth_level=AuthLevel.ASK_FIRST,
            func=my_tool,
            input_model=SearchInput,
        )

        assert ToolRegistry.tool_count() == 1
        assert "direct_tool" in ToolRegistry.tool_names()

    def test_read_only_inference(self):
        """Tools with search_/get_/query_ prefix should be inferred as read-only."""
        async def f(input: SearchInput, context: object) -> str:
            return ""

        for prefix_name, expected_ro in [
            ("search_stuff", True),
            ("get_item", True),
            ("query_db", True),
            ("read_file", True),
            ("check_status", True),
            ("create_item", False),
            ("update_item", False),
            ("delete_item", False),
            ("custom_action", False),  # unknown prefix defaults to not read-only
        ]:
            ToolRegistry.reset()
            ToolRegistry.register(
                name=prefix_name,
                description="test",
                category=ToolCategory.TASKS,
                auth_level=AuthLevel.ALWAYS_ALLOWED,
                func=f,
                input_model=SearchInput,
            )
            defn = ToolRegistry.get_definition(prefix_name)
            assert defn.is_read_only is expected_ro, f"{prefix_name} should be read_only={expected_ro}"

    def test_read_only_explicit_override(self):
        """Explicit is_read_only should override name-based inference."""
        async def f(input: SearchInput, context: object) -> str:
            return ""

        ToolRegistry.register(
            name="search_but_writes",
            description="test",
            category=ToolCategory.MEMORY,
            auth_level=AuthLevel.ALWAYS_ALLOWED,
            func=f,
            input_model=SearchInput,
            is_read_only=False,
        )
        defn = ToolRegistry.get_definition("search_but_writes")
        assert defn.is_read_only is False


class TestAnthropicFormat:
    """Test conversion to Anthropic tool format."""

    def test_anthropic_format(self):
        """get_anthropic_tools should return correct Anthropic API format."""

        @tool(
            name="test_tool_fmt",
            description="A tool for testing format",
            category=ToolCategory.MEMORY,
            auth_level=AuthLevel.ALWAYS_ALLOWED,
        )
        async def test_tool_fmt(input: SearchInput, context: object) -> str:
            return ""

        tools = ToolRegistry.get_anthropic_tools()
        assert len(tools) == 1
        t = tools[0]
        assert t["name"] == "test_tool_fmt"
        assert t["description"] == "A tool for testing format"
        assert "input_schema" in t
        assert "properties" in t["input_schema"]
        assert "query" in t["input_schema"]["properties"]

    def test_anthropic_tools_category_filter(self):
        """get_anthropic_tools with categories should filter correctly."""
        async def f(input: SearchInput, context: object) -> str:
            return ""

        ToolRegistry.register(
            name="mem_tool", description="mem", category=ToolCategory.MEMORY,
            auth_level=AuthLevel.ALWAYS_ALLOWED, func=f, input_model=SearchInput,
        )
        ToolRegistry.register(
            name="task_tool", description="task", category=ToolCategory.TASKS,
            auth_level=AuthLevel.ALWAYS_ALLOWED, func=f, input_model=SearchInput,
        )

        memory_only = ToolRegistry.get_anthropic_tools(categories={ToolCategory.MEMORY})
        assert len(memory_only) == 1
        assert memory_only[0]["name"] == "mem_tool"

    def test_tool_choice_modes(self):
        """get_tool_choice should return correct configs for each mode."""
        assert ToolRegistry.get_tool_choice("AUTO") == {"type": "auto"}
        assert ToolRegistry.get_tool_choice("ANY") == {"type": "any"}
        assert ToolRegistry.get_tool_choice("NONE") == {"type": "none"}
        assert ToolRegistry.get_tool_choice("TOOL:my_func") == {"type": "tool", "name": "my_func"}
        # Unknown mode should default to auto
        assert ToolRegistry.get_tool_choice("INVALID") == {"type": "auto"}


class TestScopedRegistry:
    """Test create_scoped_registry for subagent tool access."""

    def test_scoped_registry_filters(self):
        """create_scoped_registry should only contain specified tools."""
        async def f(input: SearchInput, context: object) -> str:
            return ""

        ToolRegistry.register(
            name="tool_a", description="a", category=ToolCategory.MEMORY,
            auth_level=AuthLevel.ALWAYS_ALLOWED, func=f, input_model=SearchInput,
        )
        ToolRegistry.register(
            name="tool_b", description="b", category=ToolCategory.TASKS,
            auth_level=AuthLevel.ALWAYS_ALLOWED, func=f, input_model=SearchInput,
        )
        ToolRegistry.register(
            name="tool_c", description="c", category=ToolCategory.SELF,
            auth_level=AuthLevel.ALWAYS_ALLOWED, func=f, input_model=SearchInput,
        )

        scoped = ToolRegistry.create_scoped_registry(["tool_a", "tool_c"])
        assert scoped.tool_count() == 2
        assert "tool_a" in scoped.tool_names()
        assert "tool_c" in scoped.tool_names()
        assert "tool_b" not in scoped.tool_names()

    def test_scoped_registry_missing_tool(self):
        """create_scoped_registry should skip missing tools silently."""
        scoped = ToolRegistry.create_scoped_registry(["nonexistent"])
        assert scoped.tool_count() == 0


class TestSchemaCleanup:
    """Test _clean_schema resolves $ref and anyOf."""

    def test_resolves_ref(self):
        """$ref references should be resolved to inline definitions."""
        schema = {
            "type": "object",
            "properties": {
                "item": {"$ref": "#/$defs/Item"}
            },
            "$defs": {
                "Item": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"}
                    }
                }
            }
        }
        cleaned = _clean_schema(schema)
        assert "$ref" not in str(cleaned)
        assert cleaned["properties"]["item"]["type"] == "object"
        assert "name" in cleaned["properties"]["item"]["properties"]

    def test_resolves_anyof_optional(self):
        """anyOf with null type (Pydantic Optional) should flatten to the non-null type."""
        schema = {
            "type": "object",
            "properties": {
                "name": {
                    "anyOf": [
                        {"type": "string"},
                        {"type": "null"},
                    ],
                    "description": "Optional name",
                    "default": None,
                }
            }
        }
        cleaned = _clean_schema(schema)
        prop = cleaned["properties"]["name"]
        assert prop["type"] == "string"
        assert prop["description"] == "Optional name"
        assert "anyOf" not in prop

    def test_removes_defs(self):
        """$defs should be removed from top-level cleaned output."""
        schema = {
            "type": "object",
            "properties": {"x": {"type": "string"}},
            "$defs": {"Foo": {"type": "object"}},
        }
        cleaned = _clean_schema(schema)
        assert "$defs" not in cleaned

    def test_get_schema_tool(self):
        """get_schema_tool should produce a valid tool dict."""
        schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
        result = get_schema_tool("structured_response", "Extract answer", schema)
        assert result["name"] == "structured_response"
        assert result["description"] == "Extract answer"
        assert "input_schema" in result


class TestToolDecoratorValidation:
    """Test that the @tool decorator validates function signatures."""

    def test_non_async_raises(self):
        """Non-async function should raise ValueError."""
        with pytest.raises(ValueError, match="must be async"):
            @tool(
                name="sync_tool",
                description="bad tool",
                category=ToolCategory.SELF,
            )
            def sync_tool(input: SearchInput, context: object) -> str:
                return ""

    def test_too_few_params_raises(self):
        """Function with fewer than 2 params should raise ValueError."""
        with pytest.raises(ValueError, match="must accept"):
            @tool(
                name="one_param",
                description="bad tool",
                category=ToolCategory.SELF,
            )
            async def one_param(input: SearchInput) -> str:
                return ""

    def test_non_basemodel_input_raises(self):
        """First param must be a Pydantic BaseModel subclass."""
        with pytest.raises(ValueError, match="must be a Pydantic BaseModel"):
            @tool(
                name="bad_input",
                description="bad tool",
                category=ToolCategory.SELF,
            )
            async def bad_input(input: str, context: object) -> str:
                return ""
