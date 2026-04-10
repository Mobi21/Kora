# Kora V2 Acceptance Test Report

Generated: 2026-04-07T02:46:13Z
Started: 2026-04-07T02:20:00Z (after bug fixes)
Simulated time elapsed: +28.0h (3 days)

Conversation: 19 user turns, 19 assistant turns

---

## Bugs Found and Fixed (4 critical, pre-test)

| # | Bug | Root Cause | Fix | File |
|---|-----|-----------|-----|------|
| 1 | **Double graph invocation** | `_handle_chat` ran `astream_events()` then fell back to `ainvoke()` on same thread — every message processed twice, doubling conversation history | Removed streaming fallback, use `ainvoke()` only | `kora_v2/daemon/server.py` |
| 2 | **Lockfile port=0** | Daemon wrote `api_port=0` before uvicorn bound, never updated with actual port. Launcher couldn't health-probe. | Added `on_bind` callback in `run_server()` to update lockfile after binding | `server.py`, `launcher.py` |
| 3 | **Operational DB empty** | `init_operational_db()` never called during startup. Life management, autonomous, quality tables all missing. | Added call in `_run_daemon()` before subsystem init | `launcher.py` |
| 4 | **Greeting polluting checkpoint** | `generate_greeting()` used same thread_id as main conversation. Greeting prompt stored as user message in LangGraph checkpoint. | Separate `greeting-{session_id}` thread for greeting | `session.py` |

Additional issues found:
- Lockfile path mismatch: harness looked at `data/.lockfile`, daemon wrote `data/kora.lock` (fixed in `automated.py` and `_harness_server.py`)
- Harness send timeout too short (150s) for planner worker calls (~95s). Increased to 300s.

---

## Coverage -- Active Items (Observed Results)

| # | Item | Result | Evidence |
|---|------|--------|----------|
| 2 | Jordan's personal context | **PASS** | Name, age, ADHD, Alex, Mochi, Portland, software engineer — all established and recalled within session |
| 3 | Week planning across 3 tracks | **PASS** | Full Mon-Fri plan with daily tasks, ADHD-aware energy scheduling |
| 4 | Coding track: plan -> implement -> revise | **PASS** | Architecture designed, component tree, data model, carryover-to-tomorrow revision |
| 5 | Research track: kickoff -> gathering -> synthesis | **PASS** | 4-category landscape, tool comparison matrix, Super Productivity selected |
| 6 | Writing track: outline -> draft -> revision | **PASS** | Outline -> stakeholder brief -> README/launch-note hybrid revision |
| 7 | Life management tools used | **PARTIAL** | `log_medication` x3, `log_meal` x1, `start_focus_block` x1, `end_focus_block` x1, `create_reminder` x3. Missing: melatonin not logged, no `quick_note` calls |
| 9 | Web search/fetch via MCP | **FAIL** | MCP `server_count=0` at startup. Kora transparently reported "server config issue". No brave_search/fetch configured. |
| 10 | Long-context compaction survived | **NOT TESTED** | Conversation too short (19 turns) to trigger compaction threshold |
| 11 | Revision wave absorbed | **PASS** | All 3 tracks revised in single turn: daily view (not weekly), privacy-only filter, README format |
| 13 | Restart resilience | **PARTIAL** | Daemon restarted successfully. SQLite records survived. Conversation context lost (in-memory MemorySaver, no SQLite checkpointer). Session bridge saved but empty ("Empty session"). |
| 14 | Weekly review matches run | **PARTIAL** | Review given but shallow — post-restart amnesia meant Kora worked from re-introduction only |
| 15 | Compaction detected in metadata | **NOT TESTED** | No compaction events fired |
| 16 | Memory recall returns earlier facts | **PARTIAL** | Pre-restart: excellent recall of all facts, projects, decisions. Post-restart: total amnesia. |
| 17 | Auth relay round-trip | **NOT TESTED** | Harness auto-approves all auth. `test-auth` command not implemented in harness server. |
| 18 | Error recovery | **PASS** | Empty message rejected ("Empty message content"), session survived, next message processed normally |
| 19 | Emotion/energy adapts tone | **PASS** | "meds wearing off" -> ADHD-aware energy advice, task difficulty matching. "focus is shot" -> suggested low-cognitive-load work. |
| 20 | Skill activation gates tools | **PARTIAL** | Life management, filesystem, planner tools all dispatched when needed. Skill gating not independently verified. |
| 21 | Autonomous execution | **FAIL** | LLM never called `start_autonomous`. Used `dispatch_worker` for "background research" instead. Autonomous loop infrastructure exists but wasn't triggered. |
| 22 | File operations via filesystem | **PASS** | 3 files created: `focus-week-plan.md` (5270 bytes), `research-notes.md` (5889 bytes), `stakeholder-brief.md`. `list_directory` used. |
| 23 | Life management DB persists | **PASS** | All records survived daemon restart: 3 medication entries, 1 meal, 1 focus block (completed), 3 reminders |

### Summary: 9 PASS, 4 PARTIAL, 2 FAIL, 5 NOT TESTED

---

## Coverage -- Deferred Items

- [~] **1. First-run onboarding** — DEFERRED: V2 has no first-run wizard
- [~] **8. Planner/reviewer subagent delegation** — DEFERRED: executor harness exists, planner/reviewer stubs only
- [~] **12. Monitored idle with grounded follow-through** — DEFERRED: BackgroundWorker runs only housekeeping items; no proactive agent, no self-directed autonomous work (see Idle Mode Gap below)

---

## Tool Usage (27 calls total)

All tool names reported as "unknown" due to harness metadata parsing bug. Actual tools called (verified from daemon logs and DB records):

| Category | Tools Called | Count |
|----------|-------------|-------|
| Life management | `log_medication`, `log_meal`, `start_focus_block`, `end_focus_block`, `create_reminder` | ~8 |
| Filesystem | `write_file`, `list_directory` | ~4 |
| Workers | `dispatch_worker` (planner) | ~6 |
| Memory | `recall` | ~3 |
| MCP (web) | `search_web` (attempted, failed) | ~2 |
| Autonomous | `start_autonomous` | 0 |

---

## Life Management Records (verified directly from operational.db)

### Medication Log (3 entries)
| Medication | Dose | Taken At |
|-----------|------|----------|
| Adderall | 20mg | 2026-04-07T02:22:19Z (Day 1 morning) |
| Adderall | 20mg | 2026-04-07T02:34:05Z (Day 2 morning) |
| Adderall | (empty) | 2026-04-07T02:40:35Z (Day 3 morning) |

### Meal Log (1 entry)
| Description | Type | Calories |
|------------|------|----------|
| Coffee and a bagel | breakfast | 300 |

### Focus Blocks (1 entry)
| Label | Completed |
|-------|-----------|
| React Focus Dashboard - Architecture | Yes (4.8 min) |

### Reminders (3 entries)
| Title | Remind At | Status |
|-------|----------|--------|
| Check API docs tomorrow morning | 2026-04-08T14:00 | pending |
| Set up CI pipeline next week | 2026-04-13T09:00 | pending |
| Standup tomorrow at 9am | 2026-04-09T14:00 | pending |

### Quick Notes
(none created)

---

## Autonomous Execution

No autonomous work initiated. The LLM chose `dispatch_worker` (planner) over `start_autonomous` when asked to "work in the background." The autonomous loop infrastructure (12-node state machine, checkpoint/resume, budget enforcement) exists but was never triggered.

**Root cause:** The supervisor prompt's delegation guidance doesn't strongly distinguish "background task that runs while user is away" from "plan this task." The LLM defaults to the planner worker.

---

## Idle Mode Gap (Critical Finding)

**Designed (PRD Phase 5 + 8b):**
- Proactive Agent: medication/meal reminders, re-engagement protocol, time blindness protection, hyperfocus protection
- Background Agents: transcript extraction, memory steward (consolidation/dedup/ADHD profile), self-improvement
- Auto-triggered autonomous work from open conversation threads

**Implemented:**
- 5 housekeeping work items that all complete in <5ms with no real work
- No proactive notifications
- No self-directed autonomous work
- No transcript processing
- No memory consolidation

**During the 30s idle soak:** 7 health polls, 0 changes, 0 items created. Daemon was healthy but completely inert.

---

## Compaction

No compaction events detected. Conversation stayed well within budget (19 turns, ~15K tokens estimated).

---

## Snapshots (7 captured)

| Snapshot | Timestamp | Messages | Items |
|----------|-----------|----------|-------|
| day1_end | 02:31:55 | 16 | 4 |
| day2_pre_autonomous_idle | 02:38:04 | 26 | 4 |
| day2_post_autonomous_idle | 02:38:49 | 26 | 4 |
| day2_end | 02:39:52 | 28 | 14 |
| pre_restart | 02:43:21 | 34 | 14 |
| post_restart | 02:43:58 | 34 | 14 |
| day3_final | 02:46:09 | 38 | 14 |

Overall state change: Messages 16 -> 38, Items 4 -> 14, Checkpoints 3 -> 6

---

## Harness Infrastructure Gaps

| Missing Command | Impact |
|----------------|--------|
| `test-auth` / `test-auth-reset` | Auth relay not testable |
| `test-error` | Error recovery only manually tested |
| `compaction-status` | Compaction monitoring unavailable |
| `life-management-check` | Had to query DB directly |
| `tool-usage-summary` | Tool names all "unknown" |

Additional harness issues:
- `trace_id`, `latency_ms`, `session_id` always null in response metadata
- Tool names not parsed from graph response (all show as "unknown")
- Coverage tracker never auto-updated (manual observation only)

---

## Files Created During Test

| File | Size | Content |
|------|------|---------|
| `focus-week-plan.md` | 5,270 bytes | Dashboard architecture, component tree, data model, tech stack, MVP scope |
| `research-notes.md` | 5,889 bytes | 4-category tool landscape, comparison matrices, ADHD lens, recommendations |
| `stakeholder-brief.md` | (created) | README/launch-note hybrid for manager |

---

## Conversation Quality Assessment

**Strengths:**
- ADHD awareness was consistently excellent (energy matching, micro-steps, no shaming)
- Architecture feedback was detailed and opinionated (not generic)
- Revision waves absorbed cleanly with concrete updates
- Proactive lunch reminder when Jordan mentioned skipping
- Transparent about web search unavailability (radical honesty principle)

**Weaknesses:**
- Post-restart amnesia (in-memory checkpointer)
- Tools reported as "unknown" in harness
- Planner worker took 95s (caused timeout on first real message)
- Melatonin mention not logged (LLM didn't call tool)
- "Background task started" claim but no autonomous loop actually ran

---

## Recommendations

1. **Fix idle mode** — Implement proactive agent (Phase 5) and auto-triggered autonomous work
2. **SQLite checkpointer** — Replace MemorySaver for conversation persistence across restarts
3. **Configure MCP servers** — brave_search + fetch for web research capability
4. **Strengthen autonomous routing** — Prompt engineering to prefer `start_autonomous` for "work while I'm away"
5. **Fix harness metadata** — Parse tool names, trace_id, latency from graph response
6. **Implement missing harness commands** — test-auth, life-management-check, tool-usage-summary
