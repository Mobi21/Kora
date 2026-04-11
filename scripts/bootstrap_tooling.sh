#!/usr/bin/env bash
# bootstrap_tooling.sh — set up the Kora v2 Python environment and print
# a checklist of non-Python prerequisites.
#
# Usage:
#   bash scripts/bootstrap_tooling.sh
#
# The script detects or creates a .venv in the repo root, installs the
# editable package with dev extras, and runs a quick import sanity check.
# It does NOT assume network access works — if pip fails it prints a
# diagnostic message and exits non-zero.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$REPO_ROOT/.venv"

# ── helpers ──────────────────────────────────────────────────────────────────

info()  { printf '\033[0;34m[bootstrap]\033[0m %s\n' "$*"; }
ok()    { printf '\033[0;32m[ok]\033[0m %s\n' "$*"; }
warn()  { printf '\033[0;33m[warn]\033[0m %s\n' "$*"; }
fail()  { printf '\033[0;31m[error]\033[0m %s\n' "$*" >&2; }

# ── 1. Detect or create the venv ─────────────────────────────────────────────

cd "$REPO_ROOT"

if [[ -d "$VENV_DIR" ]]; then
    info "Found existing .venv at $VENV_DIR"
else
    info "Creating .venv …"
    python3 -m venv "$VENV_DIR"
    ok "Created .venv"
fi

PYTHON="$VENV_DIR/bin/python"
PIP="$VENV_DIR/bin/pip"

# ── 2. Install the package ────────────────────────────────────────────────────

info "Installing kora_v2 in editable mode with dev extras …"
if "$PIP" install -e '.[dev]' --quiet; then
    ok "pip install -e '.[dev]' succeeded"
else
    fail "pip install failed."
    fail "Common causes:"
    fail "  • No network access — run on a machine with PyPI access, or use --find-links with a local cache"
    fail "  • Python < 3.12 — check: python3 --version"
    fail "  • Missing system build tools — on macOS: xcode-select --install"
    exit 1
fi

# ── 3. Import sanity check ────────────────────────────────────────────────────

info "Running import sanity check …"
if "$PYTHON" -c "import kora_v2; print('kora_v2', kora_v2.__version__)"; then
    ok "import kora_v2 passed"
else
    fail "import kora_v2 failed — the install succeeded but something is broken at import time."
    fail "Run: .venv/bin/python -c 'import kora_v2' for the full traceback."
    exit 1
fi

# ── 4. Non-Python prerequisites checklist ────────────────────────────────────

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Non-Python prerequisites checklist"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Node 18+
if command -v node &>/dev/null; then
    NODE_VER="$(node --version)"
    NODE_MAJOR="${NODE_VER#v}"; NODE_MAJOR="${NODE_MAJOR%%.*}"
    if [[ "$NODE_MAJOR" -ge 18 ]]; then
        ok "Node $NODE_VER (≥ 18 required for agent-browser)"
    else
        warn "Node $NODE_VER found but 18+ is required. Upgrade: https://nodejs.org/"
    fi
else
    warn "Node not found — Node 18+ is required for agent-browser."
    warn "Install: https://nodejs.org/ or via Homebrew: brew install node"
fi

# Google Workspace MCP server
echo ""
echo "  [ ] Google Workspace MCP server"
echo "      Kora uses an external Google Workspace MCP server for Calendar,"
echo "      Gmail, Drive, and Docs access."
echo "      Recommended: https://github.com/taylorwilsdon/google_workspace_mcp"
echo ""
echo "      Quick start (requires uv/uvx):"
echo "        uvx google-workspace-mcp"
echo ""
echo "      Configure in .env or settings.toml:"
echo "        KORA_MCP__SERVERS__workspace__COMMAND=uvx"
echo "        KORA_MCP__SERVERS__workspace__ARGS=[\"google-workspace-mcp\"]"
echo ""
echo "      See docs/phase9/setup.md for full OAuth credential setup."

# Obsidian vault
echo ""
echo "  [ ] Obsidian vault path (optional — for vault mirroring)"
echo "      Set the path to your Obsidian vault in .env or settings.toml:"
echo "        KORA_VAULT__PATH=~/ObsidianVault"
echo ""
echo "      If unset, vault features are silently disabled."

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# ── 5. Verification commands ──────────────────────────────────────────────────

ok "Bootstrap complete. Verification commands:"
echo ""
echo "  # Unit tests"
echo "  .venv/bin/python -m pytest tests/unit -q"
echo ""
echo "  # Lint"
echo "  .venv/bin/ruff check kora_v2/ tests/"
echo ""
echo "  # Import sanity"
echo "  .venv/bin/python -c \"import kora_v2; print('ok')\""
echo ""
echo "  # Inspector / doctor"
echo "  .venv/bin/python -m kora_v2.runtime.inspector doctor"
echo ""
