# GUI Acceptance Evidence Matrix

Use this matrix to decide whether a GUI acceptance claim is passed, partial, failed, skipped, or not proven.

## Verdict Definitions

Passed:

- GUI behavior is visible and usable.
- Runtime evidence proves the underlying state or action.
- Final report claim matches GUI and runtime evidence.

Partial:

- GUI is visible but backend proof is incomplete.
- Backend proof exists but GUI does not expose it well enough to a user.
- Behavior works only after retry or manual refresh and the limitation is documented.

Failed:

- GUI action does not work.
- GUI shows state that contradicts DB/log/report evidence.
- Backend state exists but user-facing flow is missing for a primary GUI requirement.
- Console/network errors break the flow.

Skipped:

- User explicitly scoped the scenario out.
- Required external capability is unavailable and the skip is disclosed.

Not proven:

- Evidence was not collected.
- Claim relies only on chat, screenshots, or coverage tracker text.

## Surface Matrix

| Surface | GUI evidence | Runtime evidence | Common failure |
| --- | --- | --- | --- |
| Global chat | visible from every major screen; can affect current surface | turn log, tool calls, state rows | chat is isolated from Today/Calendar/Repair |
| Today | ordered next actions, essentials, carryover | day plan rows, reminders, meals/meds/routines | plan appears only in prose |
| Calendar | dated commitments, reschedules, conflicts | calendar/life event rows, snapshots | calendar card not persisted |
| Repair | reality confirmation, downshift, repair actions | repair action rows, load/stabilization events | generic advice with no state change |
| Memory/Vault/Context | recalled facts, context packs, provenance | memory files, projection DB, vault snapshot | stale memory or no provenance |
| Notifications | visible nudge, defer/suppress controls | notifications, nudge decisions, NotificationGate | proactivity is just a check-in |
| Settings/support | local-first and support boundaries | support profile rows, open decisions | implied consent or automatic contact |
| Auth/error | permission prompt and recovery state | permission_grants, test-auth, test-error | hidden failure or silent retry |
| Restart | same state after refresh/restart | pre/post snapshots, DB rows | GUI resets while DB survived |

## Conflict Rules

- DB/log/file evidence wins over chat.
- A screenshot wins only for what was visible, not for whether state persisted.
- `coverage.md` is a tracker, not truth.
- The final report is an artifact to audit, not an authority.
- If GUI and backend disagree, mark the item partial or failed and name both evidence surfaces.
