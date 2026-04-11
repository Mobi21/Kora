"""Phase 9 doctor shape regression tests.

Verifies:
- doctor() result has keys: topic, summary, healthy, checks, runtime
- every check has keys: name, passed, detail
- >= 20 checks present
- no duplicate check names
- doctor_report_lines returns a list of strings containing the summary header
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_PROJECT_ROOT = Path(__file__).parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Container fixture helper (mirrors test_inspector_doctor.py)
# ---------------------------------------------------------------------------


def _make_settings(
    *,
    vault_enabled: bool = False,
    vault_path: str = "",
    browser_binary_path: str = "",
    mcp_servers: dict | None = None,
) -> MagicMock:
    settings = MagicMock()
    settings.data_dir = Path("data")
    settings.security.api_token_path = "data/.api_token"
    settings.security.cors_origins = ["http://localhost:*"]
    settings.daemon.host = "127.0.0.1"
    settings.vault.enabled = vault_enabled
    settings.vault.path = vault_path
    settings.browser.binary_path = browser_binary_path

    from kora_v2.core.settings import MCPSettings
    settings.mcp = MCPSettings(servers=mcp_servers or {})
    return settings


def _make_container(settings: MagicMock | None = None) -> MagicMock:
    container = MagicMock()
    container.settings = settings if settings is not None else _make_settings()
    container._planner = None
    container._executor = None
    container._reviewer = None
    container._checkpointer = None
    container._mcp_manager = None
    container.skill_loader = None
    return container


# ---------------------------------------------------------------------------
# 1. Top-level shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_doctor_returns_dict_with_topic() -> None:
    from kora_v2.runtime.inspector import RuntimeInspector

    container = _make_container()
    report = await RuntimeInspector(container).doctor()
    assert isinstance(report, dict)
    assert report.get("topic") == "doctor"


@pytest.mark.asyncio
async def test_doctor_has_all_required_keys() -> None:
    from kora_v2.runtime.inspector import RuntimeInspector

    container = _make_container()
    report = await RuntimeInspector(container).doctor()
    for key in ("topic", "summary", "healthy", "checks", "runtime"):
        assert key in report, f"doctor report missing key: {key!r}"


@pytest.mark.asyncio
async def test_doctor_healthy_matches_check_results() -> None:
    from kora_v2.runtime.inspector import RuntimeInspector

    container = _make_container()
    report = await RuntimeInspector(container).doctor()
    checks = report["checks"]
    all_passed = all(c["passed"] for c in checks)
    assert report["healthy"] == all_passed


# ---------------------------------------------------------------------------
# 2. Each check has required keys
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_every_check_has_name_passed_detail() -> None:
    from kora_v2.runtime.inspector import RuntimeInspector

    container = _make_container()
    report = await RuntimeInspector(container).doctor()

    for i, check in enumerate(report["checks"]):
        assert "name" in check, f"check[{i}] missing 'name'"
        assert "passed" in check, f"check[{i}] missing 'passed'"
        assert "detail" in check, f"check[{i}] missing 'detail'"
        assert isinstance(check["name"], str), f"check[{i}].name must be str"
        assert isinstance(check["passed"], bool), f"check[{i}].passed must be bool"
        assert isinstance(check["detail"], str), f"check[{i}].detail must be str"


# ---------------------------------------------------------------------------
# 3. At least 20 checks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_doctor_has_at_least_20_checks() -> None:
    from kora_v2.runtime.inspector import RuntimeInspector

    container = _make_container()
    report = await RuntimeInspector(container).doctor()
    count = len(report["checks"])
    names = [c["name"] for c in report["checks"]]
    assert count >= 20, (
        f"Expected >= 20 checks, got {count}. Checks: {names}"
    )


# ---------------------------------------------------------------------------
# 4. No duplicate check names
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_duplicate_check_names() -> None:
    from kora_v2.runtime.inspector import RuntimeInspector

    container = _make_container()
    report = await RuntimeInspector(container).doctor()
    names = [c["name"] for c in report["checks"]]
    seen: set[str] = set()
    duplicates = []
    for name in names:
        if name in seen:
            duplicates.append(name)
        seen.add(name)
    assert not duplicates, f"Duplicate check names found: {duplicates}"


# ---------------------------------------------------------------------------
# 5. Known check names are present
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_doctor_has_python_version_check() -> None:
    from kora_v2.runtime.inspector import RuntimeInspector

    container = _make_container()
    report = await RuntimeInspector(container).doctor()
    names = {c["name"] for c in report["checks"]}
    assert "python_version_ok" in names


@pytest.mark.asyncio
async def test_doctor_has_capability_registry_check() -> None:
    from kora_v2.runtime.inspector import RuntimeInspector

    container = _make_container()
    report = await RuntimeInspector(container).doctor()
    names = {c["name"] for c in report["checks"]}
    assert "capability_registry_ok" in names


@pytest.mark.asyncio
async def test_doctor_has_agent_browser_check() -> None:
    from kora_v2.runtime.inspector import RuntimeInspector

    container = _make_container()
    report = await RuntimeInspector(container).doctor()
    names = {c["name"] for c in report["checks"]}
    assert "agent_browser_present" in names


@pytest.mark.asyncio
async def test_doctor_has_pysqlite3_swap_check() -> None:
    from kora_v2.runtime.inspector import RuntimeInspector

    container = _make_container()
    report = await RuntimeInspector(container).doctor()
    names = {c["name"] for c in report["checks"]}
    assert "pysqlite3_swap" in names


@pytest.mark.asyncio
async def test_doctor_has_sentence_transformers_check() -> None:
    from kora_v2.runtime.inspector import RuntimeInspector

    container = _make_container()
    report = await RuntimeInspector(container).doctor()
    names = {c["name"] for c in report["checks"]}
    assert "sentence_transformers_importable" in names


@pytest.mark.asyncio
async def test_doctor_has_sqlite_vec_check() -> None:
    from kora_v2.runtime.inspector import RuntimeInspector

    container = _make_container()
    report = await RuntimeInspector(container).doctor()
    names = {c["name"] for c in report["checks"]}
    assert "sqlite_vec_loadable" in names


# ---------------------------------------------------------------------------
# 6. doctor_report_lines returns list of strings with summary
# ---------------------------------------------------------------------------


def test_doctor_report_lines_returns_list() -> None:
    from kora_v2.runtime.inspector import doctor_report_lines

    report = {
        "topic": "doctor",
        "summary": "5/5 checks passed",
        "healthy": True,
        "checks": [
            {"name": f"check_{i}", "passed": True, "detail": "ok"}
            for i in range(5)
        ],
    }
    lines = doctor_report_lines(report)
    assert isinstance(lines, list)
    assert len(lines) > 0


def test_doctor_report_lines_all_strings() -> None:
    from kora_v2.runtime.inspector import doctor_report_lines

    report = {
        "summary": "3/3 checks passed",
        "healthy": True,
        "checks": [
            {"name": "a", "passed": True, "detail": "detail-a"},
            {"name": "b", "passed": True, "detail": ""},
            {"name": "c", "passed": False, "detail": "broke"},
        ],
    }
    lines = doctor_report_lines(report)
    assert all(isinstance(line, str) for line in lines)


def test_doctor_report_lines_contains_summary_header() -> None:
    from kora_v2.runtime.inspector import doctor_report_lines

    report = {
        "summary": "7/10 checks passed",
        "healthy": False,
        "checks": [
            {"name": "ok", "passed": True, "detail": ""},
            {"name": "fail", "passed": False, "detail": "broken"},
        ],
    }
    lines = doctor_report_lines(report)
    full = "\n".join(lines)
    # The summary header must appear somewhere in the output
    assert "7/10 checks passed" in full or "7/10" in full, (
        f"Summary not found in output: {lines[0]!r}"
    )
