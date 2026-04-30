---
name: acceptance-test-gui
description: Run Kora's GUI-facing Life OS acceptance test with Codex Browser Use or computer-use, proving that user-visible Today, Calendar, Repair, Memory/Vault, Settings, notifications, and global chat behavior match durable Kora V2 runtime evidence.
---

# Kora V2 GUI Acceptance Test Operator

Use this skill when acceptance needs to exercise Kora through a real GUI surface with Codex Browser Use, computer-use, Playwright, or an equivalent browser automation tool. This is a companion to `acceptance-test`, not a replacement. The CLI harness remains the runtime truth surface; the GUI pass proves the user-facing surface is reachable, coherent, and backed by durable state.

Primary question:

> Can a person operate Kora through the GUI for a realistic Life OS week, and do visible UI claims match the backend evidence?

Do not run this as a screenshot demo. Do not announce test objectives to Kora. Act as Jordan, the same overwhelmed local-first Life OS user from the CLI acceptance skill.

## Product Bar

Kora GUI acceptance passes only when all three layers agree:

1. The GUI exposes the expected user-facing state or control.
2. Kora's conversation and interaction behavior is appropriate for the scenario.
3. Durable runtime evidence proves the claim through harness output, DB rows, logs, snapshots, working docs, or report artifacts.

Do not mark an item green from a visible card alone. Do not mark an item green from chat alone. Do not mark an item green from backend rows if the user cannot see or act on the result.

## Active Runtime

Develop and test against `kora_v2/` only. The CLI entrypoint is `kora = "kora_v2.daemon.launcher:main"`. Historical `kora/` paths and archive docs are not active runtime targets.

Use local-only GUI targets. Servers must bind to `127.0.0.1`. Never put private runtime paths or local secrets in public docs.

Expected GUI surfaces, if implemented:

- global Kora chat available from every screen
- Today / Plan Today
- Calendar
- Repair / Confirm Reality / Repair The Day
- Memory, Vault, Context packs, and provenance
- Settings, support modes, local-first controls, runtime health
- notification or proactive nudge surface
- visible auth relay / permission prompt surface when a tool needs approval

If a surface is not implemented, record it as missing GUI coverage. Do not substitute backend-only proof and call the GUI item passed.

## Clean Start

Start from clean acceptance state before browser testing.

```bash
python3 -m tests.acceptance.automated stop || true
rm -rf /tmp/claude/kora_acceptance
KORA_MEMORY__KORA_MEMORY_PATH=/tmp/claude/kora_acceptance/memory python3 -m tests.acceptance.automated start
```

Fast mode:

```bash
python3 -m tests.acceptance.automated stop || true
rm -rf /tmp/claude/kora_acceptance
KORA_MEMORY__KORA_MEMORY_PATH=/tmp/claude/kora_acceptance/memory python3 -m tests.acceptance.automated start --fast
```

Keep cleanup scoped. Do not wipe `data/`, `_KoraMemory/`, or real user memory. If stale Jordan/Mochi/Alex/persona facts leak from persistent memory, stop and investigate before trusting the run.

Helper:

```bash
python3 .agents/skills/acceptance-test-gui/scripts/start_gui_acceptance.py --fast --gui-url http://127.0.0.1:5177
```

The helper starts the acceptance harness and writes a run manifest under `/tmp/claude/kora_acceptance/gui_acceptance/`.

## Browser Operator Loop

For each scenario:

1. Open the GUI in Browser Use or equivalent browser automation.
2. Capture baseline evidence: page URL, screenshot, visible text, console errors, network/API failures.
3. Act as Jordan through visible UI controls and global chat.
4. Verify the expected GUI change is visible.
5. Run the matching CLI harness checks.
6. Capture DB/log/file evidence.
7. Record conflicts instead of smoothing them over.

Use harness commands through:

```bash
python3 -m tests.acceptance.automated <command>
```

Core checks to pair with GUI actions:

- `status`
- `send`
- `advance`
- `snapshot`
- `diff`
- `idle-wait`
- `phase-gate`
- `orchestration-status`
- `pipeline-history`
- `working-docs`
- `notifications`
- `insights`
- `phase-history`
- `vault-snapshot`
- `life-management-check`
- `tool-usage-summary`
- `test-auth`
- `test-error`
- `skill-gating-check`
- `report`

Use `references/gui_operator_scenarios.md` for the day-by-day browser script. Use `references/evidence_matrix.md` for pass/fail rules.

## Week Shape

Run the same realistic lived week as the CLI acceptance skill, but require a GUI proof moment for each primary product axis.

- Day 1: onboarding context, local-first preference, trusted support boundary, calendar spine, first drift, wrong-inference repair, future-self bridge.
- Day 2: ADHD/executive dysfunction, tiny next action, time blindness, life-admin decomposition, cancellation.
- Day 3: autism/sensory load, routine disruption, low-ambiguity sequencing, communication fatigue, permissioned support.
- Day 4: burnout/anxiety/low energy, stabilization before planning, downshifted calendar, essentials, crisis boundary.
- Day 5: auth relay, error recovery, compaction/runtime health, capability health, memory/vault background work.
- Day 6: proactivity, useful nudge timing, preparation, stuckness detection, suppression/defer feedback.
- Day 7: restart continuity, weekly review, artifact-backed closeout.

## GUI Evidence Standards

Capture these artifacts when possible:

- screenshots before and after important actions
- visible text or accessibility snapshot for the active surface
- current URL/route/window title
- console errors and failed network requests
- API response status for GUI data endpoints
- user-facing notification state
- selected rows/cards/items in Today, Calendar, Repair, Memory/Vault, Settings
- restart before/after screenshots for continuity

Store GUI artifacts under:

```text
/tmp/claude/kora_acceptance/gui_acceptance/
```

Recommended filenames:

```text
day1_today_before.png
day1_today_after.png
day2_repair_cancel_after.png
day3_sensory_context_pack.png
day4_crisis_boundary.png
day6_notifications_after_suppression.png
day7_restart_today_after.png
gui_evidence.jsonl
gui_reconciliation.md
```

Helper:

```bash
python3 .agents/skills/acceptance-test-gui/scripts/collect_gui_evidence.py
```

## Durable Evidence Standards

Truth surfaces include:

- `/tmp/claude/kora_acceptance/acceptance_output/acceptance_report.md`
- `/tmp/claude/kora_acceptance/acceptance_output/test_log.jsonl`
- `/tmp/claude/kora_acceptance/acceptance_output/acceptance_monitor.md`
- snapshots and diffs under the acceptance output directory
- `data/operational.db`
- `pipeline_instances`
- `worker_tasks`
- `work_ledger`
- `system_state_log`
- `notifications`
- `permission_grants`
- `open_decisions`
- life tables for day plans, life events, repair actions, support profiles, context packs, reminders, medications, meals, routines
- actual working docs under the active acceptance memory root

Treat `coverage.md` as a tracker, not final truth. Treat GUI screenshots as user-facing evidence, not durable proof by themselves.

## Report Bar

The final GUI report must include:

- GUI verdict by surface: Today, Calendar, Repair, global chat, Memory/Vault/Context, Settings/runtime health, notifications/auth.
- Scenario verdict by support track: ADHD, autism/sensory, burnout/anxiety, calendar/state, proactivity, safety/trusted support.
- Week replay with screenshots and exact visible UI state.
- Runtime proof index with DB tables, event IDs, tool calls, snapshots, working docs, and report paths.
- Reconciliation section: where GUI, chat, DB, and report agree or conflict.
- Non-proof section: missing surfaces, stale artifacts, unavailable tools, backend-only behavior, chat-only claims.

Use:

```bash
python3 .agents/skills/acceptance-test-gui/scripts/reconcile_gui_report.py
```

## What Does Not Count

Do not count:

- a pretty dashboard with no backing rows
- a reminder card that is not persisted
- a calendar claim based only on prompt text
- a weekly review that summarizes untracked work
- generic ADHD/autism/anxiety advice with no state change
- proactivity that is only a check-in
- a background job that completed but is invisible in the GUI
- GUI success after a hidden API error
- fabricated web/current facts after tool failure
- crisis language converted into normal productivity planning

## Operator Rules

1. Be Jordan, not QA.
2. Use the GUI first, then verify with harness/runtime evidence.
3. Keep global chat available as a cross-surface control, not a separate isolated tab.
4. Keep ADHD and autism/sensory separate.
5. Prefer one-day-at-a-time flow when Jordan is overwhelmed.
6. Preserve local-first/no-cloud expectations.
7. Surface GUI gaps plainly.
8. Never hide console, network, DB, or report conflicts.
9. End with an honest GUI/runtime reconciliation, not a trophy score.
