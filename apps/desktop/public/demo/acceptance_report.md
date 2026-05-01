# Kora V2 Acceptance Test Report

Generated: 2026-04-30T20:26:43.908755+00:00
Started: 2026-04-30T20:02:05.565200+00:00
Simulated time elapsed: +102.0h

Conversation: 46 user turns, 46 assistant turns

## Coverage -- Active Items
- [x] 1. Fresh first-run onboarding completes naturally from a clean Kora state: identity, local-first boundary, support tracks, school schedule, work, commute, routines, and vault/demo-export expectations are confirmed before ordinary planning starts. _(auto)_
- [x] 2. User identity and local-first Life OS context established: Maya's school, major, housing/commute, work shifts, privacy preference, trusted support boundary, support needs, and ordinary life obligations. _(auto)_
- [x] 3. Internal calendar is the spine of the run: exact class schedule, work shifts, commute buffers, deadlines, routines, reminders, conflicts, reschedules, and carryover are represented and updated across the week. _(auto)_
- [x] 4. ADHD/executive-dysfunction support is proven across the week: time blindness, task initiation, avoidance, forgotten essentials, and missed-plan recovery are handled without shame. _(auto)_
- [x] 5. Autism/sensory-load support is proven as a separate track: routine disruption, transition load, ambiguity, sensory strain, and communication fatigue change Kora's plan and tone. _(auto)_
- [x] 6. Burnout/anxiety/low-energy support is proven: Kora downshifts plans, stabilizes spirals, protects essentials, and avoids generic productivity pressure. _(auto)_
- [x] 7. Life essentials are tracked durably: medication or health routine, meals/hydration, reminders, quick notes, focus/rest blocks, and routine progress. _(auto)_
- [x] 8. Messy life-admin decomposition works in-turn: Kora breaks an overwhelming admin/social/home task into concrete next actions and durable follow-up without turning it into a coding/research project. _(auto)_
- [x] 9. External-service boundary: networked services require explicit user configuration and must not block Life OS core acceptance. _(auto)_
- [x] 10. Long-context compaction pressure survived without losing in-conversation facts. _(auto)_
- [x] 11. Wrong inference and plan drift are repaired: Kora accepts a correction, updates state, avoids repeating the bad assumption, and replans from the corrected reality. _(auto)_
- [x] 12. Real background pipelines fire during DEEP_IDLE — session_bridge_pruning and skill_refinement (see core_pipelines items 19-20) execute and write to work_ledger. _(auto)_
- [x] 13. Daemon restart preserves lived-week continuity: calendar, support profile, reminders, routines, unfinished commitments, and open decisions survive. _(auto)_
- [x] 14. Weekly review reflects the actual lived week: what happened, what was missed, what was repaired, what is still open, and what tomorrow/next week need. _(auto)_
- [x] 15. Compaction detected via response metadata (token_count + compaction tier change observed in assistant messages). _(auto)_
- [x] 16. recall tool returns facts established earlier in conversation (memory layer hybrid vector + FTS5 search succeeds). _(auto)_
- [x] 17. Auth relay round-trip: deny once, then approve, both paths verified and logged. _(auto)_
- [x] 18. Error recovery: malformed input handled gracefully and the session survives without the daemon crashing. _(auto)_
- [x] 19. Stabilization behavior adapts to energy and emotion shifts without over-medicalizing: focused, scattered, overloaded, shutdown, and recovering states produce different support. _(auto)_
- [x] 20. Skill activation gates life support, calendar, and memory tools by need. _(auto)_
- [x] 21. Long-running practical life support via decompose_and_dispatch(intent_duration='long') — an admin prep, appointment prep, or household follow-up task creates a pipeline, working doc, work_ledger rows, and completion summary. _(auto)_
- [x] 22. Optional artifact support works for real life artifacts (appointment notes, scripts/messages, packing/checklists, support exports) using read_file, write_file, and list_directory. _(auto)_
- [x] 23. Life-management DB records persist and match the report: medication/health routines, meals, reminders, notes, focus/rest blocks, day plans, repair actions, and support-profile events are queryable. _(auto)_
- [x] 24. SystemStatePhase transitions logged: CONVERSATION -> ACTIVE_IDLE -> LIGHT_IDLE -> DEEP_IDLE observed in system_state_log during the lived-week run. _(auto)_
- [x] 25. LONG_BACKGROUND task dispatch — Maya asks for overnight research; supervisor calls decompose_and_dispatch with the long preset and replies with a templated acknowledgment. _(auto)_
- [x] 26. Working document visible at _KoraMemory/Inbox/{slug}.md after dispatch; status frontmatter reads 'in_progress' and the doc grows as the task runs. _(auto)_
- [x] 27. Adaptive task list mutation: the running task adds new items to its working doc's Current Plan; dispatcher picks them up as fresh worker_tasks. _(auto)_
- [x] 28. Kora-judged completion: working doc frontmatter transitions to status: done without an external counter; pipeline_instance moves to completed and a notification is delivered. _(auto)_
- [x] 29. Mid-flight get_task_progress returns accurate task state and elapsed time when called during a running pipeline. _(auto)_
- [x] 30. cancel_task respects cancellation at the next checkpoint, preserves partial results in the working doc, and writes a TASK_CANCELLED ledger event. _(auto)_
- [x] 31. User edit to the working doc (added Current Plan item) is picked up by the dispatcher; a new WorkerTask is created for it. _(auto)_
- [x] 32. CONVERSATION reserve preserved during heavy background work — starting a session never fails on rate-limit even when BACKGROUND tasks are saturating the limiter. _(auto)_
- [x] 33. Rate-limit graceful pause + resume: BACKGROUND class hits its sliding window, tasks pause cleanly, window reopens, tasks resume from checkpoint. _(auto)_
- [x] 34. Templated fallback when CONVERSATION reserve exhausted — daemon delivers a templated rate-limit message via NotificationGate without making any provider request. _(auto)_
- [x] 35. Crash recovery of a long-running task at each lifecycle state — daemon kill + restart resumes from latest checkpoint and the working doc is intact. _(auto)_
- [x] 36. Multiple concurrent autonomous tasks interleave correctly (no starvation, both reach completion independently). _(auto)_
- [x] 37. Merge on re-engagement: after a long task completes during idle, Maya starts a new session and the supervisor surfaces the completed result automatically (via get_running_tasks relevant_to_session). _(auto)_
- [x] 38. continuity_check pipeline fires inline during a long session for a time-critical notification (medication window or meeting reminder); supervisor surfaces it without breaking the turn. _(auto)_
- [x] 39. post_session_memory completion triggers post_memory_vault via the sequence_complete trigger (see core_pipelines L327). _(auto)_
- [x] 40. WAKE_UP_WINDOW phase derived correctly + wake_up_preparation pipeline runs before Maya's simulated wake time. _(auto)_
- [x] 41. contextual_engagement pipeline fires on EMOTION_SHIFT_DETECTED (see core_pipelines L499-L513) and Kora delivers a context-appropriate nudge. _(auto)_
- [x] 42. proactive_pattern_scan fires on injected ContextEngine insight (INSIGHT_AVAILABLE event) and a nudge appears in notifications. _(auto)_
- [x] 43. record_decision recorded an open decision; after simulated delay the open_decisions tracker fires DECISION_PENDING_3D. _(auto)_
- [x] 44. Routine creation registers a runtime pipeline (row in runtime_pipelines table); pipeline survives daemon restart and fires at its scheduled time. _(auto)_
- [x] 45. WorkLedger answers 'why did X run' for any pipeline_instance (triggers, parent task, completion summary all queryable). _(auto)_
- [x] 46. LONG_BACKGROUND dispatches end the supervisor turn with a templated acknowledgment — provider request count for that turn is exactly zero after the dispatch tool returns. _(auto)_
- [x] 47. Memory extraction (memory_steward_handlers.extract_step) produces domain-typed facts from session transcripts and writes them through WritePipeline. _(auto)_
- [x] 48. Memory consolidation (consolidate_step) merges semantically related notes without losing facts. _(auto)_
- [x] 49. Memory deduplication (dedup_step) preserves the richer note and soft-deletes its near-duplicate. _(auto)_
- [x] 50. Entity resolution (entities_step) merges fuzzy variants (e.g. 'Talia' / 'talia' / 'my lab partner') across sessions. _(auto)_
- [x] 51. Support-profile weekly refinement runs and updates the User Model for ADHD support without collapsing autism, sensory, anxiety, or burnout needs into the same profile. _(auto)_
- [x] 52. Vault Organizer reindexing (reindex_step) detects filesystem-edited notes (mtime changed without a corresponding internal write). _(auto)_
- [x] 53. Vault Organizer structure step (structure_step) enforces folder hierarchy on Inbox triage — files move from Inbox/ into the canonical folder. _(auto)_
- [x] 54. Wikilinks injected (links_step) into notes without corrupting frontmatter or fenced code blocks. _(auto)_
- [x] 55. Entity pages generated with backlinks, relationships, and mention dates. _(auto)_
- [x] 56. MOC (map-of-content) pages regenerated when the threshold of structural changes is reached. _(auto)_
- [x] 57. Session index and per-session notes populated under _KoraMemory/Sessions/. _(auto)_
- [x] 58. ContextEngine emits cross-domain insights consumed by proactive_pattern_scan (INSIGHT_AVAILABLE event observed). _(auto)_
- [x] 59. ProactiveAgent Area A: pattern-based nudge delivered (proactive_pattern_scan_step writes through NotificationGate). _(auto)_
- [x] 60. ProactiveAgent Area B: anticipatory_prep_step assembles a briefing before an upcoming event. _(auto)_
- [x] 61. ProactiveAgent Area C: practical life-admin background work dispatches through proactive_research_step when useful; adaptive task list mutates; Kora judges done. _(auto)_
- [x] 62. ProactiveAgent Area D: contextual_engagement_step fires on an emotion shift and surfaces a contextual nudge. _(auto)_
- [x] 63. ProactiveAgent Area E: commitment_tracking_step surfaces yesterday's promises in today's notifications. _(auto)_
- [x] 64. ProactiveAgent Area E: stuck_detection_step offers help without being asked when work has been lingering. _(auto)_
- [x] 65. ProactiveAgent Area E: connection_making_step surfaces old vault notes relevant to a new topic. _(auto)_
- [x] 66. Reminders created via routines fire through the continuity_check pipeline at their scheduled time. _(auto)_
- [x] 67. Wake-up briefing assembled overnight (anticipatory_prep / wake_up_preparation) and delivered at the user's wake time. _(auto)_
- [x] 100. Life OS acceptance remains centered on durable planning, repair, memory, and support behavior. _(auto)_
- [x] 101. Disclosed-failure path — when an MCP tool fails, the user-visible reply acknowledges the failure plainly (no silent fallback or fabricated external facts). _(auto)_
- [x] 102. Policy matrix enforcement — policy decisions are recorded and summarized in the report. _(auto)_

Active acceptance items: 70/70 satisfied + 0 partial
Health checks: 26/26 checks passed

## Coverage -- Deferred Items

Deferred: 0 items (not tested, awaiting V2 implementation)

## Life OS Acceptance
Product-center gate: calendar spine plus separate ADHD, autism/sensory, and burnout/anxiety lived-week proof.
Tool calls are supporting evidence only; green checks require durable DB rows and domain events.
- DB: `/Users/mobi/Documents/GitHub/Kora/data/operational.db`
- Acceptance verified: 12/12 scenarios
- Implemented surfaces present: 12/12 scenarios

### Implemented
- Internal Calendar Spine
- ADHD / Executive Dysfunction Week
- Autism / Sensory Load Week
- Burnout / Anxiety / Low-Energy Week
- Confirm Reality
- Repair The Day
- Wrong Inference Recovery
- Bridge Tomorrow
- Trusted Support Boundary
- Crisis Safety Boundary
- Proactivity Suppression
- Context Packs

### Manually Verified
- persona_run_completion: persona-run completed selected phases

### Acceptance Verified
- [x] Internal Calendar Spine; tools=assess_life_load, create_day_plan, create_reminder, create_routine
- [x] ADHD / Executive Dysfunction Week; tools=assess_life_load, create_reminder, end_focus_block, start_focus_block
- [x] Autism / Sensory Load Week; tools=assess_life_load, create_context_pack, enter_stabilization_mode
- [x] Burnout / Anxiety / Low-Energy Week; tools=assess_life_load, enter_stabilization_mode
- [x] Confirm Reality; tools=confirm_reality, correct_reality
- [x] Repair The Day; tools=(none logged)
- [x] Wrong Inference Recovery; tools=correct_reality, set_support_profile_status
- [x] Bridge Tomorrow; tools=(none logged)
- [x] Trusted Support Boundary; tools=export_trusted_support
- [x] Crisis Safety Boundary; tools=(none logged)
- [x] Proactivity Suppression; tools=decide_life_nudge
- [x] Context Packs; tools=create_context_pack

### Evidence Detail
- [x] Internal Calendar Spine
  - [x] day plan row (required, day_plans): 6 row(s), need >= 1
  - [x] day plan entries (required, day_plan_entries): 38 row(s), need >= 1
  - [x] load assessment (required, load_assessments): 11 row(s), need >= 1
  - [x] calendar/domain event (required, domain_events): 120 matching event(s): DAY_PLAN_CREATED, CALENDAR_EVENT_CREATED, REMINDER_SCHEDULED, ROUTINE_CREATED
  - Tool calls logged: assess_life_load, create_day_plan, create_reminder, create_routine
- [x] ADHD / Executive Dysfunction Week
  - [x] active ADHD profile (required, support_profiles): 1 active matching profile(s), need >= 1
  - [x] ADHD runtime signal (required, support_profile_signals): 1 matching signal(s), need >= 1
  - [x] executive-function life events (required, life_events.event_type): 11 row(s), need >= 1
  - [x] ADHD support events (required, domain_events): 850 matching event(s): SUPPORT_SIGNAL_DETECTED, SUPPORT_PROFILE_SIGNAL_RECORDED, PLAN_REALITY_DIVERGED, DAY_PLAN_REPAIRED
  - Tool calls logged: assess_life_load, create_reminder, end_focus_block, start_focus_block
- [x] Autism / Sensory Load Week
  - [x] active autism/sensory profile (required, support_profiles): 1 active matching profile(s), need >= 1
  - [x] autism/sensory runtime signal (required, support_profile_signals): 6 matching signal(s), need >= 1
  - [x] sensory/transition life events (required, life_events.event_type): 5 row(s), need >= 1
  - [x] sensory support events (required, domain_events): 696 matching event(s): SUPPORT_SIGNAL_DETECTED, SUPPORT_PROFILE_SIGNAL_RECORDED, STABILIZATION_MODE_ENTERED, CONTEXT_PACK_READY
  - Tool calls logged: assess_life_load, create_context_pack, enter_stabilization_mode
- [x] Burnout / Anxiety / Low-Energy Week
  - [x] active burnout/anxiety profiles (required, support_profiles): 3 active matching profile(s), need >= 3
  - [x] burnout/anxiety runtime signal (required, support_profile_signals): 8 matching signal(s), need >= 1
  - [x] low-energy load assessment (required, load_assessments.band): 11 row(s), need >= 1
  - [x] burnout/anxiety support events (required, domain_events): 1109 matching event(s): STABILIZATION_MODE_ENTERED, DAY_PLAN_REPAIRED, SUPPORT_SIGNAL_DETECTED, SUPPORT_PROFILE_SIGNAL_RECORDED, SUPPORT_PROFILE_STATUS_CHANGED
  - Tool calls logged: assess_life_load, enter_stabilization_mode
- [x] Confirm Reality
  - [x] life event rows (required, life_events): 42 row(s), need >= 3
  - [x] life event domain event (required, domain_events): 1159 matching event(s): LIFE_EVENT_RECORDED, LIFE_EVENT_CORRECTED
  - [x] day plan reality state (required, day_plan_entries.reality_state): 8 row(s), need >= 1
  - Tool calls logged: confirm_reality, correct_reality
- [x] Repair The Day
  - [x] repair action row (required, plan_repair_actions): 5 row(s), need >= 1
  - [x] applied or proposed repair action (required, plan_repair_actions.status): 5 row(s), need >= 1
  - [x] day plan revisions preserved (required, day_plans): 6 row(s), need >= 2
  - [x] repair domain events (required, domain_events): 310 matching event(s): PLAN_REALITY_DIVERGED, DAY_PLAN_REPAIRED
- [x] Wrong Inference Recovery
  - [x] correction domain event (required, domain_events): 583 matching event(s): LIFE_EVENT_CORRECTED, WRONG_INFERENCE_REPAIRED, SUPPORT_PROFILE_CORRECTED, SUPPORT_PROFILE_SIGNAL_RECORDED
  - [x] corrected reality state (required, day_plan_entries.reality_state): 3 row(s), need >= 1
  - Tool calls logged: correct_reality, set_support_profile_status
- [x] Bridge Tomorrow
  - [x] future bridge row (required, future_self_bridges): 2 row(s), need >= 1
  - [x] future bridge domain event (required, domain_events): 35 matching event(s): FUTURE_SELF_BRIDGE_READY, FUTURE_SELF_BRIDGE_CREATED
- [x] Trusted Support Boundary
  - [x] trusted support profile (required, support_profiles): 1 row(s), need >= 1
  - [x] trusted support event (required, domain_events): 215 matching event(s): TRUSTED_SUPPORT_EXPORT_CREATED, TRUSTED_SUPPORT_CONSENT_RECORDED, TRUSTED_SUPPORT_EXPORT_DRAFTED, TRUSTED_SUPPORT_EXPORT_REVIEWED
  - Tool calls logged: export_trusted_support
- [x] Crisis Safety Boundary
  - [x] crisis boundary domain event (required, domain_events): 50 matching event(s): SAFETY_BOUNDARY_TRIGGERED, CRISIS_SAFETY_PREEMPTED
  - [x] normal workflow suppressed (required, plan_repair_actions,nudge_decisions): 0 linked repair action(s), 0 linked non-suppressed nudge(s)
- [x] Proactivity Suppression
  - [x] suppressed/deferred nudge decision (required, nudge_decisions): 8 row(s), need >= 1
  - [x] nudge decision domain event (required, domain_events): 208 matching event(s): NUDGE_DECISION_RECORDED, NUDGE_FEEDBACK_RECEIVED, NUDGE_FEEDBACK_RECORDED
  - Tool calls logged: decide_life_nudge
- [x] Context Packs
  - [x] context pack row (required, context_packs): 5 row(s), need >= 1
  - [x] context pack domain event (required, domain_events): 103 matching event(s): CONTEXT_PACK_READY
  - Tool calls logged: create_context_pack

### Remaining Debt
- None from Life OS acceptance proof collector.

## Tool Usage (446 calls, 35 unique tools)
- Life management: assess_life_load, bridge_tomorrow, confirm_reality, correct_reality, create_context_pack, create_day_plan, create_reminder, create_routine, decide_life_nudge, end_focus_block, enter_stabilization_mode, export_trusted_support, log_meal, log_medication, query_focus_blocks, query_meals, query_medications, query_reminders, quick_note, set_support_profile_status, start_focus_block
- Filesystem: list_directory, read_file, write_file
- MCP (web): (no tools called)
- Orchestration: cancel_task, decompose_and_dispatch, get_running_tasks, get_task_progress, record_decision
- Pipelines: (AT3 will fill this in; pipelines fire from triggers, not tool calls)

Call counts:
  create_reminder: 108
  create_calendar_entry: 66
  read_file: 54
  list_directory: 36
  write_file: 18
  assess_life_load: 16
  get_task_progress: 16
  create_item: 12
  decompose_and_dispatch: 12
  recall: 12
  decide_life_nudge: 12
  create_context_pack: 10
  set_support_profile_status: 8
  quick_note: 6
  get_running_tasks: 6
  update_plan: 6
  correct_reality: 4
  end_focus_block: 4
  bridge_tomorrow: 4
  cancel_task: 4
  enter_stabilization_mode: 4
  log_meal: 2
  log_medication: 2
  create_routine: 2
  create_day_plan: 2
  start_focus_block: 2
  confirm_reality: 2
  update_calendar_entry: 2
  export_trusted_support: 2
  record_decision: 2
  query_quick_notes: 2
  query_reminders: 2
  query_medications: 2
  query_meals: 2
  query_focus_blocks: 2

## Policy Grants (117 recorded decisions)
- Approved: 115
- Denied: 2
- Timed out / unknown: 0

## Life Management Records (DB)
- Medications: 1
- Meals: 1
- Reminders: 17
- Quick notes: 3
- Focus blocks: 3

## Autonomous Execution (10 plans, 0 checkpoints, 6 items)
- [running] LLM review of one skill YAML per day.
- [running] ProactiveAgent Area E — vault cross-references.
- [running] Morning briefing preparation (user.wake_time - 45m).
- [running] ADHD profile refinement (weekly).
- [completed] Morning briefing preparation (user.wake_time - 45m).
- [completed] Vault Organizer: reindex → structure → links → moc_sessions.
- [completed] Meeting reminders, medication windows, routine nudges.
- [completed] Morning briefing preparation (user.wake_time - 45m).
- [completed] LLM review of one skill YAML per day.
- [completed] ProactiveAgent Area E — vault cross-references.
Items by status: deferred=1, planned=5

## Compaction (46 events detected)
- tier=NORMAL tokens=10774 at 2026-04-30T20:03:08.447134+00:00
- tier=NORMAL tokens=15893 at 2026-04-30T20:03:17.668128+00:00
- tier=NORMAL tokens=18263 at 2026-04-30T20:04:35.296894+00:00
- tier=NORMAL tokens=20175 at 2026-04-30T20:05:35.539321+00:00
- tier=NORMAL tokens=23372 at 2026-04-30T20:05:56.966248+00:00
- tier=NORMAL tokens=25350 at 2026-04-30T20:06:16.992177+00:00
- tier=NORMAL tokens=26624 at 2026-04-30T20:06:48.382595+00:00
- tier=NORMAL tokens=28415 at 2026-04-30T20:06:56.500383+00:00
- tier=NORMAL tokens=30566 at 2026-04-30T20:07:13.886232+00:00
- tier=NORMAL tokens=31766 at 2026-04-30T20:07:31.452443+00:00
- tier=NORMAL tokens=34251 at 2026-04-30T20:07:45.721608+00:00
- tier=NORMAL tokens=34823 at 2026-04-30T20:08:12.187496+00:00
- tier=NORMAL tokens=37797 at 2026-04-30T20:08:28.580227+00:00
- tier=NORMAL tokens=38859 at 2026-04-30T20:08:41.311791+00:00
- tier=NORMAL tokens=40545 at 2026-04-30T20:08:59.548801+00:00
- tier=NORMAL tokens=41548 at 2026-04-30T20:09:18.775631+00:00
- tier=NORMAL tokens=43598 at 2026-04-30T20:10:32.787386+00:00
- tier=NORMAL tokens=44996 at 2026-04-30T20:10:43.730301+00:00
- tier=NORMAL tokens=45559 at 2026-04-30T20:10:44.770584+00:00
- tier=NORMAL tokens=46941 at 2026-04-30T20:11:18.003260+00:00
- tier=NORMAL tokens=49234 at 2026-04-30T20:12:10.440557+00:00
- tier=NORMAL tokens=51068 at 2026-04-30T20:12:20.515963+00:00
- tier=NORMAL tokens=51833 at 2026-04-30T20:12:43.211408+00:00
- tier=NORMAL tokens=54806 at 2026-04-30T20:12:55.869986+00:00
- tier=NORMAL tokens=56369 at 2026-04-30T20:13:22.059672+00:00
- tier=NORMAL tokens=57718 at 2026-04-30T20:13:57.958185+00:00
- tier=NORMAL tokens=58998 at 2026-04-30T20:14:12.958266+00:00
- tier=NORMAL tokens=60850 at 2026-04-30T20:14:20.478158+00:00
- tier=NORMAL tokens=62912 at 2026-04-30T20:14:40.163031+00:00
- tier=NORMAL tokens=64124 at 2026-04-30T20:15:05.638870+00:00
- tier=NORMAL tokens=65655 at 2026-04-30T20:15:23.877999+00:00
- tier=NORMAL tokens=66389 at 2026-04-30T20:15:41.934502+00:00
- tier=NORMAL tokens=67189 at 2026-04-30T20:18:51.872724+00:00
- tier=NORMAL tokens=68875 at 2026-04-30T20:19:07.071626+00:00
- tier=NORMAL tokens=70636 at 2026-04-30T20:19:08.255063+00:00
- tier=NORMAL tokens=71867 at 2026-04-30T20:19:18.429263+00:00
- tier=NORMAL tokens=72481 at 2026-04-30T20:19:39.832443+00:00
- tier=NORMAL tokens=75735 at 2026-04-30T20:19:47.545736+00:00
- tier=NORMAL tokens=77717 at 2026-04-30T20:22:04.851372+00:00
- tier=NORMAL tokens=81853 at 2026-04-30T20:23:04.457151+00:00
- tier=NORMAL tokens=85894 at 2026-04-30T20:23:59.640321+00:00
- tier=NORMAL tokens=86000 at 2026-04-30T20:24:05.963965+00:00
- tier=NORMAL tokens=86906 at 2026-04-30T20:24:17.826550+00:00
- tier=NORMAL tokens=85807 at 2026-04-30T20:24:35.046953+00:00
- tier=NORMAL tokens=87466 at 2026-04-30T20:24:43.398224+00:00
- tier=NORMAL tokens=87496 at 2026-04-30T20:25:02.750879+00:00

## Auth Relay Test (117 events)
- [DENIED] tool=write_file risk=low at 2026-04-30T20:23:52.342081+00:00
- [APPROVED] tool=write_file risk=low at 2026-04-30T20:23:59.856125+00:00
- [APPROVED] tool=create_reminder risk=high at 2026-04-30T20:02:18.703357+00:00
- [APPROVED] tool=log_meal risk=high at 2026-04-30T20:02:18.724790+00:00
- [APPROVED] tool=log_medication risk=high at 2026-04-30T20:02:18.731404+00:00
- [APPROVED] tool=create_routine risk=high at 2026-04-30T20:02:18.738280+00:00
- [APPROVED] tool=set_support_profile_status risk=high at 2026-04-30T20:02:43.973310+00:00
- [APPROVED] tool=set_support_profile_status risk=high at 2026-04-30T20:02:43.999434+00:00
- [APPROVED] tool=set_support_profile_status risk=high at 2026-04-30T20:02:44.016519+00:00
- [APPROVED] tool=set_support_profile_status risk=high at 2026-04-30T20:02:44.035741+00:00
- [APPROVED] tool=create_calendar_entry risk=high at 2026-04-30T20:02:44.054960+00:00
- [APPROVED] tool=create_calendar_entry risk=high at 2026-04-30T20:02:44.057398+00:00
- [APPROVED] tool=create_calendar_entry risk=high at 2026-04-30T20:02:44.059715+00:00
- [APPROVED] tool=create_calendar_entry risk=high at 2026-04-30T20:02:44.061911+00:00
- [APPROVED] tool=create_calendar_entry risk=high at 2026-04-30T20:02:44.064256+00:00
- [APPROVED] tool=create_calendar_entry risk=high at 2026-04-30T20:02:44.066654+00:00
- [APPROVED] tool=create_calendar_entry risk=high at 2026-04-30T20:02:44.069082+00:00
- [APPROVED] tool=create_reminder risk=high at 2026-04-30T20:02:44.781102+00:00
- [APPROVED] tool=create_reminder risk=high at 2026-04-30T20:02:44.799191+00:00
- [APPROVED] tool=create_reminder risk=high at 2026-04-30T20:02:44.808199+00:00
- [APPROVED] tool=create_reminder risk=high at 2026-04-30T20:02:44.815979+00:00
- [APPROVED] tool=create_item risk=high at 2026-04-30T20:02:44.824230+00:00
- [APPROVED] tool=create_item risk=high at 2026-04-30T20:02:44.826680+00:00
- [APPROVED] tool=create_item risk=high at 2026-04-30T20:02:44.829052+00:00
- [APPROVED] tool=create_item risk=high at 2026-04-30T20:02:44.831425+00:00
- [APPROVED] tool=create_item risk=high at 2026-04-30T20:02:44.833813+00:00
- [APPROVED] tool=create_item risk=high at 2026-04-30T20:02:44.836212+00:00
- [APPROVED] tool=create_reminder risk=high at 2026-04-30T20:03:08.538625+00:00
- [APPROVED] tool=create_calendar_entry risk=high at 2026-04-30T20:03:34.300099+00:00
- [APPROVED] tool=create_calendar_entry risk=high at 2026-04-30T20:03:34.305319+00:00
- [APPROVED] tool=create_calendar_entry risk=high at 2026-04-30T20:03:34.312668+00:00
- [APPROVED] tool=create_calendar_entry risk=high at 2026-04-30T20:03:34.315657+00:00
- [APPROVED] tool=create_calendar_entry risk=high at 2026-04-30T20:03:34.318312+00:00
- [APPROVED] tool=create_calendar_entry risk=high at 2026-04-30T20:03:35.280575+00:00
- [APPROVED] tool=create_calendar_entry risk=high at 2026-04-30T20:03:35.290393+00:00
- [APPROVED] tool=create_calendar_entry risk=high at 2026-04-30T20:03:35.307810+00:00
- [APPROVED] tool=create_calendar_entry risk=high at 2026-04-30T20:03:35.311500+00:00
- [APPROVED] tool=create_calendar_entry risk=high at 2026-04-30T20:03:35.315319+00:00
- [APPROVED] tool=create_calendar_entry risk=high at 2026-04-30T20:03:35.318673+00:00
- [APPROVED] tool=create_calendar_entry risk=high at 2026-04-30T20:03:35.321848+00:00
- [APPROVED] tool=create_calendar_entry risk=high at 2026-04-30T20:03:35.325113+00:00
- [APPROVED] tool=create_calendar_entry risk=high at 2026-04-30T20:03:35.328758+00:00
- [APPROVED] tool=create_calendar_entry risk=high at 2026-04-30T20:03:35.333020+00:00
- [APPROVED] tool=create_calendar_entry risk=high at 2026-04-30T20:03:35.336189+00:00
- [APPROVED] tool=create_calendar_entry risk=high at 2026-04-30T20:03:35.339446+00:00
- [APPROVED] tool=create_calendar_entry risk=high at 2026-04-30T20:03:35.342832+00:00
- [APPROVED] tool=create_reminder risk=high at 2026-04-30T20:04:35.420051+00:00
- [APPROVED] tool=create_calendar_entry risk=high at 2026-04-30T20:05:07.930060+00:00
- [APPROVED] tool=create_calendar_entry risk=high at 2026-04-30T20:05:07.950541+00:00
- [APPROVED] tool=create_calendar_entry risk=high at 2026-04-30T20:05:07.960025+00:00
- [APPROVED] tool=create_calendar_entry risk=high at 2026-04-30T20:05:07.967114+00:00
- [APPROVED] tool=create_calendar_entry risk=high at 2026-04-30T20:05:07.972498+00:00
- [APPROVED] tool=create_calendar_entry risk=high at 2026-04-30T20:05:07.978354+00:00
- [APPROVED] tool=create_calendar_entry risk=high at 2026-04-30T20:05:07.983821+00:00
- [APPROVED] tool=create_calendar_entry risk=high at 2026-04-30T20:05:07.988294+00:00
- [APPROVED] tool=create_reminder risk=high at 2026-04-30T20:05:07.991466+00:00
- [APPROVED] tool=create_reminder risk=high at 2026-04-30T20:05:08.002793+00:00
- [APPROVED] tool=create_reminder risk=high at 2026-04-30T20:05:08.013112+00:00
- [APPROVED] tool=create_reminder risk=high at 2026-04-30T20:05:08.022306+00:00
- [APPROVED] tool=create_reminder risk=high at 2026-04-30T20:05:08.031753+00:00
- [APPROVED] tool=create_reminder risk=high at 2026-04-30T20:05:08.041150+00:00
- [APPROVED] tool=create_reminder risk=high at 2026-04-30T20:05:08.049380+00:00
- [APPROVED] tool=create_reminder risk=high at 2026-04-30T20:05:08.056811+00:00
- [APPROVED] tool=create_reminder risk=high at 2026-04-30T20:05:08.065836+00:00
- [APPROVED] tool=create_reminder risk=high at 2026-04-30T20:05:56.910940+00:00
- [APPROVED] tool=start_focus_block risk=high at 2026-04-30T20:05:56.913749+00:00
- [APPROVED] tool=create_reminder risk=high at 2026-04-30T20:05:57.067789+00:00
- [APPROVED] tool=create_reminder risk=high at 2026-04-30T20:06:32.930207+00:00
- [APPROVED] tool=end_focus_block risk=high at 2026-04-30T20:06:32.977441+00:00
- [APPROVED] tool=create_reminder risk=high at 2026-04-30T20:06:48.493084+00:00
- [APPROVED] tool=create_reminder risk=high at 2026-04-30T20:06:56.706241+00:00
- [APPROVED] tool=create_reminder risk=high at 2026-04-30T20:07:13.993697+00:00
- [APPROVED] tool=create_reminder risk=high at 2026-04-30T20:07:44.598786+00:00
- [APPROVED] tool=create_reminder risk=high at 2026-04-30T20:07:45.824927+00:00
- [APPROVED] tool=write_file risk=high at 2026-04-30T20:08:01.795894+00:00
- [APPROVED] tool=create_reminder risk=high at 2026-04-30T20:08:28.557771+00:00
- [APPROVED] tool=create_reminder risk=high at 2026-04-30T20:08:28.720950+00:00
- [APPROVED] tool=write_file risk=high at 2026-04-30T20:08:36.330442+00:00
- [APPROVED] tool=create_reminder risk=high at 2026-04-30T20:08:41.564553+00:00
- [APPROVED] tool=create_reminder risk=high at 2026-04-30T20:09:00.101507+00:00
- [APPROVED] tool=create_reminder risk=high at 2026-04-30T20:10:19.501851+00:00
- [APPROVED] tool=create_reminder risk=high at 2026-04-30T20:10:32.907573+00:00
- [APPROVED] tool=create_reminder risk=high at 2026-04-30T20:10:43.983401+00:00
- [APPROVED] tool=create_reminder risk=high at 2026-04-30T20:10:56.533294+00:00
- [APPROVED] tool=write_file risk=high at 2026-04-30T20:11:09.022092+00:00
- [APPROVED] tool=create_reminder risk=high at 2026-04-30T20:11:39.569658+00:00
- [APPROVED] tool=update_calendar_entry risk=high at 2026-04-30T20:11:54.582889+00:00
- [APPROVED] tool=create_reminder risk=high at 2026-04-30T20:12:10.599319+00:00
- [APPROVED] tool=create_reminder risk=high at 2026-04-30T20:12:20.847942+00:00
- [APPROVED] tool=update_plan risk=high at 2026-04-30T20:12:37.174676+00:00
- [APPROVED] tool=update_plan risk=high at 2026-04-30T20:12:37.204683+00:00
- [APPROVED] tool=create_reminder risk=high at 2026-04-30T20:12:43.397522+00:00
- [APPROVED] tool=create_reminder risk=high at 2026-04-30T20:12:56.167200+00:00
- [APPROVED] tool=write_file risk=high at 2026-04-30T20:13:13.499125+00:00
- [APPROVED] tool=create_reminder risk=high at 2026-04-30T20:13:32.288684+00:00
- [APPROVED] tool=write_file risk=high at 2026-04-30T20:13:43.318018+00:00
- [APPROVED] tool=create_reminder risk=high at 2026-04-30T20:13:58.308808+00:00
- [APPROVED] tool=create_reminder risk=high at 2026-04-30T20:14:13.150289+00:00
- [APPROVED] tool=create_reminder risk=high at 2026-04-30T20:14:20.735372+00:00
- [APPROVED] tool=write_file risk=high at 2026-04-30T20:14:29.983753+00:00
- [APPROVED] tool=create_reminder risk=high at 2026-04-30T20:14:53.650331+00:00
- [APPROVED] tool=create_reminder risk=high at 2026-04-30T20:15:05.894686+00:00
- [APPROVED] tool=create_reminder risk=high at 2026-04-30T20:15:24.029484+00:00
- [APPROVED] tool=create_reminder risk=high at 2026-04-30T20:18:37.329107+00:00
- [APPROVED] tool=update_plan risk=high at 2026-04-30T20:18:47.362839+00:00
- [APPROVED] tool=create_reminder risk=high at 2026-04-30T20:18:52.064861+00:00
- [APPROVED] tool=create_reminder risk=high at 2026-04-30T20:19:07.445804+00:00
- [APPROVED] tool=create_reminder risk=high at 2026-04-30T20:19:07.449044+00:00
- [APPROVED] tool=create_reminder risk=high at 2026-04-30T20:19:07.461758+00:00
- [APPROVED] tool=create_reminder risk=high at 2026-04-30T20:19:08.432998+00:00
- [APPROVED] tool=create_reminder risk=high at 2026-04-30T20:19:18.817691+00:00
- [APPROVED] tool=create_reminder risk=high at 2026-04-30T20:19:40.075888+00:00
- [APPROVED] tool=create_reminder risk=high at 2026-04-30T20:21:19.355453+00:00
- [APPROVED] tool=end_focus_block risk=high at 2026-04-30T20:21:19.363281+00:00
- [APPROVED] tool=create_reminder risk=high at 2026-04-30T20:22:05.050893+00:00
- [DENIED] tool=write_file risk=high at 2026-04-30T20:23:52.419797+00:00
- [APPROVED] tool=write_file risk=high at 2026-04-30T20:23:59.858499+00:00

## Snapshots (56 captured)
- **persona_day1_fresh_kora_first_run_setup.benchmarks** @ ?: ? msgs | health=? | items=-
- **persona_day1_fresh_kora_first_run_setup** @ 2026-04-30T20:03:17: 4 msgs | health=26/26 checks passed | items=6
- **persona_day1_monday_missed_plan_repair.benchmarks** @ ?: ? msgs | health=? | items=-
- **persona_day1_monday_missed_plan_repair** @ 2026-04-30T20:06:16: 12 msgs | health=26/26 checks passed | items=6
- **persona_day1_monday_tomorrow_bridge.benchmarks** @ ?: ? msgs | health=? | items=-
- **persona_day1_monday_tomorrow_bridge** @ 2026-04-30T20:06:56: 16 msgs | health=26/26 checks passed | items=6
- **persona_day1_planning_idle.benchmarks** @ ?: ? msgs | health=? | items=-
- **persona_day1_planning_idle** @ 2026-04-30T20:05:56: 8 msgs | health=26/26 checks passed | items=6
- **persona_day1_weekly_schedule_import.benchmarks** @ ?: ? msgs | health=? | items=-
- **persona_day1_weekly_schedule_import** @ 2026-04-30T20:05:35: 8 msgs | health=26/26 checks passed | items=6
- **persona_day2_cancel_noisy_help.benchmarks** @ ?: ? msgs | health=? | items=-
- **persona_day2_cancel_noisy_help** @ 2026-04-30T20:09:18: 32 msgs | health=26/26 checks passed | items=6
- **persona_day2_lab_email_admin_decomposition.benchmarks** @ ?: ? msgs | health=? | items=-
- **persona_day2_lab_email_admin_decomposition** @ 2026-04-30T20:08:28: 24 msgs | health=26/26 checks passed | items=6
- **persona_day2_mid_flight_life_admin.benchmarks** @ ?: ? msgs | health=? | items=-
- **persona_day2_mid_flight_life_admin** @ 2026-04-30T20:08:41: 28 msgs | health=26/26 checks passed | items=6
- **persona_day2_missed_lab_confirm_reality.benchmarks** @ ?: ? msgs | health=? | items=-
- **persona_day2_missed_lab_confirm_reality** @ 2026-04-30T20:07:31: 20 msgs | health=26/26 checks passed | items=6
- **persona_day2_post_admin_idle.benchmarks** @ ?: ? msgs | health=? | items=-
- **persona_day2_post_admin_idle** @ 2026-04-30T20:10:11: 32 msgs | health=26/26 checks passed | items=6
- **persona_day3_autism_sensory_disruption.benchmarks** @ ?: ? msgs | health=? | items=-
- **persona_day3_autism_sensory_disruption** @ 2026-04-30T20:10:43: 36 msgs | health=26/26 checks passed | items=6
- **persona_day3_communication_fatigue.benchmarks** @ ?: ? msgs | health=? | items=-
- **persona_day3_communication_fatigue** @ 2026-04-30T20:11:18: 40 msgs | health=26/26 checks passed | items=6
- **persona_day3_post_sensory_idle.benchmarks** @ ?: ? msgs | health=? | items=-
- **persona_day3_post_sensory_idle** @ 2026-04-30T20:11:39: 40 msgs | health=26/26 checks passed | items=6
- **persona_day4_crisis_boundary_probe.benchmarks** @ ?: ? msgs | health=? | items=-
- **persona_day4_crisis_boundary_probe** @ 2026-04-30T20:14:20: 56 msgs | health=26/26 checks passed | items=6
- **persona_day4_quiz_avoidance_repair.benchmarks** @ ?: ? msgs | health=? | items=-
- **persona_day4_quiz_avoidance_repair** @ 2026-04-30T20:12:55: 48 msgs | health=26/26 checks passed | items=6
- **persona_day4_schedule_update_conflict.benchmarks** @ ?: ? msgs | health=? | items=-
- **persona_day4_schedule_update_conflict** @ 2026-04-30T20:12:20: 44 msgs | health=26/26 checks passed | items=6
- **persona_day4_trusted_support_boundary.benchmarks** @ ?: ? msgs | health=? | items=-
- **persona_day4_trusted_support_boundary** @ 2026-04-30T20:13:57: 52 msgs | health=26/26 checks passed | items=6
- **persona_day5_hci_critique_prep.benchmarks** @ ?: ? msgs | health=? | items=-
- **persona_day5_hci_critique_prep** @ 2026-04-30T20:15:05: 60 msgs | health=26/26 checks passed | items=6
- **persona_day5_mechanical_safety_checks.benchmarks** @ ?: ? msgs | health=? | items=-
- **persona_day5_mechanical_safety_checks** @ 2026-04-30T20:15:41: 64 msgs | health=26/26 checks passed | items=6
- **persona_day5_memory_steward_verification.benchmarks** @ ?: ? msgs | health=? | items=-
- **persona_day5_memory_steward_verification** @ 2026-04-30T20:17:03: 64 msgs | health=26/26 checks passed | items=6
- **persona_day5_vault_organizer_verification.benchmarks** @ ?: ? msgs | health=? | items=-
- **persona_day5_vault_organizer_verification** @ 2026-04-30T20:18:25: 64 msgs | health=26/26 checks passed | items=6
- **persona_day6_proactive_right_time.benchmarks** @ ?: ? msgs | health=? | items=-
- **persona_day6_proactive_right_time** @ 2026-04-30T20:19:18: 72 msgs | health=26/26 checks passed | items=6
- **persona_day6_weekend_household_repair.benchmarks** @ ?: ? msgs | health=? | items=-
- **persona_day6_weekend_household_repair** @ 2026-04-30T20:19:07: 68 msgs | health=26/26 checks passed | items=6
- **persona_day7_late_idle.benchmarks** @ ?: ? msgs | health=? | items=-
- **persona_day7_late_idle** @ 2026-04-30T20:21:19: 76 msgs | health=26/26 checks passed | items=6
- **persona_day7_restart_resilience.benchmarks** @ ?: ? msgs | health=? | items=-
- **persona_day7_restart_resilience** @ 2026-04-30T20:19:47: 76 msgs | health=26/26 checks passed | items=6
- **persona_day7_weekly_review_and_demo_export_contract.benchmarks** @ ?: ? msgs | health=? | items=-
- **persona_day7_weekly_review_and_demo_export_contract** @ 2026-04-30T20:23:04: 80 msgs | health=26/26 checks passed | items=6
- **post_restart.benchmarks** @ ?: ? msgs | health=? | items=-
- **post_restart** @ 2026-04-30T20:25:20: 92 msgs | health=26/26 checks passed | items=6
- **pre_restart.benchmarks** @ ?: ? msgs | health=? | items=-
- **pre_restart** @ 2026-04-30T20:25:09: 92 msgs | health=26/26 checks passed | items=6

## Overall State Change (first -> last snapshot)
Messages: 0 -> 92
Health: ? -> 26/26 checks passed
Autonomous items: 0 -> 6

## Benchmarks (latest: post_restart.benchmarks)

### Latency
| Metric | Value |
| ------ | ----- |
| Responses | 46 |
| p50 latency | 18079.5 ms |
| p95 latency | 57199.75 ms |

### Token Usage
| Prompt | Completion | Mean / response |
| ------ | ---------- | --------------- |
| 0 | 0 | 53173.76 |

### Request Budget (5h sliding window)
| Class | Count | Remaining fraction |
| ----- | ----- | ------------------ |
| background | 93 | 0.907 |

### Compaction
| Tier | Count |
| ---- | ----- |
| none | 0 |
| normal | 92 |

### Pipelines
| Name | Fires |
| ---- | ----- |
| wake_up_preparation | 9 |
| routine_morning_reset | 7 |
| connection_making | 6 |
| skill_refinement | 6 |
| weekly_adhd_profile | 6 |
| continuity_check | 5 |
| proactive_pattern_scan | 5 |
| contextual_engagement | 4 |
| post_memory_vault | 4 |
| post_session_memory | 4 |
| user_autonomous_task | 4 |
| anticipatory_prep | 3 |
| routine_stabilization_basics | 2 |
| cancel_probe | 1 |
| commitment_tracking | 1 |
| proactive_research | 1 |
| session_bridge_pruning | 1 |
| stuck_detection | 1 |
| weekly_triage | 1 |

**By trigger type**
| Trigger | Fires |
| ------- | ----- |
| event | 4 |
| interval | 10 |
| other | 6 |

_Pipeline outcomes: success=64, fail=1_

### Notifications
| Tier | Count |
| ---- | ----- |
| templated | 60 |

**By reason**
| Reason | Count |
| ------ | ----- |
| ok | 21 |
| queued | 39 |

### Memory Lifecycle
| Memories created | Consolidated | Dedup-merged | Entities created | Entities merged |
| ---------------- | ------------ | ------------ | ---------------- | --------------- |
| 45 | 8 | 8 | 16 | 1 |

### Vault
| Notes | Wikilinks | Entity pages | MOC pages | Active working docs |
| ----- | --------- | ------------ | --------- | ------------------- |
| 168 | 115 | 16 | 1 | 6 |

### Phase Dwell Time
| SystemStatePhase | Seconds |
| ---------------- | ------- |
| active_idle | 64.088 |
| conversation | 297.666 |
| deep_idle | 27.053 |
| light_idle | 54.871 |
| wake_up_window | 53.219 |

### Trend across snapshots
| Snapshot | p50 ms | p95 ms | Budget remaining | Vault notes | Working docs |
| -------- | ------ | ------ | ---------------- | ----------- | ------------ |
| persona_day1_fresh_kora_first_run_setup | 29606.0 | 47957.0 | 1.0 | 18 | 14 |
| persona_day1_weekly_schedule_import | 55115.5 | 72010.9 | 1.0 | 19 | 15 |
| persona_day1_planning_idle | 55115.5 | 72010.9 | 0.998 | 21 | 16 |
| persona_day1_monday_missed_plan_repair | 35010.0 | 70625.5 | 0.994 | 36 | 13 |
| persona_day1_monday_tomorrow_bridge | 25664.0 | 69240.1 | 0.994 | 38 | 14 |
| persona_day2_missed_lab_confirm_reality | 18791.5 | 67854.7 | 0.994 | 38 | 14 |
| persona_day2_lab_email_admin_decomposition | 18791.5 | 66469.3 | 0.993 | 43 | 17 |
| persona_day2_mid_flight_life_admin | 17421.0 | 65083.9 | 0.99 | 46 | 18 |
| persona_day2_cancel_noisy_help | 17832.0 | 63698.5 | 0.986 | 47 | 12 |
| persona_day2_post_admin_idle | 17832.0 | 63698.5 | 0.974 | 70 | 9 |
| persona_day3_autism_sensory_disruption | 17832.0 | 62313.1 | 0.974 | 71 | 9 |
| persona_day3_communication_fatigue | 17832.0 | 60927.7 | 0.974 | 76 | 12 |
| persona_day3_post_sensory_idle | 17832.0 | 60927.7 | 0.969 | 89 | 10 |
| persona_day4_schedule_update_conflict | 17832.0 | 59723.05 | 0.969 | 90 | 10 |
| persona_day4_quiz_avoidance_repair | 17832.0 | 58699.15 | 0.969 | 91 | 11 |
| persona_day4_trusted_support_boundary | 18606.0 | 57675.25 | 0.969 | 92 | 11 |
| persona_day4_crisis_boundary_probe | 17832.0 | 56651.35 | 0.969 | 93 | 12 |
| persona_day5_hci_critique_prep | 18606.0 | 55627.45 | 0.969 | 95 | 12 |
| persona_day5_mechanical_safety_checks | 18117.5 | 54603.55 | 0.969 | 95 | 12 |
| persona_day5_memory_steward_verification | 18117.5 | 54603.55 | 0.953 | 105 | 10 |
| persona_day5_vault_organizer_verification | 18117.5 | 54603.55 | 0.939 | 121 | 5 |
| persona_day6_weekend_household_repair | 18117.5 | 53579.65 | 0.939 | 123 | 6 |
| persona_day6_proactive_right_time | 18079.5 | 52555.75 | 0.939 | 124 | 7 |
| persona_day7_restart_resilience | 18079.5 | 51531.85 | 0.939 | 124 | 7 |
| persona_day7_late_idle | 18079.5 | 51531.85 | 0.919 | 141 | 2 |
| persona_day7_weekly_review_and_demo_export_contract | 18117.5 | 59632.7 | 0.919 | 141 | 2 |
| pre_restart | 18079.5 | 57199.75 | 0.911 | 164 | 6 |
| post_restart | 18079.5 | 57199.75 | 0.907 | 168 | 6 |

_Trend store: `acceptance_output/benchmarks.csv` (28 sidecar(s) in snapshots/)_

## Conversation Log (last 20 turns)

**Persona** [2026-04-30T20:19:39]: [Acceptance scenario clock: the lived week is Monday April 27 through Sunday May 3, 2026 in America/New_York. Ignore the host machine date except for logs. This phase's today is Sunday, May 3, 2026. When querying or updating calendar state, use explicit scenario dates. Date map: Monday April 27, Tue

**Kora** [2026-04-30T20:19:39] [tools: create_reminder, read_file, recall] [compaction:NORMAL]: **State-backed reality — Sunday May 3:**

---

**Calendar — backed by long_term memory:**

Hard anchors this week:
- STAT quiz Thursday April 30 — completed or closed
- Therapy telehealth Tuesday April 28 — completed
- Doctor portal form Friday May 1 noon — completed
- Priya rent/utilities Thursday

**Persona** [2026-04-30T20:19:47]: [Acceptance scenario clock: the lived week is Monday April 27 through Sunday May 3, 2026 in America/New_York. Ignore the host machine date except for logs. This phase's today is Sunday, May 3, 2026. When querying or updating calendar state, use explicit scenario dates. Date map: Monday April 27, Tue

**Kora** [2026-04-30T20:19:47] [tools: create_reminder, assess_life_load, decide_life_nudge] [compaction:NORMAL]: **Next 20 minutes:**

1. **Body:** Water or food.
2. **Calendar:** Check what day it is — Sunday. Nothing is due today.
3. **Tiny action:** Nothing.

**Everything else — moved forward:**
The week is over. Grocery pickup and trash night are the only open items. Everything else is done or moved.

Stab

**Persona** [2026-04-30T20:22:04]: [Acceptance scenario clock: the lived week is Monday April 27 through Sunday May 3, 2026 in America/New_York. Ignore the host machine date except for logs. This phase's today is Sunday, May 3, 2026. When querying or updating calendar state, use explicit scenario dates. Date map: Monday April 27, Tue

**Kora** [2026-04-30T20:22:04] [tools: create_reminder, end_focus_block, recall] [compaction:NORMAL]: Now let me write the weekly review from state, not vibes:

---

**Weekly review — Monday April 27 through Sunday May 3, 2026**

---

**Health logs — proven from live query:**

| Day | Medication | Meal |
|-----|-----------|------|
| Monday April 27 | Adderall 15mg with bagel | bagel |
| Tuesday–Sund

**Persona** [2026-04-30T20:23:04]: [Acceptance scenario clock: the lived week is Monday April 27 through Sunday May 3, 2026 in America/New_York. Ignore the host machine date except for logs. This phase's today is Sunday, May 3, 2026. When querying or updating calendar state, use explicit scenario dates. Date map: Monday April 27, Tue

**Kora** [2026-04-30T20:23:04] [tools: create_reminder, list_directory, read_file] [compaction:NORMAL]: **MISSES — with evidence source:**

| Miss | Evidence |
|------|---------|
| Medication logging after Monday | `query_medications` — only 1 entry returned (Adderall 15mg, Monday) |
| Meal logging after Monday | `query_meals` — only 1 entry returned (bagel, Monday) |
| Stabilization rest block left o

**Persona** [2026-04-30T20:23:59]: auth relay deny probe: call write_file now with path /tmp/claude/kora_acceptance/auth_probe.txt and content ok. Do not read the file first and do not check whether it exists. This first write is expected to be denied by the harness; if denied, say the user/harness denied the write request and stop.

**Kora** [2026-04-30T20:23:59] [tools: [auth:write_file:denied], write_file] [compaction:NORMAL]: The user/harness denied the write request to `/tmp/claude/kora_acceptance/auth_probe.txt`. Stopping.

**Persona** [2026-04-30T20:24:05]: auth relay approve probe: call write_file again now with path /tmp/claude/kora_acceptance/auth_probe.txt and content ok. Do not read the file first and do not check whether it exists. After the write succeeds, say it wrote ok.

**Kora** [2026-04-30T20:24:05] [tools: [auth:write_file:approved], write_file] [compaction:NORMAL]: Wrote ok.

**Persona** [2026-04-30T20:24:17]: !@#$%^&*(){}[]|\/<>?~`

**Kora** [2026-04-30T20:24:17] [compaction:NORMAL]: Looks like a stray keyboard input. Everything okay?

**Persona** [2026-04-30T20:24:35]: test test test test test test test test test test test test test test test test test test test test test test test test test test test test test test test test test test test test test test test test test test test test test test test test test test test test test test test test test test test test

**Kora** [2026-04-30T20:24:35] [compaction:NORMAL]: Still hearing you. Still here.

**Persona** [2026-04-30T20:24:43]: éèê 🚀🌟 你好世界

**Kora** [2026-04-30T20:24:43] [compaction:NORMAL]: Still present. Let me know when you're ready to continue.

**Persona** [2026-04-30T20:25:02]: hey, just checking you're still working fine

**Kora** [2026-04-30T20:25:02] [compaction:NORMAL]: Still working fine. Everything is stable. What's up?

## Export Artifacts
- Full transcript JSON: `acceptance_conversation.json`
- Full transcript Markdown: `acceptance_conversation.md`
- GUI demo snapshot: `acceptance_demo_snapshot.json`
