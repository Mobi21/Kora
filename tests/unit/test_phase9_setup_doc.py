"""Phase 9 setup doc regression tests.

Verifies that docs/phase9/setup.md exists and contains the required
section headers and env var placeholders.
"""
from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).parents[2]
_SETUP_DOC = _REPO_ROOT / "docs" / "phase9" / "setup.md"


# ---------------------------------------------------------------------------
# 1. File exists
# ---------------------------------------------------------------------------


def test_setup_doc_exists() -> None:
    assert _SETUP_DOC.exists(), f"docs/phase9/setup.md not found at {_SETUP_DOC}"


def _doc_content() -> str:
    return _SETUP_DOC.read_text()


# ---------------------------------------------------------------------------
# 2. Required section headers (case-insensitive)
# ---------------------------------------------------------------------------


def test_setup_doc_has_prerequisites_section() -> None:
    content = _doc_content().lower()
    assert "prerequisite" in content or "prereq" in content, (
        "setup.md must contain a Prerequisites or Prereqs section"
    )


def test_setup_doc_has_bootstrap_or_install_section() -> None:
    content = _doc_content().lower()
    assert "bootstrap" in content or "install" in content, (
        "setup.md must contain a Bootstrap or Install section"
    )


def test_setup_doc_has_workspace_section() -> None:
    content = _doc_content().lower()
    assert "workspace" in content, (
        "setup.md must contain a Workspace section"
    )


def test_setup_doc_has_browser_section() -> None:
    content = _doc_content().lower()
    assert "browser" in content or "agent-browser" in content, (
        "setup.md must contain a Browser or agent-browser section"
    )


def test_setup_doc_has_vault_or_obsidian_section() -> None:
    content = _doc_content().lower()
    assert "vault" in content or "obsidian" in content, (
        "setup.md must contain a Vault or Obsidian section"
    )


def test_setup_doc_has_verification_section() -> None:
    content = _doc_content().lower()
    assert "verification" in content or "verify" in content, (
        "setup.md must contain a Verification or Verify section"
    )


# ---------------------------------------------------------------------------
# 3. Env var placeholders
# ---------------------------------------------------------------------------


def test_setup_doc_mentions_vault_path_var() -> None:
    content = _doc_content()
    assert "KORA_VAULT__PATH" in content, (
        "setup.md must mention KORA_VAULT__PATH env var"
    )


def test_setup_doc_mentions_browser_binary_path_var() -> None:
    content = _doc_content()
    assert "KORA_BROWSER__BINARY_PATH" in content, (
        "setup.md must mention KORA_BROWSER__BINARY_PATH env var"
    )


def test_setup_doc_mentions_mcp_servers_workspace_command() -> None:
    """setup.md should reference the MCP workspace config pattern."""
    content = _doc_content()
    # Can be KORA_MCP__SERVERS__workspace__COMMAND or just workspace__COMMAND
    assert "workspace" in content and "COMMAND" in content, (
        "setup.md must show MCP workspace server command configuration"
    )


# ---------------------------------------------------------------------------
# 4. Non-empty content sanity
# ---------------------------------------------------------------------------


def test_setup_doc_is_not_empty() -> None:
    content = _doc_content()
    assert len(content.strip()) > 200, (
        "setup.md appears too short — it should have substantial content"
    )


def test_setup_doc_has_markdown_headers() -> None:
    content = _doc_content()
    headers = [line for line in content.splitlines() if line.startswith("#")]
    assert len(headers) >= 4, (
        f"setup.md should have at least 4 markdown headers, found {len(headers)}"
    )
