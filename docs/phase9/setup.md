# Phase 9 Setup Guide — macOS

Step-by-step instructions for setting up Kora v2 on a clean macOS machine,
including Python dependencies, the Google Workspace MCP server, agent-browser,
and Obsidian vault mirroring.

---

## Prerequisites

| Requirement | Minimum | Notes |
|---|---|---|
| Python | 3.12+ | `python3 --version` |
| Node.js | 18+ | Required for agent-browser |
| System sqlite3 | 3.38+ | macOS Ventura+ ships 3.39 (`sqlite3 --version`) |
| Homebrew | any | Optional — convenient for Node and uv |
| uv / uvx | any | Needed to run the Google Workspace MCP server via `uvx` |

### Install Python 3.12+

macOS ships Python 3.x but often at an older version. Recommended approach:

```bash
brew install python@3.12
```

Or download directly from https://www.python.org/downloads/.

### Install Node 18+

```bash
brew install node
# or: nvm install 18 && nvm use 18
```

Verify: `node --version`

### Install uv (recommended for MCP server)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Verify: `uvx --version`

---

## Python environment bootstrap

Clone the repo and run the bootstrap script:

```bash
git clone <repo-url> kora && cd kora
bash scripts/bootstrap_tooling.sh
```

The script will:
1. Create `.venv` if it does not exist.
2. Run `pip install -e '.[dev]'`.
3. Run `python -c "import kora_v2"` as a sanity check.
4. Print the non-Python prerequisites checklist.

### Platform note: pysqlite3-binary on macOS

`pysqlite3-binary` is a Linux-only dependency. On macOS it is skipped
(`pyproject.toml` gates it with `sys_platform == 'linux'`).

`kora_v2/__init__.py` already handles this: it attempts to import
`pysqlite3` and falls back to the stdlib `sqlite3` if unavailable.

The sqlite-vec extension (`sqlite-vec~=0.1.6`) uses `load_extension`, which
requires sqlite3 3.38 or newer. macOS Ventura (13+) ships 3.39, so this works
without any additional steps. If you are on an older macOS release, upgrade
sqlite via Homebrew:

```bash
brew install sqlite
```

and ensure the Homebrew sqlite is on your PATH before the system one.

### Manual bootstrap (without the script)

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -c "import kora_v2; print('ok')"
```

---

## Google Workspace MCP setup

Kora uses an external Google Workspace MCP server to access Calendar, Gmail,
Drive, and Docs. The recommended server is
[taylorwilsdon/google_workspace_mcp](https://github.com/taylorwilsdon/google_workspace_mcp).

### Step 1 — Create OAuth credentials

1. Go to https://console.cloud.google.com/.
2. Create (or select) a project.
3. Enable the APIs you need: Calendar, Gmail, Drive, Docs.
4. Create an OAuth 2.0 credential (Desktop app type).
5. Download the JSON file, e.g. `~/google-oauth-credentials.json`.

### Step 2 — Configure Kora

Add to `.env` (or `settings.toml`):

```env
KORA_MCP__SERVERS__workspace__COMMAND=uvx
KORA_MCP__SERVERS__workspace__ARGS=["google-workspace-mcp"]
KORA_MCP__SERVERS__workspace__ENV__GOOGLE_OAUTH_CREDENTIALS=/Users/yourname/google-oauth-credentials.json
```

Or the equivalent block in `settings.toml`:

```toml
[mcp.servers.workspace]
command = "uvx"
args = ["google-workspace-mcp"]

[mcp.servers.workspace.env]
GOOGLE_OAUTH_CREDENTIALS = "/Users/yourname/google-oauth-credentials.json"
```

### Step 3 — Authorise the server

The first time the server is invoked it will open a browser window for OAuth
consent. Follow the prompts. The token is cached by the MCP server process.

### Step 4 — Verify

Start Kora and ask it to list your upcoming calendar events. Alternatively,
run the inspector doctor:

```bash
.venv/bin/python -m kora_v2.runtime.inspector doctor
```

The output should show `workspace` as `configured` and list the discovered
tool names from the MCP server.

---

## agent-browser setup

> **TODO — verify the exact npm package name and CLI flags before using
> this section.** The steps below reflect the expected integration target
> (`agent-browser`), but the exact package name on npm must be confirmed
> against the upstream project.

Kora uses `agent-browser` for browser automation (web clipping, read
continuity when MCP is unavailable, etc.).

### Install

```bash
# TODO: replace with the verified npm package name
npm install -g @vercel-labs/agent-browser
```

Verify: `agent-browser --version`

### Configure

Add to `.env`:

```env
KORA_BROWSER__BINARY_PATH=/usr/local/bin/agent-browser
```

If `agent-browser` is on your `PATH` and the binary is named `agent-browser`,
you can omit this — Kora will discover it automatically.

### Verify

Run the inspector doctor — it checks for the binary and prints the version:

```bash
.venv/bin/python -m kora_v2.runtime.inspector doctor
```

If the binary is absent, browser capability will be disabled and the doctor
output will say so explicitly.

---

## Obsidian vault setup

The vault capability lets Kora mirror clipped content and notes to an Obsidian
vault. The vault is a write target — Kora's canonical memory stays in
`_KoraMemory/`.

### Configure

Add to `.env`:

```env
KORA_VAULT__PATH=~/ObsidianVault
```

Or the equivalent in `settings.toml`:

```toml
[vault]
path = "~/ObsidianVault"
```

The path is expanded at runtime (`~` is resolved). Kora will create a `Clips/`
subdirectory inside the vault for browser-clipped pages.

If the vault path is not configured, vault features are silently disabled —
the capability returns a non-fatal `StructuredFailure(recoverable=True)` and
no files are written.

---

## Verification

Run the full unit test suite to confirm the installation is healthy:

```bash
.venv/bin/python -m pytest tests/unit -q
```

Run the inspector doctor for a capability health summary:

```bash
.venv/bin/python -m kora_v2.runtime.inspector doctor
```

Expected doctor output includes:
- Python version and sqlite3 version
- kora_v2 import status
- MCP server configuration status (with tool list if reachable)
- agent-browser binary presence and version
- Vault path configuration and writability
- Capability-pack registration status

---

## Troubleshooting

**`pip install` fails on macOS with a build error**

Install Xcode command-line tools:

```bash
xcode-select --install
```

**`import kora_v2` fails with a sqlite error**

Check your sqlite3 version:

```bash
sqlite3 --version
```

If it is below 3.38, upgrade via Homebrew:

```bash
brew install sqlite
export PATH="$(brew --prefix sqlite)/bin:$PATH"
```

**Google Workspace MCP server not reachable**

Check that `uvx` is installed and on your PATH, and that the OAuth credentials
file path in your config is correct. Re-run the OAuth flow by deleting the
cached token (location depends on the MCP server implementation — check its
README).

**agent-browser not found**

Confirm the binary is on your PATH:

```bash
which agent-browser
agent-browser --version
```

If missing, re-install via npm and ensure your npm global bin directory is on
`PATH`.
