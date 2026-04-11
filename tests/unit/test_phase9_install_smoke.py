"""Phase 9 install smoke tests.

Regression guards for:
- pyproject.toml pysqlite3-binary platform gate
- kora_v2/__init__.py pysqlite3 try/except block
- scripts/bootstrap_tooling.sh existence, executability, and content
- .env.example Phase 9 env var placeholders
"""
from __future__ import annotations

import stat
from pathlib import Path

_REPO_ROOT = Path(__file__).parents[2]


# ---------------------------------------------------------------------------
# 1. pyproject.toml gates pysqlite3-binary to Linux
# ---------------------------------------------------------------------------


def test_pyproject_pysqlite3_has_linux_platform_gate() -> None:
    """pyproject.toml must specify sys_platform == 'linux' for pysqlite3-binary."""
    try:
        import tomllib  # Python 3.11+
    except ModuleNotFoundError:
        import tomli as tomllib  # type: ignore[no-redef]

    pyproject_path = _REPO_ROOT / "pyproject.toml"
    assert pyproject_path.exists(), "pyproject.toml not found"

    data = tomllib.loads(pyproject_path.read_text())
    deps: list[str] = data.get("project", {}).get("dependencies", [])

    pysqlite_deps = [d for d in deps if "pysqlite3" in d.lower()]
    assert pysqlite_deps, "pysqlite3-binary not found in [project.dependencies]"

    # Every pysqlite3 dep must include the linux platform gate
    for dep in pysqlite_deps:
        assert "linux" in dep.lower(), (
            f"pysqlite3-binary dep must be gated to linux, got: {dep!r}"
        )
        assert "sys_platform" in dep, (
            f"pysqlite3-binary dep must use sys_platform marker, got: {dep!r}"
        )


def test_pyproject_pysqlite3_marker_exact_form() -> None:
    """The marker must use == 'linux' (equality, not 'in')."""
    pyproject_path = _REPO_ROOT / "pyproject.toml"
    content = pyproject_path.read_text()
    # Find the pysqlite3 line and assert it contains the exact marker
    lines = [ln for ln in content.splitlines() if "pysqlite3" in ln]
    assert lines, "pysqlite3 not found in pyproject.toml"
    for line in lines:
        assert "sys_platform == 'linux'" in line or 'sys_platform == "linux"' in line, (
            f"Expected sys_platform == 'linux' gate in: {line!r}"
        )


# ---------------------------------------------------------------------------
# 2. kora_v2/__init__.py contains try/except ImportError for pysqlite3
# ---------------------------------------------------------------------------


def test_init_has_pysqlite3_try_except() -> None:
    """kora_v2/__init__.py must contain the pysqlite3 try/except ImportError block."""
    init_path = _REPO_ROOT / "kora_v2" / "__init__.py"
    assert init_path.exists(), "kora_v2/__init__.py not found"

    content = init_path.read_text()
    assert "pysqlite3" in content, "__init__.py must import pysqlite3"
    assert "try:" in content, "__init__.py must have a try block"
    assert "ImportError" in content, "__init__.py must catch ImportError"
    # Ensure the fallback comment or pass is there
    assert "except ImportError" in content, (
        "__init__.py must have an 'except ImportError' clause"
    )


def test_init_pysqlite3_swaps_sqlite3_module() -> None:
    """The try block must swap sys.modules['sqlite3']."""
    init_path = _REPO_ROOT / "kora_v2" / "__init__.py"
    content = init_path.read_text()
    assert 'sys.modules["sqlite3"]' in content or "sys.modules['sqlite3']" in content, (
        "__init__.py must set sys.modules['sqlite3'] = pysqlite3"
    )


# ---------------------------------------------------------------------------
# 3. scripts/bootstrap_tooling.sh exists, is executable, and has expected content
# ---------------------------------------------------------------------------


def test_bootstrap_script_exists() -> None:
    script = _REPO_ROOT / "scripts" / "bootstrap_tooling.sh"
    assert script.exists(), "scripts/bootstrap_tooling.sh not found"


def test_bootstrap_script_is_executable() -> None:
    script = _REPO_ROOT / "scripts" / "bootstrap_tooling.sh"
    mode = script.stat().st_mode
    assert bool(mode & stat.S_IEXEC), "bootstrap_tooling.sh must be executable"


def test_bootstrap_script_starts_with_set_euo_pipefail() -> None:
    script = _REPO_ROOT / "scripts" / "bootstrap_tooling.sh"
    content = script.read_text()
    assert "set -euo pipefail" in content, (
        "bootstrap_tooling.sh must start with 'set -euo pipefail'"
    )


def test_bootstrap_script_contains_prerequisites_checklist() -> None:
    script = _REPO_ROOT / "scripts" / "bootstrap_tooling.sh"
    content = script.read_text().lower()
    # Must mention prerequisites (or prereqs) in some form
    assert "prerequisite" in content or "checklist" in content, (
        "bootstrap_tooling.sh must contain a prerequisites checklist section"
    )


def test_bootstrap_script_mentions_agent_browser() -> None:
    script = _REPO_ROOT / "scripts" / "bootstrap_tooling.sh"
    content = script.read_text()
    assert "agent-browser" in content or "agent_browser" in content, (
        "bootstrap_tooling.sh must mention agent-browser in its prerequisites"
    )


def test_bootstrap_script_mentions_vault_or_obsidian() -> None:
    script = _REPO_ROOT / "scripts" / "bootstrap_tooling.sh"
    content = script.read_text()
    assert "vault" in content.lower() or "obsidian" in content.lower(), (
        "bootstrap_tooling.sh must mention vault/Obsidian in its prerequisites"
    )


# ---------------------------------------------------------------------------
# 4. .env.example contains Phase 9 env var placeholders
# ---------------------------------------------------------------------------


def test_env_example_contains_mcp_workspace_command() -> None:
    env_example = _REPO_ROOT / ".env.example"
    assert env_example.exists(), ".env.example not found"
    content = env_example.read_text()
    assert "KORA_MCP__SERVERS__workspace__COMMAND" in content, (
        ".env.example must contain KORA_MCP__SERVERS__workspace__COMMAND placeholder"
    )


def test_env_example_contains_browser_binary_path() -> None:
    env_example = _REPO_ROOT / ".env.example"
    content = env_example.read_text()
    assert "KORA_BROWSER__BINARY_PATH" in content, (
        ".env.example must contain KORA_BROWSER__BINARY_PATH placeholder"
    )


def test_env_example_contains_vault_path() -> None:
    env_example = _REPO_ROOT / ".env.example"
    content = env_example.read_text()
    assert "KORA_VAULT__PATH" in content, (
        ".env.example must contain KORA_VAULT__PATH placeholder"
    )
