"""Shared fixtures for the orchestration integration test suite.

Mirrors the unit-test conftest for the one fixture both trees need:
the autonomous runtime context is a process-level global, and any
test that installs one (via ``set_autonomous_context`` or via
``OrchestrationEngine.start``) must clear it so later tests do not
inherit a stale container / DB path.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from kora_v2.autonomous.runtime_context import clear_autonomous_context


@pytest.fixture(autouse=True)
def _reset_autonomous_context() -> Iterator[None]:
    """Clear the module-global autonomous runtime context between tests.

    The autonomous step function reads its container/db_path from a
    process-level global (see
    :mod:`kora_v2.autonomous.runtime_context`) that is populated by
    :meth:`OrchestrationEngine.start`. Tests that never start the
    engine — and tests that tear their engine down without calling
    ``stop()`` — leave the context set to stale state from a previous
    test's ``tmp_path``. This autouse fixture clears it before and
    after each test so no test is affected by another's residue.
    """
    clear_autonomous_context()
    try:
        yield
    finally:
        clear_autonomous_context()
