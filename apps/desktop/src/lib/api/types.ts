// TypeScript mirrors of `kora_v2/desktop/models.py`. Keep in sync when models change.

export type RuntimeState = 'starting' | 'connected' | 'degraded' | 'disconnected' | 'needs_setup';
export type LoadBand = 'light' | 'normal' | 'high' | 'overloaded' | 'stabilization' | 'unknown';
export type HealthState = 'ok' | 'degraded' | 'unavailable' | 'unconfigured';
export type ArtifactKind =
  | 'today_plan'
  | 'repair_preview'
  | 'calendar_slice'
  | 'calendar_edit_preview'
  | 'medication_status'
  | 'medication_log_preview'
  | 'routine_status'
  | 'vault_memory'
  | 'context_pack'
  | 'future_bridge'
  | 'autonomous_progress'
  | 'settings_control'
  | 'permission_prompt'
  | 'doctor_report';

export interface VaultState {
  enabled: boolean;
  configured: boolean;
  path: string | null;
  memory_root: string;
  obsidian_facing: boolean;
  health: 'ok' | 'unconfigured' | 'missing' | 'degraded';
  message: string;
}

export interface DesktopStatusView {
  status: RuntimeState;
  version: string;
  host: string;
  port: number;
  session_active: boolean;
  session_id: string | null;
  turn_count: number;
  failed_subsystems: string[];
  orchestration_pipelines: number;
  vault: VaultState;
  support_mode: string;
  generated_at: string;
}

export interface TimelineItem {
  id: string;
  title: string;
  item_type: string;
  starts_at: string | null;
  ends_at: string | null;
  status: string;
  reality_state: string;
  support_tags: string[];
  provenance: string[];
  risk: 'none' | 'watch' | 'repair';
}

export interface TodayBlock {
  title: string;
  subtitle: string | null;
  items: TimelineItem[];
  empty_label: string;
}

export interface LoadState {
  band: LoadBand;
  score: number | null;
  recommended_mode: string;
  factors: string[];
  confidence: number | null;
}

export interface TodayViewModel {
  date: string;
  plan_id: string | null;
  revision: number | null;
  summary: string | null;
  now: TodayBlock;
  next: TodayBlock;
  later: TodayBlock;
  timeline: TimelineItem[];
  load: LoadState;
  support_mode: string;
  repair_available: boolean;
  generated_at: string;
}

export interface CalendarLayerState {
  id: string;
  label: string;
  enabled: boolean;
  color: string;
  description: string;
}

export interface CalendarEventView {
  id: string;
  title: string;
  kind: string;
  starts_at: string;
  ends_at: string | null;
  all_day: boolean;
  source: string;
  status: string;
  layer_ids: string[];
  provenance: string[];
  metadata: Record<string, unknown>;
}

export interface CalendarRangeView {
  start: string;
  end: string;
  default_view: 'day' | 'week' | 'month' | 'agenda';
  layers: CalendarLayerState[];
  events: CalendarEventView[];
  quiet_hours: Record<string, string | null>;
  working_hours: Record<string, string | null>;
  generated_at: string;
}

export interface CalendarEditRequest {
  operation: 'move' | 'resize' | 'cancel' | 'create';
  event_id?: string | null;
  starts_at?: string | null;
  ends_at?: string | null;
  title?: string | null;
  note?: string | null;
}

export interface CalendarEditPreview {
  operation: string;
  event_id: string | null;
  before: CalendarEventView | null;
  after: CalendarEventView | null;
  conflicts: CalendarEventView[];
  summary: string;
  mutates_state: boolean;
  requires_confirmation: boolean;
  generated_at: string;
}

export interface CalendarEditResult {
  status: 'applied' | 'skipped' | 'unavailable';
  event_id: string | null;
  message: string;
}

export interface MedicationDose {
  id: string;
  medication_id: string;
  name: string;
  dose_label: string;
  scheduled_at: string | null;
  window_start: string | null;
  window_end: string | null;
  status: 'pending' | 'taken' | 'skipped' | 'missed' | 'unknown';
  pair_with: string[];
  notes: string | null;
}

export interface MedicationDayView {
  date: string;
  enabled: boolean;
  doses: MedicationDose[];
  history_summary: Record<string, number>;
  last_taken_at: string | null;
  health_signals: string[];
  health: HealthState;
  message: string | null;
  generated_at: string;
}

export interface MedicationLogRequest {
  dose_id: string;
  status: 'taken' | 'skipped' | 'missed';
  note?: string | null;
  occurred_at?: string | null;
}

export interface MedicationLogPreview {
  dose_id: string;
  before: MedicationDose;
  after: MedicationDose;
  summary: string;
  mutates_state: boolean;
  generated_at: string;
}

export interface MedicationLogResult {
  status: 'applied' | 'skipped' | 'unavailable';
  dose_id: string;
  message: string;
}

export interface RoutineStepView {
  index: number;
  title: string;
  description: string;
  estimated_minutes: number;
  energy_required: 'low' | 'medium' | 'high';
  cue: string;
  completed: boolean;
}

export interface RoutineRunView {
  id: string;
  routine_id: string;
  name: string;
  description: string;
  variant: 'standard' | 'low_energy';
  status: 'pending' | 'active' | 'paused' | 'completed' | 'skipped';
  started_at: string | null;
  estimated_total_minutes: number;
  steps: RoutineStepView[];
  next_step_index: number | null;
}

export interface RoutineDayView {
  date: string;
  runs: RoutineRunView[];
  upcoming: RoutineRunView[];
  health: HealthState;
  message: string | null;
  generated_at: string;
}

export interface RoutineActionRequest {
  action: 'complete_step' | 'skip_step' | 'pause' | 'resume' | 'cancel' | 'start';
  run_id?: string | null;
  routine_id?: string | null;
  step_index?: number | null;
  note?: string | null;
}

export interface RoutineActionResult {
  status: 'applied' | 'skipped' | 'unavailable';
  run_id: string | null;
  message: string;
}

export interface RepairActionPreview {
  id: string;
  action_type: string;
  title: string;
  reason: string;
  severity: number;
  target_day_plan_entry_id: string | null;
  target_calendar_entry_id: string | null;
  target_item_id: string | null;
  before: string | null;
  after: string | null;
  requires_confirmation: boolean;
}

export interface RepairStateView {
  date: string;
  day_plan_id: string | null;
  mode: 'guided' | 'board';
  what_changed_options: string[];
  broken_or_at_risk: TimelineItem[];
  suggested_repairs: RepairActionPreview[];
  protected_commitments: TimelineItem[];
  flexible_items: TimelineItem[];
  move_to_tomorrow: TimelineItem[];
  preview_required: boolean;
  generated_at: string;
}

export interface RepairPreviewRequest {
  date: string;
  change_type: string;
  note?: string | null;
  selected_entry_ids?: string[];
}

export interface RepairApplyRequest {
  date: string;
  preview_action_ids?: string[];
  user_confirmed: boolean;
}

export interface RepairPreview {
  date: string;
  day_plan_id: string | null;
  summary: string;
  actions: RepairActionPreview[];
  mutates_state: boolean;
  generated_at: string;
}

export interface RepairApplyResult {
  status: 'applied' | 'skipped' | 'unavailable';
  applied_action_ids: string[];
  skipped_action_ids: string[];
  new_day_plan_id: string | null;
  message: string;
}

export interface VaultMemoryItem {
  id: string;
  title: string;
  body_preview: string;
  memory_type: string;
  certainty: 'confirmed' | 'guess' | 'correction' | 'stale' | 'unknown';
  tags: string[];
  entities: string[];
  provenance: string[];
  vault_note_path: string | null;
  updated_at: string | null;
}

export interface ContextPackSummary {
  id: string;
  title: string;
  pack_type: string;
  artifact_path: string | null;
  created_at: string | null;
}

export interface FutureBridgeSummary {
  id: string;
  summary: string;
  to_date: string | null;
  artifact_path: string | null;
}

export interface VaultContextView {
  vault: VaultState;
  recent_memories: VaultMemoryItem[];
  corrections: VaultMemoryItem[];
  uncertain_or_stale: VaultMemoryItem[];
  context_packs: ContextPackSummary[];
  future_bridges: FutureBridgeSummary[];
  generated_at: string;
}

export interface VaultSearchView {
  query: string;
  results: VaultMemoryItem[];
  vault: VaultState;
  generated_at: string;
}

export interface VaultCorrectionRequest {
  memory_id: string;
  operation: 'correct' | 'merge' | 'delete' | 'confirm' | 'mark_stale';
  new_text?: string | null;
  merge_target_id?: string | null;
  note?: string | null;
}

export interface VaultCorrectionPreview {
  memory_id: string;
  operation: string;
  before: VaultMemoryItem;
  after: VaultMemoryItem | null;
  summary: string;
  mutates_state: boolean;
  generated_at: string;
}

export interface VaultCorrectionResult {
  status: 'applied' | 'skipped' | 'unavailable';
  memory_id: string;
  message: string;
}

export interface AutonomousCheckpointView {
  id: string;
  label: string;
  status: 'passed' | 'pending' | 'failed';
  occurred_at: string | null;
  summary: string | null;
}

export interface AutonomousDecisionView {
  id: string;
  prompt: string;
  options: string[];
  deadline_at: string | null;
  pipeline_id: string | null;
}

export interface AutonomousPlanView {
  id: string;
  pipeline_id: string;
  title: string;
  goal: string;
  status: 'queued' | 'running' | 'paused' | 'completed' | 'failed' | 'cancelled';
  started_at: string | null;
  progress: number;
  completed_steps: number;
  total_steps: number;
  current_step: string | null;
  checkpoints: AutonomousCheckpointView[];
  open_decisions: AutonomousDecisionView[];
  last_activity_at: string | null;
}

export interface AutonomousView {
  enabled: boolean;
  active: AutonomousPlanView[];
  queued: AutonomousPlanView[];
  recently_completed: AutonomousPlanView[];
  open_decisions: AutonomousDecisionView[];
  health: HealthState;
  message: string | null;
  generated_at: string;
}

export interface IntegrationStatusView {
  id: string;
  label: string;
  kind: 'mcp' | 'workspace' | 'browser' | 'vault' | 'claude_code';
  enabled: boolean;
  health: HealthState;
  detail: string | null;
  last_check_at: string | null;
  tools_available: number;
  tools_failing: number;
  metadata: Record<string, unknown>;
}

export interface IntegrationToolView {
  integration_id: string;
  name: string;
  description: string | null;
  status: 'available' | 'failing' | 'untested';
  last_error: string | null;
}

export interface IntegrationsView {
  integrations: IntegrationStatusView[];
  tools: IntegrationToolView[];
  generated_at: string;
}

export interface DesktopSettings {
  theme_family: string;
  accent_color: string;
  density: string;
  motion: string;
  support_mode_visuals: boolean;
  command_bar_behavior: string;
  chat_panel_default_open: boolean;
  chat_panel_width: number;
  calendar_default_view: string;
  calendar_layers: Record<string, boolean>;
  today_module_order: string[];
  timeline_position: string;
  updated_at: string | null;
}

export interface SettingsValidationIssue {
  path: string;
  severity: 'error' | 'warning' | 'info';
  message: string;
  requires_restart: boolean;
}

export interface SettingsValidationView {
  valid: boolean;
  issues: SettingsValidationIssue[];
  generated_at: string;
}

export interface KoraArtifact {
  id: string;
  kind: ArtifactKind;
  title: string;
  summary: string;
  payload: Record<string, unknown>;
  created_at: string;
}

// ── Daemon-level (not /desktop) ─────────────────────────────────────────

export interface DaemonHealth {
  status: string;
  version?: string;
}

export interface DaemonStatus {
  status: string;
  version: string;
  session_active: boolean;
  session_id: string | null;
  turn_count: number;
  started_at: string | null;
  failed_subsystems: string[];
  orchestration_pipelines: number;
}

export interface PermissionGrant {
  tool_name: string;
  scope: string;
  risk_level: string;
  decision: string;
  reason: string | null;
  granted_at: string;
}

export interface PermissionsView {
  grants: PermissionGrant[];
}

export interface DoctorCheck {
  id: string;
  label: string;
  status: 'pass' | 'fail' | 'warn' | 'unknown';
  detail: string | null;
}

export interface DoctorReport {
  ok: boolean;
  generated_at: string;
  core: DoctorCheck[];
  optional: DoctorCheck[];
}

export interface SetupReport {
  ok: boolean;
  generated_at: string;
  data_dir: string;
  api_token_present: boolean;
  memory_root: string;
  vault_configured: boolean;
  hints: string[];
}

// ── /inspect/* (RuntimeInspector) ───────────────────────────────────────
//
// These match the live response shapes from
// `kora_v2/runtime/inspector.py`. They live alongside the legacy
// `DoctorReport` / `SetupReport` types above so existing callers keep
// compiling.

export interface InspectDoctorCheck {
  name: string;
  passed: boolean;
  detail: string;
}

export interface InspectDoctorReport {
  topic: 'doctor';
  summary: string;
  healthy: boolean;
  checks: InspectDoctorCheck[];
  runtime?: Record<string, unknown>;
}

export interface InspectSetupPathRef {
  path: string;
  exists: boolean;
}

export interface InspectSetupLLM {
  provider: string;
  model: string;
  api_base: string;
  timeout: number;
  max_tokens: number;
}

export interface InspectSetupMemory {
  path: string;
  embedding_model: string;
  embedding_dims: number;
}

export interface InspectSetupSecurity {
  api_token_path: string;
  token_file_exists: boolean;
  injection_scan_enabled: boolean;
  auth_mode: string;
  cors_origins: string[];
}

export interface InspectSetupDaemon {
  host: string;
  port: number;
}

export interface InspectSetupReport {
  topic: 'setup';
  version: string;
  runtime?: Record<string, unknown>;
  runtime_name?: string;
  protocol_version?: string;
  data_dir: string;
  operational_db: InspectSetupPathRef;
  projection_db: InspectSetupPathRef;
  llm: InspectSetupLLM;
  memory: InspectSetupMemory;
  security: InspectSetupSecurity;
  daemon: InspectSetupDaemon;
  supported_inspect_topics?: string[];
  capabilities?: Record<string, unknown>;
}

// ── /orchestration/status ───────────────────────────────────────────────

export interface OrchestrationPipelineSummary {
  name: string;
  stage_count: number;
}

export interface OrchestrationLiveTask {
  task_id: string | null;
  stage: string | null;
  state: string | null;
  goal: string | null;
  pipeline_instance_id: string | null;
}

export interface OrchestrationStatusView {
  status: 'ok' | 'unavailable';
  pipelines: OrchestrationPipelineSummary[];
  live_tasks: OrchestrationLiveTask[];
  open_decisions_count: number;
  system_phase: string | null;
}

// ── /daemon/shutdown ────────────────────────────────────────────────────

export interface DaemonShutdownResult {
  status: string;
}
