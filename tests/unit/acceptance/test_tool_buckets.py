"""Tool-bucket consistency tests for the acceptance harness.

The harness CLI, the JSON server, and the final-report builder all bucket
tool calls into the same named categories. When any one of them drifts
(renaming ``Autonomous`` to ``Orchestration``, adding a new supervisor
tool, etc.) the other two must follow. These tests encode that
invariant.
"""

from __future__ import annotations

from tests.acceptance._report import TOOL_BUCKETS

# The 7 supervisor orchestration tools listed in
# ``kora_v2/graph/dispatch.py:SUPERVISOR_TOOLS`` (Phase 7.5b). When that
# list grows, this set — and ``TOOL_BUCKETS["orchestration_tools"]`` —
# must both grow with it.
_EXPECTED_ORCHESTRATION_TOOLS = frozenset(
    {
        "decompose_and_dispatch",
        "get_running_tasks",
        "get_task_progress",
        "get_working_doc",
        "cancel_task",
        "modify_task",
        "record_decision",
    }
)


def test_no_start_autonomous_in_buckets() -> None:
    """``start_autonomous`` was retired in Phase 7.5 — no bucket may
    still reference it."""
    for bucket_name, tools in TOOL_BUCKETS.items():
        assert "start_autonomous" not in tools, (
            f"bucket {bucket_name!r} still contains retired 'start_autonomous'"
        )


def test_orchestration_bucket_complete() -> None:
    assert "orchestration_tools" in TOOL_BUCKETS
    assert TOOL_BUCKETS["orchestration_tools"] == _EXPECTED_ORCHESTRATION_TOOLS


def test_no_autonomous_tools_bucket() -> None:
    """The legacy ``auto_tools`` / ``autonomous_tools`` bucket is gone."""
    assert "auto_tools" not in TOOL_BUCKETS
    assert "autonomous_tools" not in TOOL_BUCKETS


def test_expected_top_level_buckets_present() -> None:
    expected = {
        "life_tools",
        "filesystem_tools",
        "mcp_tools",
        "orchestration_tools",
        "memory_tools",
    }
    missing = expected - set(TOOL_BUCKETS)
    assert not missing, f"missing buckets: {missing}"


def test_buckets_consistent() -> None:
    """The harness server imports the same ``TOOL_BUCKETS`` the report
    uses — which is the only durable way to keep the two aligned.

    We prove that by re-importing the module and asserting identity; if
    ``_harness_server.py`` ever shadows ``TOOL_BUCKETS`` with a local
    dict this test fails immediately.
    """
    from tests.acceptance import _harness_server, _report

    # _harness_server.cmd_tool_usage_summary pulls the buckets from
    # _report at call time; the cheapest way to check alignment is to
    # assert the two modules agree on the bucket identities.
    assert _report.TOOL_BUCKETS is TOOL_BUCKETS
    # Guard against a sneaky re-definition at module scope.
    assert not hasattr(_harness_server, "TOOL_BUCKETS"), (
        "_harness_server must not shadow TOOL_BUCKETS locally"
    )


def test_orchestration_category_label_not_autonomous() -> None:
    """The CLI ``cmd_tool_usage_summary`` renames the old ``Autonomous``
    category to ``Orchestration`` and adds a ``Pipelines`` placeholder.
    Re-read the source so the label never silently regresses."""
    import inspect

    from tests.acceptance import automated

    src = inspect.getsource(automated.cmd_tool_usage_summary)
    assert "Orchestration" in src
    assert "Pipelines" in src
    assert '("Autonomous"' not in src
