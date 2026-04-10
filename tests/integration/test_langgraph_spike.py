"""Spike test: LangGraph supervisor dispatches to worker subgraph via tool.

This validates the core V2 pattern: supervisor graph calls dispatch_worker tool
which invokes a worker subgraph and returns results.
"""
import pytest
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode
from typing import TypedDict, Annotated
from langgraph.graph.message import add_messages
import json

# Define a minimal worker subgraph
# The worker just processes input and returns a result

class WorkerState(TypedDict):
    input_text: str
    result: str

def worker_process(state: WorkerState) -> dict:
    """Simulate a worker processing step."""
    return {"result": f"Processed: {state['input_text']}"}

def build_worker_graph():
    """Build a minimal worker subgraph."""
    builder = StateGraph(WorkerState)
    builder.add_node("process", worker_process)
    builder.set_entry_point("process")
    builder.add_edge("process", END)
    return builder.compile()

# Define dispatch_worker as a tool function
WORKER_GRAPH = build_worker_graph()

def dispatch_worker(worker_name: str, input_json: str) -> str:
    """Dispatch to a worker subgraph and return its result."""
    input_data = json.loads(input_json)
    result = WORKER_GRAPH.invoke(input_data)
    return json.dumps({"worker": worker_name, "result": result["result"]})

# Define supervisor state
class SupervisorState(TypedDict):
    messages: Annotated[list, add_messages]

# Tests
class TestLangGraphDispatchSpike:
    def test_worker_subgraph_standalone(self):
        """Worker subgraph processes input independently."""
        result = WORKER_GRAPH.invoke({"input_text": "hello", "result": ""})
        assert result["result"] == "Processed: hello"

    def test_dispatch_tool_invocation(self):
        """dispatch_worker tool calls subgraph and returns JSON result."""
        result_json = dispatch_worker("test_worker", '{"input_text": "test data", "result": ""}')
        result = json.loads(result_json)
        assert result["worker"] == "test_worker"
        assert result["result"] == "Processed: test data"

    def test_supervisor_graph_with_tool_node(self):
        """Full supervisor graph with ToolNode calling dispatch_worker.

        This test validates the actual dispatch pattern by building a real
        graph: supervisor node emits an AIMessage with tool_calls ->
        ToolNode executes dispatch_worker -> routes back to supervisor ->
        supervisor sees result and ends.
        """
        from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
        from langchain_core.tools import tool as lc_tool

        @lc_tool
        def dispatch_worker_tool(worker_name: str, input_json: str) -> str:
            """Dispatch to a worker subgraph."""
            return dispatch_worker(worker_name, input_json)

        tools = [dispatch_worker_tool]
        tool_node = ToolNode(tools)

        # Track how many times supervisor is called
        call_count = {"n": 0}

        def supervisor_node(state: SupervisorState) -> dict:
            """Supervisor: first call emits tool_call, second call ends."""
            call_count["n"] += 1
            if call_count["n"] == 1:
                # First invocation: decide to dispatch to worker
                ai_msg = AIMessage(
                    content="",
                    tool_calls=[{
                        "id": "call_1",
                        "name": "dispatch_worker_tool",
                        "args": {
                            "worker_name": "planner",
                            "input_json": '{"input_text": "decompose task", "result": ""}'
                        }
                    }]
                )
                return {"messages": [ai_msg]}
            else:
                # Second invocation: got tool result, produce final answer
                return {"messages": [AIMessage(content="Done dispatching.")]}

        def should_continue(state: SupervisorState) -> str:
            """Route to tools if last message has tool_calls, else end."""
            last = state["messages"][-1]
            if hasattr(last, "tool_calls") and last.tool_calls:
                return "tools"
            return END

        # Build the supervisor graph
        builder = StateGraph(SupervisorState)
        builder.add_node("supervisor", supervisor_node)
        builder.add_node("tools", tool_node)
        builder.set_entry_point("supervisor")
        builder.add_conditional_edges("supervisor", should_continue, {"tools": "tools", END: END})
        builder.add_edge("tools", "supervisor")
        graph = builder.compile()

        # Run the full graph
        result = graph.invoke({"messages": [HumanMessage(content="plan this")]})

        # Verify: supervisor was called twice (dispatch + finalize)
        assert call_count["n"] == 2

        # Verify tool result is in the message history
        tool_msgs = [m for m in result["messages"] if isinstance(m, ToolMessage)]
        assert len(tool_msgs) == 1
        payload = json.loads(tool_msgs[0].content)
        assert payload["worker"] == "planner"
        assert "Processed: decompose task" in payload["result"]

        # Verify final AI message
        final_msg = result["messages"][-1]
        assert isinstance(final_msg, AIMessage)
        assert final_msg.content == "Done dispatching."

    def test_streaming_events_from_worker(self):
        """Verify that worker subgraph can produce streaming events."""
        events = list(WORKER_GRAPH.stream({"input_text": "stream test", "result": ""}))
        assert len(events) > 0
        # Last event should have the result
        final = events[-1]
        assert "process" in final
        assert final["process"]["result"] == "Processed: stream test"
