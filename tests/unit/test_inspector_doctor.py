"""Unit tests for the extended RuntimeInspector.doctor() method (Task 5, Phase 9).

Tests verify:
- Top-level return shape
- At least 15 checks present (13 original + new ones)
- New check names are present
- healthy bool matches passed == total
- Capability pack UNHEALTHY aggregation
- agent_browser_present False when binary_path is non-existent
- vault checks "disabled" state without error
- doctor_report_lines returns non-empty list and handles edge cases
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_PROJECT_ROOT = Path(__file__).parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Container fixture helpers
# ---------------------------------------------------------------------------


def _make_settings(
    *,
    vault_enabled: bool = True,
    vault_path: str = "",
    browser_binary_path: str = "",
    mcp_servers: dict | None = None,
) -> MagicMock:
    """Build a mock Settings object."""
    settings = MagicMock()
    settings.data_dir = Path("data")

    # Security
    settings.security.api_token_path = "data/.api_token"
    settings.security.cors_origins = ["http://localhost:*"]

    # Daemon
    settings.daemon.host = "127.0.0.1"

    # Vault
    settings.vault.enabled = vault_enabled
    settings.vault.path = vault_path

    # Browser
    settings.browser.binary_path = browser_binary_path

    # MCP
    from kora_v2.core.settings import MCPSettings
    if mcp_servers is None:
        settings.mcp = MCPSettings(servers={})
    else:
        settings.mcp = MCPSettings(servers=mcp_servers)

    return settings


def _make_container(settings: MagicMock | None = None) -> MagicMock:
    """Build a minimal mock Container suitable for RuntimeInspector."""
    container = MagicMock()
    container.settings = settings if settings is not None else _make_settings()

    # Workers not initialized by default
    container._planner = None
    container._executor = None
    container._reviewer = None
    container._checkpointer = None
    container._mcp_manager = None
    container.skill_loader = None

    return container


# ---------------------------------------------------------------------------
# 1. Basic shape and minimum check count
# ---------------------------------------------------------------------------


class TestDoctorShape:
    """Top-level shape and mandatory fields."""

    @pytest.mark.asyncio
    async def test_returns_dict_with_expected_keys(self) -> None:
        from kora_v2.runtime.inspector import RuntimeInspector

        container = _make_container()
        inspector = RuntimeInspector(container)
        report = await inspector.doctor()

        assert isinstance(report, dict)
        assert report["topic"] == "doctor"
        assert "summary" in report
        assert "healthy" in report
        assert "checks" in report
        assert "runtime" in report

    @pytest.mark.asyncio
    async def test_healthy_matches_all_checks_passed(self) -> None:
        from kora_v2.runtime.inspector import RuntimeInspector

        container = _make_container()
        inspector = RuntimeInspector(container)
        report = await inspector.doctor()

        checks = report["checks"]
        passed_count = sum(1 for c in checks if c["passed"])
        total = len(checks)
        expected_healthy = passed_count == total
        assert report["healthy"] == expected_healthy

    @pytest.mark.asyncio
    async def test_at_least_15_checks(self) -> None:
        from kora_v2.runtime.inspector import RuntimeInspector

        container = _make_container()
        inspector = RuntimeInspector(container)
        report = await inspector.doctor()

        assert len(report["checks"]) >= 15, (
            f"Expected >= 15 checks, got {len(report['checks'])}: "
            f"{[c['name'] for c in report['checks']]}"
        )

    @pytest.mark.asyncio
    async def test_summary_format(self) -> None:
        from kora_v2.runtime.inspector import RuntimeInspector

        container = _make_container()
        inspector = RuntimeInspector(container)
        report = await inspector.doctor()

        assert "/" in report["summary"]
        assert "checks passed" in report["summary"]


# ---------------------------------------------------------------------------
# 2. New check names are present
# ---------------------------------------------------------------------------


class TestNewCheckNames:
    """Each new check group contributes at least one recognisable check name."""

    @pytest.mark.asyncio
    async def test_python_version_ok_present(self) -> None:
        from kora_v2.runtime.inspector import RuntimeInspector

        container = _make_container()
        inspector = RuntimeInspector(container)
        report = await inspector.doctor()

        names = {c["name"] for c in report["checks"]}
        assert "python_version_ok" in names

    @pytest.mark.asyncio
    async def test_pysqlite3_swap_present(self) -> None:
        from kora_v2.runtime.inspector import RuntimeInspector

        container = _make_container()
        inspector = RuntimeInspector(container)
        report = await inspector.doctor()

        names = {c["name"] for c in report["checks"]}
        assert "pysqlite3_swap" in names

    @pytest.mark.asyncio
    async def test_sentence_transformers_present(self) -> None:
        from kora_v2.runtime.inspector import RuntimeInspector

        container = _make_container()
        inspector = RuntimeInspector(container)
        report = await inspector.doctor()

        names = {c["name"] for c in report["checks"]}
        assert "sentence_transformers_importable" in names

    @pytest.mark.asyncio
    async def test_sqlite_vec_loadable_present(self) -> None:
        from kora_v2.runtime.inspector import RuntimeInspector

        container = _make_container()
        inspector = RuntimeInspector(container)
        report = await inspector.doctor()

        names = {c["name"] for c in report["checks"]}
        assert "sqlite_vec_loadable" in names

    @pytest.mark.asyncio
    async def test_capability_registry_ok_present(self) -> None:
        from kora_v2.runtime.inspector import RuntimeInspector

        container = _make_container()
        inspector = RuntimeInspector(container)
        report = await inspector.doctor()

        names = {c["name"] for c in report["checks"]}
        assert "capability_registry_ok" in names

    @pytest.mark.asyncio
    async def test_agent_browser_present_check_exists(self) -> None:
        from kora_v2.runtime.inspector import RuntimeInspector

        container = _make_container()
        inspector = RuntimeInspector(container)
        report = await inspector.doctor()

        names = {c["name"] for c in report["checks"]}
        assert "agent_browser_present" in names

    @pytest.mark.asyncio
    async def test_mcp_no_servers_check_present(self) -> None:
        """When no MCP servers configured, mcp_servers_configured check appears."""
        from kora_v2.runtime.inspector import RuntimeInspector

        settings = _make_settings(mcp_servers={})
        container = _make_container(settings)
        inspector = RuntimeInspector(container)
        report = await inspector.doctor()

        names = {c["name"] for c in report["checks"]}
        assert "mcp_servers_configured" in names


# ---------------------------------------------------------------------------
# 3. Capability pack UNHEALTHY aggregation
# ---------------------------------------------------------------------------


class TestCapabilityPackAggregation:
    """Doctor aggregates capability health correctly."""

    @pytest.mark.asyncio
    async def test_unhealthy_pack_marks_check_failed(self) -> None:
        """If a pack returns UNHEALTHY, capability_{name} check passes=False."""
        from kora_v2.capabilities.base import CapabilityHealth, CapabilityPack, HealthStatus
        from kora_v2.capabilities.registry import CapabilityRegistry
        from kora_v2.runtime.inspector import RuntimeInspector

        class UnhealthyPack(CapabilityPack):
            name = "test_unhealthy_pack"
            description = "Test only"

            async def health_check(self) -> CapabilityHealth:
                return CapabilityHealth(
                    status=HealthStatus.UNHEALTHY,
                    summary="intentionally broken",
                )

            def register_actions(self, registry):
                return None

            def get_policy(self):
                from kora_v2.capabilities.policy import PolicyMatrix
                return PolicyMatrix()

        local_registry = CapabilityRegistry()
        local_registry.register(UnhealthyPack())

        with patch(
            "kora_v2.runtime.inspector.RuntimeInspector.doctor.__func__"
            if False else "kora_v2.capabilities.get_all_capabilities",
            return_value=local_registry.get_all(),
        ):
            container = _make_container()
            inspector = RuntimeInspector(container)
            report = await inspector.doctor()

        # Find the capability check for our test pack
        cap_check = next(
            (c for c in report["checks"] if c["name"] == "capability_test_unhealthy_pack"),
            None,
        )
        assert cap_check is not None, "capability_test_unhealthy_pack check not found"
        assert cap_check["passed"] is False
        assert "unhealthy" in cap_check["detail"].lower()

    @pytest.mark.asyncio
    async def test_degraded_pack_marks_check_passed(self) -> None:
        """DEGRADED health still passes (only UNHEALTHY fails)."""
        from kora_v2.capabilities.base import CapabilityHealth, CapabilityPack, HealthStatus
        from kora_v2.capabilities.registry import CapabilityRegistry
        from kora_v2.runtime.inspector import RuntimeInspector

        class DegradedPack(CapabilityPack):
            name = "test_degraded_pack"
            description = "Test only"

            async def health_check(self) -> CapabilityHealth:
                return CapabilityHealth(
                    status=HealthStatus.DEGRADED,
                    summary="partially working",
                )

            def register_actions(self, registry):
                return None

            def get_policy(self):
                from kora_v2.capabilities.policy import PolicyMatrix
                return PolicyMatrix()

        local_registry = CapabilityRegistry()
        local_registry.register(DegradedPack())

        with patch(
            "kora_v2.capabilities.get_all_capabilities",
            return_value=local_registry.get_all(),
        ):
            container = _make_container()
            inspector = RuntimeInspector(container)
            report = await inspector.doctor()

        cap_check = next(
            (c for c in report["checks"] if c["name"] == "capability_test_degraded_pack"),
            None,
        )
        assert cap_check is not None
        assert cap_check["passed"] is True
        assert "degraded" in cap_check["detail"].lower()

    @pytest.mark.asyncio
    async def test_unimplemented_pack_marks_check_passed(self) -> None:
        """UNIMPLEMENTED health still passes."""
        from kora_v2.capabilities.base import CapabilityHealth, CapabilityPack, HealthStatus
        from kora_v2.capabilities.registry import CapabilityRegistry
        from kora_v2.runtime.inspector import RuntimeInspector

        class UnimplementedPack(CapabilityPack):
            name = "test_unimplemented_pack"
            description = "Test only"

            async def health_check(self) -> CapabilityHealth:
                return CapabilityHealth(
                    status=HealthStatus.UNIMPLEMENTED,
                    summary="not yet built",
                )

            def register_actions(self, registry):
                return None

            def get_policy(self):
                from kora_v2.capabilities.policy import PolicyMatrix
                return PolicyMatrix()

        local_registry = CapabilityRegistry()
        local_registry.register(UnimplementedPack())

        with patch(
            "kora_v2.capabilities.get_all_capabilities",
            return_value=local_registry.get_all(),
        ):
            container = _make_container()
            inspector = RuntimeInspector(container)
            report = await inspector.doctor()

        cap_check = next(
            (c for c in report["checks"] if c["name"] == "capability_test_unimplemented_pack"),
            None,
        )
        assert cap_check is not None
        assert cap_check["passed"] is True


# ---------------------------------------------------------------------------
# 4. agent_browser_present with non-existent path
# ---------------------------------------------------------------------------


class TestAgentBrowserCheck:
    """agent_browser_present check behaves correctly for missing binary."""

    @pytest.mark.asyncio
    async def test_nonexistent_binary_path_reports_false(self) -> None:
        """A configured binary_path that doesn't exist yields passed=False."""
        from kora_v2.runtime.inspector import RuntimeInspector

        settings = _make_settings(browser_binary_path="/nonexistent/path/agent-browser")
        container = _make_container(settings)
        inspector = RuntimeInspector(container)
        report = await inspector.doctor()

        check = next(
            (c for c in report["checks"] if c["name"] == "agent_browser_present"),
            None,
        )
        assert check is not None
        assert check["passed"] is False
        assert "not found" in check["detail"].lower() or check["detail"]

    @pytest.mark.asyncio
    async def test_empty_binary_path_falls_back_to_which(self) -> None:
        """Empty binary_path tries shutil.which('agent-browser')."""
        from kora_v2.runtime.inspector import RuntimeInspector

        settings = _make_settings(browser_binary_path="")
        container = _make_container(settings)
        inspector = RuntimeInspector(container)

        # Simulate agent-browser not on PATH
        with patch("kora_v2.runtime.inspector.shutil.which", return_value=None):
            report = await inspector.doctor()

        check = next(
            (c for c in report["checks"] if c["name"] == "agent_browser_present"),
            None,
        )
        assert check is not None
        assert check["passed"] is False
        assert "not found on PATH" in check["detail"]

    @pytest.mark.asyncio
    async def test_which_finds_binary_reports_true(self) -> None:
        """If shutil.which returns a path, check passes."""
        from kora_v2.runtime.inspector import RuntimeInspector

        settings = _make_settings(browser_binary_path="")
        container = _make_container(settings)
        inspector = RuntimeInspector(container)

        with patch(
            "kora_v2.runtime.inspector.shutil.which",
            return_value="/usr/local/bin/agent-browser",
        ):
            report = await inspector.doctor()

        check = next(
            (c for c in report["checks"] if c["name"] == "agent_browser_present"),
            None,
        )
        assert check is not None
        assert check["passed"] is True
        assert "/usr/local/bin/agent-browser" in check["detail"]


# ---------------------------------------------------------------------------
# 5. Vault checks with vault.enabled = False
# ---------------------------------------------------------------------------


class TestVaultChecks:
    """Vault doctor checks handle disabled/enabled states."""

    @pytest.mark.asyncio
    async def test_vault_disabled_reports_consistent_without_error(self) -> None:
        """vault.enabled=False adds vault_enabled=True (consistent state) with detail."""
        from kora_v2.runtime.inspector import RuntimeInspector

        settings = _make_settings(vault_enabled=False, vault_path="")
        container = _make_container(settings)
        inspector = RuntimeInspector(container)

        # Should NOT raise
        report = await inspector.doctor()

        check = next(
            (c for c in report["checks"] if c["name"] == "vault_enabled"),
            None,
        )
        assert check is not None, "vault_enabled check should exist when vault is disabled"
        assert check["passed"] is True
        assert "disabled" in check["detail"]

    @pytest.mark.asyncio
    async def test_vault_enabled_but_no_path(self) -> None:
        """vault.enabled=True but path='' yields vault_path_configured=False."""
        from kora_v2.runtime.inspector import RuntimeInspector

        settings = _make_settings(vault_enabled=True, vault_path="")
        container = _make_container(settings)
        inspector = RuntimeInspector(container)
        report = await inspector.doctor()

        check = next(
            (c for c in report["checks"] if c["name"] == "vault_path_configured"),
            None,
        )
        assert check is not None
        assert check["passed"] is False

    @pytest.mark.asyncio
    async def test_vault_enabled_with_existing_writable_dir(self, tmp_path: Path) -> None:
        """vault.enabled=True and a real writable dir yields vault_writable=True."""
        from kora_v2.runtime.inspector import RuntimeInspector

        settings = _make_settings(vault_enabled=True, vault_path=str(tmp_path))
        container = _make_container(settings)
        inspector = RuntimeInspector(container)
        report = await inspector.doctor()

        check = next(
            (c for c in report["checks"] if c["name"] == "vault_writable"),
            None,
        )
        assert check is not None
        assert check["passed"] is True


# ---------------------------------------------------------------------------
# 6. doctor_report_lines helper
# ---------------------------------------------------------------------------


class TestDoctorReportLines:
    """doctor_report_lines() renders human-readable output correctly."""

    def test_returns_non_empty_list(self) -> None:
        from kora_v2.runtime.inspector import doctor_report_lines

        report = {
            "topic": "doctor",
            "summary": "2/2 checks passed",
            "healthy": True,
            "checks": [
                {"name": "foo", "passed": True, "detail": "ok"},
                {"name": "bar", "passed": True, "detail": ""},
            ],
        }
        lines = doctor_report_lines(report)
        assert isinstance(lines, list)
        assert len(lines) > 0

    def test_first_line_contains_summary(self) -> None:
        from kora_v2.runtime.inspector import doctor_report_lines

        report = {
            "summary": "3/4 checks passed",
            "healthy": False,
            "checks": [
                {"name": "a", "passed": True, "detail": ""},
                {"name": "b", "passed": False, "detail": "reason"},
            ],
        }
        lines = doctor_report_lines(report)
        assert "3/4 checks passed" in lines[0]

    def test_healthy_label_ok(self) -> None:
        from kora_v2.runtime.inspector import doctor_report_lines

        report = {
            "summary": "1/1 checks passed",
            "healthy": True,
            "checks": [{"name": "x", "passed": True, "detail": ""}],
        }
        lines = doctor_report_lines(report)
        assert "[OK]" in lines[0]

    def test_unhealthy_label_degraded(self) -> None:
        from kora_v2.runtime.inspector import doctor_report_lines

        report = {
            "summary": "0/1 checks passed",
            "healthy": False,
            "checks": [{"name": "x", "passed": False, "detail": "broken"}],
        }
        lines = doctor_report_lines(report)
        assert "[DEGRADED]" in lines[0]

    def test_check_lines_use_tick_symbols(self) -> None:
        from kora_v2.runtime.inspector import doctor_report_lines

        report = {
            "summary": "1/2 checks passed",
            "healthy": False,
            "checks": [
                {"name": "good_check", "passed": True, "detail": "all fine"},
                {"name": "bad_check", "passed": False, "detail": "broke"},
            ],
        }
        lines = doctor_report_lines(report)
        # Lines beyond the header should contain tick/cross
        body = "\n".join(lines[1:])
        assert "\u2713" in body  # ✓
        assert "\u2717" in body  # ✗

    def test_detail_included_in_output(self) -> None:
        from kora_v2.runtime.inspector import doctor_report_lines

        report = {
            "summary": "1/1 checks passed",
            "healthy": True,
            "checks": [{"name": "mycheck", "passed": True, "detail": "version=3.12.3"}],
        }
        lines = doctor_report_lines(report)
        full = "\n".join(lines)
        assert "version=3.12.3" in full

    def test_handles_empty_checks_gracefully(self) -> None:
        """Edge case: no checks returns a single non-empty line."""
        from kora_v2.runtime.inspector import doctor_report_lines

        report = {"checks": []}
        lines = doctor_report_lines(report)
        assert isinstance(lines, list)
        assert len(lines) >= 1
        assert all(isinstance(line, str) for line in lines)

    def test_handles_missing_checks_key(self) -> None:
        """Edge case: missing 'checks' key is treated as no checks."""
        from kora_v2.runtime.inspector import doctor_report_lines

        report = {"summary": "unknown"}
        lines = doctor_report_lines(report)
        assert isinstance(lines, list)
        assert len(lines) >= 1


# ---------------------------------------------------------------------------
# 7. python_version_ok passes on Python 3.12+
# ---------------------------------------------------------------------------


class TestPythonVersionCheck:
    """python_version_ok check reflects runtime Python version."""

    @pytest.mark.asyncio
    async def test_python_version_check_passes_on_312(self) -> None:
        from kora_v2.runtime.inspector import RuntimeInspector

        container = _make_container()
        inspector = RuntimeInspector(container)
        report = await inspector.doctor()

        check = next(
            (c for c in report["checks"] if c["name"] == "python_version_ok"),
            None,
        )
        assert check is not None
        # This environment should be running Python 3.12+
        vi = sys.version_info
        expected = vi >= (3, 12)
        assert check["passed"] == expected
        assert str(vi.major) in check["detail"]
