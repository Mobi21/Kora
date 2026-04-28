"""Sub-task tool-scope validation — Phase 8f spec §4a.

Centralised checks the dispatcher and the ``decompose_and_dispatch``
tool both run against any in-turn / background sub-task before it is
allowed onto the engine. Implements the three constraints Phase 8
stage handlers (and the supervisor's ``decompose_and_dispatch`` tool)
must respect when dispatching sub-tasks:

    1. **No ASK_FIRST tools** — sub-task tool scope cannot include any
       tool whose registered ``auth_level`` is ``ASK_FIRST`` or
       ``NEVER``. The whole point of a sub-task is autonomous
       background execution; an ASK_FIRST tool cannot be authorised
       inside a worker context because there is no user to prompt.
    2. **No recursion** — ``decompose_and_dispatch`` is never in a
       sub-task's tool scope. Sub-agents cannot spawn sub-agents.
    3. **Acyclic dependency graphs** — when a stage dispatches multiple
       sub-tasks with ``depends_on`` edges, the dependency graph must
       be acyclic. Pipeline-level cycle detection (in ``pipeline.py``)
       covers stages declared at registration time; this module adds
       the same guarantee for runtime-built sub-task graphs.

The functions return a :class:`ScopeValidationError` on rejection with
``reason`` set to one of the strings in :data:`REJECTION_REASONS` so
callers can return structured error payloads (e.g. the supervisor tool
returns ``REQUIRES_USER_APPROVAL`` to the LLM instead of raising).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from kora_v2.tools.types import AuthLevel

# Reason strings used by the supervisor tool layer to return structured
# error payloads. Keep the names stable — the LLM's instruction handling
# in `dispatch.py` keys off these literals.
REJECTION_REASON_REQUIRES_USER_APPROVAL = "REQUIRES_USER_APPROVAL"
REJECTION_REASON_NO_RECURSION = "NO_RECURSION"
REJECTION_REASON_CYCLE = "CYCLE_DETECTED"
REJECTION_REASON_UNKNOWN_DEPENDENCY = "UNKNOWN_DEPENDENCY"

REJECTION_REASONS: frozenset[str] = frozenset(
    {
        REJECTION_REASON_REQUIRES_USER_APPROVAL,
        REJECTION_REASON_NO_RECURSION,
        REJECTION_REASON_CYCLE,
        REJECTION_REASON_UNKNOWN_DEPENDENCY,
    }
)

# Tools that must never appear in a sub-task tool scope, regardless of
# whether they are present in the live ToolRegistry. Spec §4a explicitly
# names ``decompose_and_dispatch``; we add the other orchestration
# control tools because none of them belong inside a worker that should
# only execute its own scope.
_FORBIDDEN_SUBAGENT_TOOLS: frozenset[str] = frozenset(
    {
        "decompose_and_dispatch",
        # The orchestration control surface is the supervisor's, not a
        # sub-agent's — explicitly forbid these so a stage handler that
        # forwards its own tool list cannot accidentally hand them off.
        "cancel_task",
        "modify_task",
    }
)

# Write tools that executor sub-tasks are permitted to use even though they
# are ASK_FIRST in the interactive ToolRegistry. The authorization is implicit:
# the user approved the dispatch (via decompose_and_dispatch), and the executor
# pipeline stage is the intended site for file writes. Without these, any
# autonomous task that needs to persist its output is blocked.
_ORCHESTRATION_EXECUTOR_TOOLS: frozenset[str] = frozenset(
    {
        "write_file",
        "create_directory",
    }
)

# Capability-pack interaction tools that mutate external state (clicks,
# typing, form fills, etc.). These are NOT registered in the in-process
# ToolRegistry — they live on the capability-pack ActionRegistry — so the
# normal ``auth_lookup`` returns ``None`` for them and they would otherwise
# fall through as ALWAYS_ALLOWED. We treat any name in this set as
# implicitly ASK_FIRST so they cannot be silently delegated to a sub-task.
#
# Spec §4a constraint 3 ("Read-only capability actions only") — sub-tasks
# may use browser read/navigation actions but not interaction actions.
KNOWN_INTERACTION_TOOL_PATTERNS: frozenset[str] = frozenset(
    {
        # Browser pack — write actions (see kora_v2.capabilities.browser).
        # `browser.open` / `browser.snapshot` / `browser.screenshot` /
        # `browser.clip_page` / `browser.clip_selection` / `browser.close`
        # are read-only navigation and remain ALWAYS_ALLOWED.
        "browser.click",
        "browser.type",
        "browser.fill",
    }
)


@dataclass(frozen=True)
class SubTaskSpec:
    """Minimal description of a sub-task for validation.

    Mirrors the spec §4b ``SubTaskSpec`` shape but carries only the
    fields the validator actually needs. Real dispatch code can pass
    its own dataclass — this module only reads the four fields below.
    """

    task_id: str
    description: str
    required_tools: list[str]
    depends_on: list[str]


@dataclass(frozen=True)
class ScopeValidationError(Exception):
    """Raised by the validators when a sub-task spec violates a rule."""

    reason: str
    message: str
    offending_field: str | None = None

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.reason}: {self.message}"


def validate_tool_scope(
    tool_scope: Iterable[str],
    *,
    auth_lookup: Callable[..., AuthLevel | None] | None = None,
) -> None:
    """Reject sub-task ``tool_scope`` containing ASK_FIRST/NEVER tools.

    ``auth_lookup`` defaults to :class:`ToolRegistry.get_definition`
    when not supplied so the function works without ceremony in
    production. Tests may pass a stub mapping name -> :class:`AuthLevel`.

    A tool name that is unknown to the registry is treated as
    permissive (``ALWAYS_ALLOWED``) — this matches the dispatch tool's
    behaviour today, which only enforces auth on tools that explicitly
    register an ``auth_level`` in :class:`ToolDefinition`. The exception
    is :data:`KNOWN_INTERACTION_TOOL_PATTERNS`, which are rejected even
    when the registry has no entry for them (capability-pack tools are
    not in the ToolRegistry but must still be gated).
    """
    scope_list = [str(t).strip() for t in tool_scope if str(t).strip()]
    if not scope_list:
        return

    if auth_lookup is None:
        from kora_v2.tools.registry import ToolRegistry

        def _default_lookup(name: str) -> AuthLevel | None:
            definition = ToolRegistry.get_definition(name)
            if definition is None:
                return None
            return definition.auth_level

        auth_lookup = _default_lookup

    # Forbidden control-surface tools are rejected first — they would
    # also fail the recursion check, but the more specific rejection
    # message helps the LLM understand why.
    for name in scope_list:
        if name in _FORBIDDEN_SUBAGENT_TOOLS:
            raise ScopeValidationError(
                reason=REJECTION_REASON_NO_RECURSION,
                message=(
                    f"Tool {name!r} cannot be in a sub-task scope; "
                    "sub-agents cannot spawn sub-agents."
                ),
                offending_field=name,
            )

    # Known capability-pack interaction tools are gated even though they
    # are not in the ToolRegistry. Capability-pack tools that fall through
    # to ``ALWAYS_ALLOWED`` would let a sub-agent silently click / type /
    # fill on the user's behalf — explicitly reject them here.
    for name in scope_list:
        if name in KNOWN_INTERACTION_TOOL_PATTERNS:
            raise ScopeValidationError(
                reason=REJECTION_REASON_REQUIRES_USER_APPROVAL,
                message=(
                    f"Tool {name!r} is an interaction action that requires "
                    "user approval; sub-tasks may only call read-only "
                    "capability actions."
                ),
                offending_field=name,
            )

    # ASK_FIRST / NEVER tools require user permission to execute,
    # which is not available inside a background worker. The supervisor
    # must call them itself. Exception: executor write tools are permitted
    # since the user implicitly approved writes when they approved the dispatch.
    for name in scope_list:
        if name in _ORCHESTRATION_EXECUTOR_TOOLS:
            continue
        level = auth_lookup(name)
        if level in (AuthLevel.ASK_FIRST, AuthLevel.NEVER):
            raise ScopeValidationError(
                reason=REJECTION_REASON_REQUIRES_USER_APPROVAL,
                message=(
                    f"Tool {name!r} has auth_level={level.value}; "
                    "sub-tasks may only call ALWAYS_ALLOWED tools."
                ),
                offending_field=name,
            )


def validate_dependency_graph(specs: Iterable[SubTaskSpec]) -> None:
    """Reject cyclic / unknown-dependency sub-task graphs.

    Mirrors the Tarjan-style traversal in
    :func:`kora_v2.runtime.orchestration.pipeline._assert_acyclic`,
    but operates on the runtime sub-task list passed to
    ``decompose_and_dispatch``. Empty / single-node graphs are trivially
    acyclic and return immediately.
    """
    nodes: list[SubTaskSpec] = list(specs)
    if not nodes:
        return

    name_to_deps: dict[str, list[str]] = {n.task_id: list(n.depends_on) for n in nodes}
    name_set = set(name_to_deps)

    for node in nodes:
        for dep in node.depends_on:
            if dep not in name_set:
                raise ScopeValidationError(
                    reason=REJECTION_REASON_UNKNOWN_DEPENDENCY,
                    message=(
                        f"Sub-task {node.task_id!r} depends on unknown "
                        f"task_id {dep!r}."
                    ),
                    offending_field=dep,
                )

    WHITE, GREY, BLACK = 0, 1, 2
    color: dict[str, int] = dict.fromkeys(name_to_deps, WHITE)

    def visit(node: str, stack: list[str]) -> None:
        color[node] = GREY
        stack.append(node)
        for neighbour in name_to_deps.get(node, ()):  # type: ignore[arg-type]
            if color[neighbour] == GREY:
                cycle = stack[stack.index(neighbour) :] + [neighbour]
                raise ScopeValidationError(
                    reason=REJECTION_REASON_CYCLE,
                    message=(
                        "Cyclic sub-task dependency: "
                        + " -> ".join(cycle)
                    ),
                    offending_field=neighbour,
                )
            if color[neighbour] == WHITE:
                visit(neighbour, stack)
        stack.pop()
        color[node] = BLACK

    for node_name in name_to_deps:
        if color[node_name] == WHITE:
            visit(node_name, [])


def validate_subtask_specs(
    specs: Iterable[SubTaskSpec],
    *,
    auth_lookup: Callable[..., AuthLevel | None] | None = None,
) -> None:
    """Run every sub-task constraint in one pass.

    The convenience entry point used by ``decompose_and_dispatch`` and
    by Phase 8 stage handlers that dispatch sub-tasks. Raises the first
    :class:`ScopeValidationError` it encounters — callers translate
    that into a structured error payload for the LLM.
    """
    spec_list: list[SubTaskSpec] = list(specs)
    for spec in spec_list:
        validate_tool_scope(spec.required_tools, auth_lookup=auth_lookup)
    validate_dependency_graph(spec_list)


__all__ = [
    "KNOWN_INTERACTION_TOOL_PATTERNS",
    "REJECTION_REASONS",
    "REJECTION_REASON_REQUIRES_USER_APPROVAL",
    "REJECTION_REASON_NO_RECURSION",
    "REJECTION_REASON_CYCLE",
    "REJECTION_REASON_UNKNOWN_DEPENDENCY",
    "ScopeValidationError",
    "SubTaskSpec",
    "validate_tool_scope",
    "validate_dependency_graph",
    "validate_subtask_specs",
]
