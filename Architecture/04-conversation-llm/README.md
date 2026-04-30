# Cluster 04 — Conversation, LLM, and User Interface

This cluster covers the full path from a user's typed message to Kora's reply:
how the model is called, how the user's emotional state is inferred, how quality
is measured on generated output, how external tools plug in via MCP, and how the
whole thing is surfaced through the desktop GUI and Rich terminal client.

The cluster owns five subsystems inside `kora_v2/`, plus the desktop app under
`apps/desktop/`:

| Subsystem | Directory | Role |
|---|---|---|
| LLM providers | `llm/` | Abstraction layer over MiniMax M2.7-highspeed and Claude Code |
| Emotion | `emotion/` | Two-tier PAD affect assessment per turn |
| CLI | `cli/` | Rich WebSocket chat client and first-run wizard |
| Desktop GUI | `../../apps/desktop/` | Electron/React Life OS screens, REST view-models, global WebSocket chat |
| MCP | `mcp/` | Model Context Protocol subprocess manager |
| Quality | `quality/` | Per-turn metrics, confidence scoring, and quality gates |

## How a turn flows

```
User types message
    │
    ▼
Desktop chat panel or KoraCLI
  sends: {"type": "chat", "content": "..."}
  over WebSocket ws://<host>:<port>/api/v1/ws?token=<token>
    │
    ▼
Daemon / supervisor graph (kora_v2/graph/)
  ┌─ emotion/fast_assessor.py  ─── immediate PAD estimate (<1 ms)
  │
  ├─ If confidence < 0.5 or large shift
  │    emotion/llm_assessor.py  ── LLM-based PAD estimate (async, ~30 s timeout)
  │
  ├─ llm/minimax.py  ─────────── generate() / generate_stream()
  │    MiniMax M2.7-highspeed via Anthropic SDK, with thinking blocks and tool calling
  │
  ├─ mcp/manager.py  ─────────── call_tool() for external tool servers
  │
  └─ quality/tier1.py  ────────── record_turn() — latency, tokens, gates
       quality/confidence.py  ─── composite confidence score
       quality/gates.py  ──────── execute_with_quality_gates()
    │
    ▼
Client streams back tokens
  {"type": "token"} ... {"type": "response_complete"}
```

## Key design decisions

**Single provider in production.** The active LLM is MiniMax M2.7-highspeed, accessed
through the Anthropic SDK via MiniMax's Anthropic-compatible endpoint at
`https://api.minimax.io/anthropic`. `ClaudeCodeDelegate` exists but is a
subprocess shim for code-heavy autonomous work, not a drop-in provider.

**Two-tier emotion.** `FastEmotionAssessor` runs synchronously on every turn
at negligible cost. `LLMEmotionAssessor` is invoked only when confidence drops
below 0.5 or a PAD axis shifts by more than 0.4 — with a 3-turn cooldown to
avoid thrashing. Results feed back into system prompt construction and
notification throttling.

**MCP over stdio.** External tool servers (e.g., `brave_search`) are managed as
long-lived subprocesses communicating over JSON-RPC on stdin/stdout. The manager
handles lazy startup, handshake, and tool discovery. Older docs overstated automatic crash recovery; the restart helper exists, but normal `call_tool()` failures surface explicitly.

**Quality collection exists, but persistence is not fully automatic on every path.** The supervisor records turn samples in memory; persistence helpers exist but are not guaranteed after every turn. Autonomous work additionally flows through
`execute_with_quality_gates()`, which runs a producer–reviewer cycle with
configurable retry and confidence thresholds.

## Documents in this cluster

| File | Contents |
|---|---|
| `llm.md` | Provider interface, MiniMax implementation, Claude Code delegate, streaming, tool calling, error handling |
| `emotion.md` | PAD model, fast tier math, LLM tier prompts, trigger logic, state schema |
| `cli.md` | Rich app structure, WebSocket protocol, slash commands, first-run wizard |
| `../../apps/desktop/README.md` | Electron/React GUI, desktop view-model API, browser dev bridge, keyboard shortcuts |
| `mcp.md` | Lifecycle, JSON-RPC handshake, tool registration, config format |
| `quality.md` | Tier 1 metrics, confidence formula, quality gates, DB persistence |
