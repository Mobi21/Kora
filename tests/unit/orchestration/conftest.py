"""Shared fixtures for the orchestration unit test suite."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, time
from pathlib import Path

import pytest

from kora_v2.autonomous.runtime_context import clear_autonomous_context
from kora_v2.runtime.orchestration import (
    OrchestrationEngine,
    SystemStatePhase,
    UserScheduleProfile,
    init_orchestration_schema,
)


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


@pytest.fixture
def frozen_now() -> datetime:
    return datetime(2026, 4, 14, 15, 0, 0, tzinfo=UTC)


@pytest.fixture
async def orchestration_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "operational.db"
    await init_orchestration_schema(db_path)
    return db_path


@pytest.fixture
async def engine(orchestration_db: Path) -> OrchestrationEngine:
    profile = UserScheduleProfile(
        timezone="UTC",
        wake_time=time(8, 0),
        sleep_start=time(23, 0),
        sleep_end=time(7, 0),
    )
    eng = OrchestrationEngine(
        orchestration_db,
        schedule_profile=profile,
        tick_interval=0.01,
    )
    # Tests drive the dispatcher manually via tick_once; skip the
    # background loop entirely.
    await init_orchestration_schema(orchestration_db)
    await eng.limiter.replay_from_log()
    eng.state_machine.note_session_end(datetime.now(UTC))
    yield eng
    # No running loop to stop when dispatcher.start() was not called.


_PHASE_OK = SystemStatePhase  # re-export for tests that import from conftest
