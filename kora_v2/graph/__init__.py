"""Kora V2 -- LangGraph supervisor graph layer.

Public API:
  - ``SupervisorState`` -- graph state TypedDict
  - ``build_supervisor_graph`` -- compile the 5-node supervisor graph
  - ``build_frozen_prefix`` / ``build_dynamic_suffix`` -- prompt builders
"""

from kora_v2.graph.state import SupervisorState
from kora_v2.graph.supervisor import build_supervisor_graph

__all__ = [
    "SupervisorState",
    "build_supervisor_graph",
]
