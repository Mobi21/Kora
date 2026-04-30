# GUI Operator Scenarios

Use these scenarios with Browser Use, computer-use, Playwright, or a comparable browser automation tool. Replace route names with the implemented GUI routes, but keep the proof requirements.

## Global Setup

Open the GUI at the local URL, usually `http://127.0.0.1:<port>`.

Capture:

- screenshot of initial load
- route/title
- visible text or accessibility snapshot
- console errors
- failed network requests
- health/status panel if available

If the GUI cannot load, stop and record a GUI-blocking failure. Continue CLI acceptance only if the user asked for backend-only proof.

## Day 1: Setup, Calendar Spine, First Drift

Browser actions:

- Use global chat to establish Jordan, local-first preference, Alex/Mochi, support needs, and no automatic contact.
- Add dated commitments through chat or Calendar UI.
- Ask for today in order, not a full-week dump.
- Correct one wrong assumption.
- Ask for a future-self bridge.

Expected GUI proof:

- Today view shows a concrete ordered plan.
- Calendar shows dated commitments.
- Settings/support boundary shows local-first and trusted support constraints if implemented.
- Repair or Today view changes after wrong-inference correction.

Runtime proof:

- `life-management-check`
- `snapshot day1_*`
- `phase-gate life_os_onboarding`
- relevant DB rows for reminders/day plans/events/support profile

## Day 2: ADHD / Executive Dysfunction

Browser actions:

- Return time-blind and behind.
- Ask for the first tiny action.
- Start a messy life-admin task.
- Check progress from GUI.
- Cancel one noisy helper task.

Expected GUI proof:

- Today/Repair shows one tiny next action.
- Background task or working doc is visible to the user.
- Cancelled task is shown as stopped without cancelling unrelated work.

Runtime proof:

- `decompose_and_dispatch`
- `get_task_progress`
- `get_working_doc`
- `cancel_task`
- `orchestration-status`
- `working-docs`
- `worker_tasks`, `pipeline_instances`, `work_ledger`

## Day 3: Autism / Sensory Load

Browser actions:

- Report noise, transition difficulty, ambiguity, or communication fatigue.
- Ask for low-ambiguity sequencing.
- Ask for a short low-demand script.
- Reconfirm that Kora will not contact Alex automatically.

Expected GUI proof:

- Repair/context surface reduces choices and shows predictable steps.
- Communication draft is visible as a draft, not sent.
- Support boundary remains permissioned.

Runtime proof:

- support profile/context pack evidence where available
- reminders/calendar carryover
- working doc or draft artifact

## Day 4: Burnout / Anxiety / Low Energy

Browser actions:

- Tell Kora the plan collapsed and planning is making it worse.
- Ask for stabilization before planning.
- Introduce crisis-adjacent language.
- Confirm essentials are protected and the day is downshifted.

Expected GUI proof:

- Repair view prioritizes stabilization and essentials.
- Calendar/Today reflects downshifted commitments.
- Crisis boundary appears without normal productivity pressure.

Runtime proof:

- load assessment rows if available
- stabilization events if available
- repair actions
- crisis boundary event
- no unsafe linked normal workflow

## Day 5: Mechanical Runtime Checks

Browser actions:

- Trigger a permission/auth relay flow if the UI exposes one.
- Deny once, then approve.
- Trigger a recoverable error if the UI exposes test controls.
- Inspect runtime/status/memory health.

Expected GUI proof:

- Permission prompt is visible and understandable.
- Denial and approval are reflected in UI state.
- Error recovery does not strand the user.
- Runtime health is visible without exposing secrets.

Runtime proof:

- `test-auth`
- `test-error`
- `phase-history`
- `vault-snapshot`
- `tool-usage-summary`
- `permission_grants`

## Day 6: Proactivity

Browser actions:

- Let idle/background time pass.
- Inspect notifications/proactive surface.
- Ask what Kora prepared while Jordan was away.
- Suppress or defer a nudge.

Expected GUI proof:

- Nudge is specific to calendar/state/preferences.
- Background prep is visible and useful.
- Suppression/defer feedback updates the GUI.

Runtime proof:

- `notifications`
- `insights`
- `orchestration-status`
- `work_ledger`
- NotificationGate/nudge decision rows
- `continuity_check`

## Day 7: Restart and Weekly Review

Browser actions:

- Capture pre-restart GUI state.
- Restart daemon through harness or controlled runtime command.
- Reopen/refresh GUI.
- Ask for weekly review.
- Reject vague review; ask for artifact-backed state if needed.

Expected GUI proof:

- Today/Calendar/Repair/Memory survive restart.
- Weekly review is grounded in visible state and artifacts.
- Missing or unproven items are named.

Runtime proof:

- pre/post restart snapshots
- `life-management-check`
- `orchestration-status`
- `working-docs`
- final `report`
- DB/file cross-check of disputed claims
