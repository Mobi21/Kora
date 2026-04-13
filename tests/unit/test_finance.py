"""Unit tests for Phase 5 finance tools."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kora_v2.core.db import init_operational_db
from kora_v2.tools.life_management import (
    IMPULSE_MIN_SAMPLES,
    LogExpenseInput,
    QueryExpensesInput,
    log_expense,
    query_expenses,
)


class _StubContainer:
    def __init__(self, data_dir: Path):
        class _Settings:
            pass

        self.settings = _Settings()
        self.settings.data_dir = data_dir


@pytest.fixture
async def container(tmp_path):
    await init_operational_db(tmp_path / "operational.db")
    return _StubContainer(tmp_path)


async def test_log_expense_stores_row(container):
    r = await log_expense(
        LogExpenseInput(amount=12.5, category="food", description="lunch"),
        container,
    )
    data = json.loads(r)
    assert data["success"] is True
    assert data["amount"] == 12.5


async def test_log_expense_requires_positive(container):
    r = await log_expense(
        LogExpenseInput(amount=0, category="food"), container
    )
    assert json.loads(r)["success"] is False


async def test_is_impulse_requires_min_samples(container):
    # First IMPULSE_MIN_SAMPLES entries should never be flagged even if
    # one is huge compared to the rest, because there's no history yet.
    for i in range(IMPULSE_MIN_SAMPLES):
        r = await log_expense(
            LogExpenseInput(amount=10 + i, category="food"), container
        )
        assert json.loads(r)["is_impulse"] is False


async def test_is_impulse_fires_after_history(container):
    # Seed IMPULSE_MIN_SAMPLES small entries → then one big one.
    for amt in [10, 12, 15, 11, 13, 9]:
        await log_expense(
            LogExpenseInput(amount=amt, category="tech"), container
        )
    r = await log_expense(
        LogExpenseInput(amount=100, category="tech"), container
    )
    data = json.loads(r)
    assert data["is_impulse"] is True
    assert data["note"] is not None
    assert "higher than usual" in data["note"]


async def test_query_groups_by_category(container):
    await log_expense(
        LogExpenseInput(amount=10, category="food"), container
    )
    await log_expense(
        LogExpenseInput(amount=20, category="tech"), container
    )
    r = await query_expenses(QueryExpensesInput(days_back=7), container)
    data = json.loads(r)
    assert data["total"] == 30.0
    assert data["by_category"]["food"] == 10.0
    assert data["by_category"]["tech"] == 20.0
