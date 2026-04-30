import type { ApiClient } from '@/lib/api/client';
import type {
  AutonomousView,
  CalendarEditPreview,
  CalendarEditRequest,
  CalendarEditResult,
  CalendarEventView,
  CalendarRangeView,
  ContextPackSummary,
  DaemonShutdownResult,
  DaemonStatus,
  DesktopSettings,
  DesktopStatusView,
  DoctorReport,
  FutureBridgeSummary,
  InspectDoctorReport,
  InspectSetupReport,
  IntegrationsView,
  MedicationDose,
  MedicationDayView,
  MedicationLogPreview,
  MedicationLogRequest,
  MedicationLogResult,
  OrchestrationStatusView,
  PermissionsView,
  RepairActionPreview,
  RepairApplyRequest,
  RepairApplyResult,
  RepairPreview,
  RepairPreviewRequest,
  RepairStateView,
  RoutineActionRequest,
  RoutineActionResult,
  RoutineDayView,
  RoutineRunView,
  TimelineItem,
  TodayViewModel,
  VaultContextView,
  VaultCorrectionPreview,
  VaultCorrectionRequest,
  VaultCorrectionResult,
  VaultMemoryItem,
  VaultSearchView,
} from '@/lib/api/types';
import { DEMO_LABEL, DEMO_SNAPSHOT_PATH } from './mode';

interface SnapshotEvent {
  source_table?: string;
  source_id?: string;
  kind?: string;
  title?: string;
  starts_at?: string | null;
  ends_at?: string | null;
  status?: string | null;
  payload?: Record<string, unknown> | null;
}

interface SnapshotMessage {
  message_index?: number;
  role?: string;
  speaker?: string;
  timestamp?: string;
  content?: string;
}

interface Snapshot {
  demo_meta?: {
    label?: string;
    captured_at?: string;
  };
  persona?: Record<string, unknown>;
  today?: {
    latest_snapshot_captured_at?: string;
    health?: string;
    life_counts?: Record<string, number>;
    active_day_plan?: {
      id?: string;
      plan_date?: string;
      revision?: number;
      summary?: string;
    } | null;
  };
  calendar?: {
    events?: SnapshotEvent[];
  };
  confirm_reality?: {
    entries?: Record<string, unknown>[];
  };
  repair?: {
    actions?: Record<string, unknown>[];
  };
  tomorrow_bridge?: {
    context_packs?: Record<string, unknown>[];
    events?: SnapshotEvent[];
    conversation_mentions?: Record<string, unknown>[];
  };
  memory?: {
    memory_lifecycle?: Record<string, unknown>;
    vault_state?: Record<string, unknown>;
    session_transcripts?: Record<string, unknown>[];
    tool_calls?: Record<string, unknown>[];
  };
  conversation?: {
    message_count?: number;
    messages?: SnapshotMessage[];
  };
  acceptance_proof?: Record<string, unknown>;
}

let snapshotPromise: Promise<Snapshot> | null = null;
let settings: DesktopSettings | null = null;

const DEMO_VISIBLE_CALENDAR_EVENTS: SnapshotEvent[] = [
  {
    source_table: 'calendar_entries',
    source_id: 'demo-mon-cogs',
    kind: 'event',
    title: 'COGS 302 lecture',
    starts_at: '2026-04-27T09:00:00-04:00',
    ends_at: '2026-04-27T10:15:00-04:00',
    status: 'confirmed',
  },
  {
    source_table: 'calendar_entries',
    source_id: 'demo-mon-hci',
    kind: 'event',
    title: 'HCI 210 seminar',
    starts_at: '2026-04-27T11:00:00-04:00',
    ends_at: '2026-04-27T12:15:00-04:00',
    status: 'confirmed',
  },
  {
    source_table: 'calendar_entries',
    source_id: 'demo-tue-bio-lab',
    kind: 'event',
    title: 'BIO 240 lab',
    starts_at: '2026-04-28T08:30:00-04:00',
    ends_at: '2026-04-28T10:20:00-04:00',
    status: 'confirmed',
  },
  {
    source_table: 'calendar_entries',
    source_id: 'demo-tue-therapy',
    kind: 'event',
    title: 'Telehealth therapy',
    starts_at: '2026-04-28T18:00:00-04:00',
    ends_at: '2026-04-28T18:50:00-04:00',
    status: 'confirmed',
  },
  {
    source_table: 'calendar_entries',
    source_id: 'demo-wed-shift',
    kind: 'event',
    title: 'Accessibility resource center shift',
    starts_at: '2026-04-29T13:00:00-04:00',
    ends_at: '2026-04-29T16:00:00-04:00',
    status: 'confirmed',
  },
  {
    source_table: 'calendar_entries',
    source_id: 'demo-thu-stat-window',
    kind: 'deadline',
    title: 'STAT quiz window',
    starts_at: '2026-04-30T08:00:00-04:00',
    ends_at: '2026-04-30T23:59:00-04:00',
    status: 'active',
  },
  {
    source_table: 'calendar_entries',
    source_id: 'demo-thu-hci-feedback',
    kind: 'deadline',
    title: 'HCI prototype peer feedback due',
    starts_at: '2026-04-30T11:59:00-04:00',
    ends_at: null,
    status: 'active',
  },
  {
    source_table: 'calendar_entries',
    source_id: 'demo-thu-stat-lecture',
    kind: 'event',
    title: 'STAT 220 Methods lecture',
    starts_at: '2026-04-30T10:00:00-04:00',
    ends_at: '2026-04-30T11:15:00-04:00',
    status: 'confirmed',
  },
  {
    source_table: 'calendar_entries',
    source_id: 'demo-thu-accessibility-shift',
    kind: 'event',
    title: 'Accessibility resource center shift',
    starts_at: '2026-04-30T15:00:00-04:00',
    ends_at: '2026-04-30T18:00:00-04:00',
    status: 'confirmed',
  },
  {
    source_table: 'calendar_entries',
    source_id: 'demo-thu-priya',
    kind: 'event',
    title: 'Priya rent/utilities check-in',
    starts_at: '2026-04-30T19:00:00-04:00',
    ends_at: '2026-04-30T19:20:00-04:00',
    status: 'confirmed',
  },
  {
    source_table: 'reminders',
    source_id: 'demo-thu-mom',
    kind: 'reminder',
    title: 'Text mom — short check-in only',
    starts_at: '2026-04-30T21:00:00-04:00',
    ends_at: null,
    status: 'pending',
  },
  {
    source_table: 'calendar_entries',
    source_id: 'demo-fri-doctor',
    kind: 'deadline',
    title: 'Doctor portal form',
    starts_at: '2026-05-01T12:00:00-04:00',
    ends_at: null,
    status: 'active',
  },
  {
    source_table: 'calendar_entries',
    source_id: 'demo-fri-cogs-review',
    kind: 'deadline',
    title: 'COGS exam review sheet due',
    starts_at: '2026-05-01T13:00:00-04:00',
    ends_at: null,
    status: 'active',
  },
  {
    source_table: 'calendar_entries',
    source_id: 'demo-sat-grocery-laundry',
    kind: 'event',
    title: 'Groceries + laundry recovery block',
    starts_at: '2026-05-02T11:00:00-04:00',
    ends_at: '2026-05-02T13:00:00-04:00',
    status: 'planned',
  },
];

export function createDemoApiClient(): ApiClient {
  return {
    status: async () => makeStatus(await loadSnapshot()),
    today: async (date) => makeToday(await loadSnapshot(), date),
    calendar: async (start, end, view) => makeCalendar(await loadSnapshot(), start, end, view),
    calendarPreview: async (req) => makeCalendarPreview(await loadSnapshot(), req),
    calendarApply: async (req) => makeCalendarApply(req),
    medication: async (date) => makeMedication(await loadSnapshot(), date),
    medicationPreview: async (req) => makeMedicationPreview(req),
    medicationApply: async (req) => makeMedicationApply(req),
    routines: async (date) => makeRoutines(await loadSnapshot(), date),
    routinesApply: async (req) => makeRoutineApply(req),
    repairState: async (date) => makeRepairState(await loadSnapshot(), date),
    repairPreview: async (req) => makeRepairPreview(await loadSnapshot(), req),
    repairApply: async (req) => makeRepairApply(await loadSnapshot(), req),
    vaultSearch: async (q) => makeVaultSearch(await loadSnapshot(), q),
    vaultContext: async () => makeVaultContext(await loadSnapshot()),
    vaultCorrectionPreview: async (req) => makeVaultCorrectionPreview(await loadSnapshot(), req),
    vaultCorrectionApply: async (req) => makeVaultCorrectionApply(req),
    autonomous: async () => makeAutonomous(await loadSnapshot()),
    integrations: async () => makeIntegrations(await loadSnapshot()),
    getSettings: async () => getDemoSettings(await loadSnapshot()),
    patchSettings: async (patch) => patchDemoSettings(await loadSnapshot(), patch),
    validateSettings: async () => ({
      valid: true,
      issues: [
        {
          path: 'demo',
          severity: 'info',
          message: DEMO_LABEL,
          requires_restart: false,
        },
      ],
      generated_at: nowIso(await loadSnapshot()),
    }),
    health: async () => ({ status: 'ok', version: 'demo' }),
    daemonStatus: async () => makeDaemonStatus(await loadSnapshot()),
    doctor: async () => makeDoctor(await loadSnapshot()),
    setup: async () => makeSetup(await loadSnapshot()),
    permissions: async () => makePermissions(),
    getInspectDoctor: async () => makeInspectDoctor(await loadSnapshot()),
    getInspectSetup: async () => makeInspectSetup(await loadSnapshot()),
    orchestrationStatus: async () => makeOrchestration(await loadSnapshot()),
    shutdownDaemon: async () => makeShutdown(),
    daemonLogsTail: async () => [
      DEMO_LABEL,
      'The browser demo does not connect to the daemon or tail local logs.',
    ],
  };
}

export async function loadSnapshot(): Promise<Snapshot> {
  snapshotPromise ??= fetch(DEMO_SNAPSHOT_PATH, {
    cache: 'force-cache',
    headers: { Accept: 'application/json' },
  }).then(async (res) => {
    if (!res.ok) throw new Error(`Demo snapshot failed to load: ${res.status}`);
    return (await res.json()) as Snapshot;
  });
  return snapshotPromise;
}

function nowIso(snapshot: Snapshot): string {
  return (
    snapshot.today?.latest_snapshot_captured_at ??
    snapshot.demo_meta?.captured_at ??
    new Date().toISOString()
  );
}

function makeStatus(snapshot: Snapshot): DesktopStatusView {
  return {
    status: 'connected',
    version: 'acceptance-demo',
    host: 'static-demo',
    port: 0,
    session_active: false,
    session_id: 'acceptance-snapshot',
    turn_count: snapshot.conversation?.message_count ?? snapshot.conversation?.messages?.length ?? 0,
    failed_subsystems: [],
    orchestration_pipelines: 1,
    vault: makeVaultState(snapshot),
    support_mode: 'demo-read-only',
    generated_at: nowIso(snapshot),
  };
}

function makeDaemonStatus(snapshot: Snapshot): DaemonStatus {
  return {
    status: 'demo',
    version: 'acceptance-demo',
    session_active: false,
    session_id: 'acceptance-snapshot',
    turn_count: snapshot.conversation?.message_count ?? 0,
    started_at: nowIso(snapshot),
    failed_subsystems: [],
    orchestration_pipelines: 1,
  };
}

function makeCalendar(
  snapshot: Snapshot,
  start: string,
  end: string,
  view: 'day' | 'week' | 'month' | 'agenda' = 'week',
): CalendarRangeView {
  const events = allCalendarEvents(snapshot).filter((event) => {
    const startMs = new Date(event.starts_at).getTime();
    const rangeStart = new Date(start).getTime();
    const rangeEnd = new Date(end).getTime();
    return Number.isFinite(startMs) && startMs >= rangeStart && startMs < rangeEnd;
  });
  return {
    start,
    end,
    default_view: view,
    layers: [
      {
        id: 'classes',
        label: 'Classes',
        enabled: true,
        color: '#2563eb',
        description: 'Maya Rivera course, lab, and campus schedule.',
      },
      {
        id: 'life',
        label: 'Life admin',
        enabled: true,
        color: '#0f766e',
        description: 'Errands, reminders, appointments, and housing admin.',
      },
      {
        id: 'repair',
        label: 'Repairs',
        enabled: true,
        color: '#dc2626',
        description: 'Acceptance-week recovery and protected-context changes.',
      },
      {
        id: 'memory',
        label: 'Memory',
        enabled: true,
        color: '#7c3aed',
        description: 'Context packs, bridges, and durable proof artifacts.',
      },
    ],
    events,
    quiet_hours: {
      start: '22:30',
      end: '07:30',
    },
    working_hours: {
      start: '08:00',
      end: '20:30',
    },
    generated_at: nowIso(snapshot),
  };
}

function allCalendarEvents(snapshot: Snapshot): CalendarEventView[] {
  void snapshot;
  return mapSnapshotEvents(DEMO_VISIBLE_CALENDAR_EVENTS);
}

function mapSnapshotEvents(raw: SnapshotEvent[]): CalendarEventView[] {
  const seen = new Set<string>();
  return raw
    .map((event, index) => mapCalendarEvent(event, index))
    .filter((event): event is CalendarEventView => {
      if (!event || seen.has(event.id)) return false;
      seen.add(event.id);
      return true;
    })
    .sort((a, b) => new Date(a.starts_at).getTime() - new Date(b.starts_at).getTime());
}

function mapCalendarEvent(event: SnapshotEvent, index: number): CalendarEventView | null {
  if (!event.starts_at) return null;
  const payload = asRecord(event.payload);
  const metadata = {
    ...payload,
    source_table: event.source_table ?? null,
    source_id: event.source_id ?? null,
    calendar_entry_id: stringValue(payload.id) ?? event.source_id ?? null,
    item_id: stringValue(payload.item_id) ?? null,
    reminder_id: stringValue(payload.reminder_id) ?? null,
    last_sync_at: stringValue(payload.updated_at) ?? stringValue(payload.created_at) ?? null,
  };
  const kind = event.kind ?? stringValue(payload.kind) ?? 'calendar';
  const sourceTable = event.source_table ?? 'acceptance_snapshot';
  const title = event.title ?? stringValue(payload.title) ?? stringValue(payload.name) ?? 'Untitled';
  return {
    id: `${sourceTable}:${event.source_id ?? index}`,
    title,
    kind,
    starts_at: event.starts_at,
    ends_at: event.ends_at ?? null,
    all_day: false,
    source: sourceTable,
    status: event.status ?? stringValue(payload.status) ?? 'active',
    layer_ids: layersForEvent(sourceTable, kind, title),
    provenance: provenanceForEvent(sourceTable, event.status ?? stringValue(payload.status)),
    metadata,
  };
}

function layersForEvent(source: string, kind: string, title: string): string[] {
  const text = `${source} ${kind} ${title}`.toLowerCase();
  const layers = new Set<string>();
  if (text.includes('class') || text.includes('lab') || text.includes('quiz') || text.includes('exam')) {
    layers.add('classes');
  }
  if (text.includes('repair') || text.includes('defer') || text.includes('blocked')) {
    layers.add('repair');
  }
  if (text.includes('memory') || text.includes('context') || text.includes('bridge')) {
    layers.add('memory');
  }
  if (layers.size === 0) layers.add('life');
  return Array.from(layers);
}

function provenanceForEvent(source: string, status: string | null | undefined): string[] {
  const values = ['local'];
  if (source.includes('repair') || status === 'applied') values.push('repair');
  if (status === 'confirmed' || status === 'active' || status === 'completed') values.push('confirmed');
  if (values.length === 1) values.push('inferred');
  return values;
}

function makeToday(snapshot: Snapshot, date: string): TodayViewModel {
  if (date === '2026-04-30') {
    const repairs = demoRepairActions();
    const nowItems = [
      makeTimelineItem(
        'demo-now-stabilize',
        'Stabilization reset after missed groceries/laundry',
        'routine',
        '2026-04-30T16:15:00-04:00',
        '2026-04-30T16:35:00-04:00',
        'active',
        ['repair', 'life'],
        ['local', 'repair', 'confirmed'],
        'repair',
      ),
    ];
    const nextItems = [
      makeTimelineItem(
        'demo-next-stat',
        'Finish STAT quiz while the window is still open',
        'deadline',
        '2026-04-30T17:00:00-04:00',
        '2026-04-30T18:00:00-04:00',
        'watch',
        ['classes'],
        ['local', 'confirmed'],
        'watch',
      ),
      makeTimelineItem(
        'demo-next-priya',
        'Priya rent/utilities check-in',
        'event',
        '2026-04-30T19:00:00-04:00',
        '2026-04-30T19:20:00-04:00',
        'confirmed',
        ['life'],
        ['local', 'confirmed'],
      ),
      makeTimelineItem(
        'demo-next-bridge',
        'Write tomorrow bridge: what moved and why',
        'memory',
        '2026-04-30T21:10:00-04:00',
        '2026-04-30T21:25:00-04:00',
        'planned',
        ['memory'],
        ['local', 'confirmed'],
      ),
    ];
    const laterItems = [
      makeTimelineItem(
        'demo-later-mom',
        'Text mom — short check-in only',
        'reminder',
        '2026-04-30T21:00:00-04:00',
        null,
        'pending',
        ['life'],
        ['local', 'confirmed'],
      ),
      makeTimelineItem(
        'demo-later-sat-recovery',
        'Groceries + laundry moved to Saturday recovery block',
        'repair',
        '2026-05-02T11:00:00-04:00',
        '2026-05-02T13:00:00-04:00',
        'planned',
        ['repair', 'life'],
        ['local', 'repair', 'confirmed'],
        'repair',
      ),
    ];
    const timeline = [...nowItems, ...nextItems, ...laterItems];
    return {
      date,
      plan_id: snapshot.today?.active_day_plan?.id ?? 'acceptance-demo-thu-plan',
      revision: 2,
      summary: 'Thursday is in stabilization mode: protect STAT, HCI, shift, and rent/utilities; move groceries and laundry without losing the thread.',
      now: {
        title: 'Now',
        subtitle: 'A small reset that keeps the rest of the day from cascading.',
        items: nowItems,
        empty_label: 'No current item in this snapshot window.',
      },
      next: {
        title: 'Next',
        subtitle: 'Protected anchors and the smallest useful follow-through.',
        items: nextItems,
        empty_label: 'No next items in this range.',
      },
      later: {
        title: 'Later',
        subtitle: 'Low-pressure bridge notes and moved tasks.',
        items: laterItems,
        empty_label: 'No later items in this range.',
      },
      timeline,
      load: {
        band: 'stabilization',
        score: 72,
        recommended_mode: 'protect anchors, reduce optional load',
        factors: [
          'Groceries and laundry slipped',
          'School and rent commitments stay protected',
          `${repairs.length} repair options ready`,
        ],
        confidence: 0.92,
      },
      support_mode: 'acceptance snapshot',
      repair_available: true,
      generated_at: nowIso(snapshot),
    };
  }

  const dayEvents = allCalendarEvents(snapshot).filter((event) => event.starts_at.slice(0, 10) === date);
  const timeline = dayEvents.map((event) => timelineFromEvent(event));
  const nowItems = timeline.slice(0, 1);
  const nextItems = timeline.slice(1, 5);
  const laterItems = timeline.slice(5);
  const activePlan = snapshot.today?.active_day_plan;
  const classes = dayEvents.filter((event) => event.layer_ids.includes('classes')).length;
  const repairs = (snapshot.repair?.actions ?? []).length;
  return {
    date,
    plan_id: activePlan?.id ?? 'acceptance-snapshot-plan',
    revision: activePlan?.revision ?? 1,
    summary:
      activePlan?.summary ??
      `Acceptance snapshot for Maya Rivera: ${dayEvents.length} scheduled items, ${classes} school anchors, ${repairs} repair actions preserved.`,
    now: {
      title: 'Now',
      subtitle: DEMO_LABEL,
      items: nowItems,
      empty_label: 'No current item in this snapshot window.',
    },
    next: {
      title: 'Next',
      subtitle: 'The next protected and flexible blocks from the lived-week snapshot.',
      items: nextItems,
      empty_label: 'No next items in this range.',
    },
    later: {
      title: 'Later',
      subtitle: 'Remaining planned life, school, repair, and bridge context.',
      items: laterItems,
      empty_label: 'No later items in this range.',
    },
    timeline,
    load: {
      band: repairs > 0 ? 'stabilization' : 'normal',
      score: Math.round(Math.min(100, Math.max(35, (dayEvents.length / 14) * 100))),
      recommended_mode: repairs > 0 ? 'protect anchors, reduce optional load' : 'planned support',
      factors: [
        `${dayEvents.length} calendar items in view`,
        `${snapshot.confirm_reality?.entries?.length ?? 0} reality confirmations`,
        `${repairs} repair actions`,
      ],
      confidence: 0.92,
    },
    support_mode: 'acceptance snapshot',
    repair_available: repairs > 0,
    generated_at: nowIso(snapshot),
  };
}

function makeTimelineItem(
  id: string,
  title: string,
  itemType: string,
  startsAt: string | null,
  endsAt: string | null,
  status: string,
  supportTags: string[],
  provenance: string[],
  risk: TimelineItem['risk'] = 'none',
): TimelineItem {
  return {
    id,
    title,
    item_type: itemType,
    starts_at: startsAt,
    ends_at: endsAt,
    status,
    reality_state: status,
    support_tags: supportTags,
    provenance,
    risk,
  };
}

function timelineFromEvent(event: CalendarEventView): TimelineItem {
  const risk = event.provenance.includes('repair') ? 'repair' : event.status.includes('blocked') ? 'watch' : 'none';
  return {
    id: event.id,
    title: event.title,
    item_type: event.kind,
    starts_at: event.starts_at,
    ends_at: event.ends_at,
    status: event.status,
    reality_state: event.status === 'completed' ? 'confirmed_done' : event.status,
    support_tags: event.layer_ids,
    provenance: event.provenance,
    risk,
  };
}

function makeRepairState(snapshot: Snapshot, date: string): RepairStateView {
  const repairActions = demoRepairActions();
  const broken = [
    makeTimelineItem(
      'demo-broken-grocery-laundry',
      'Groceries + laundry run missed',
      'life_admin',
      '2026-04-30T16:00:00-04:00',
      null,
      'blocked',
      ['life', 'repair'],
      ['local', 'repair', 'confirmed'],
      'repair',
    ),
    makeTimelineItem(
      'demo-broken-stat',
      'STAT quiz window still open',
      'deadline',
      '2026-04-30T17:00:00-04:00',
      '2026-04-30T23:59:00-04:00',
      'watch',
      ['classes'],
      ['local', 'confirmed'],
      'watch',
    ),
  ];
  const protectedItems = [
    makeTimelineItem('demo-protected-hci', 'HCI prototype peer feedback due', 'deadline', '2026-04-30T11:59:00-04:00', null, 'confirmed', ['classes'], ['local', 'confirmed']),
    makeTimelineItem('demo-protected-shift', 'Accessibility resource center shift', 'event', '2026-04-30T15:00:00-04:00', '2026-04-30T18:00:00-04:00', 'confirmed', ['life'], ['local', 'confirmed']),
    makeTimelineItem('demo-protected-priya', 'Priya rent/utilities check-in', 'event', '2026-04-30T19:00:00-04:00', '2026-04-30T19:20:00-04:00', 'confirmed', ['life'], ['local', 'confirmed']),
  ];
  const flexible = [
    makeTimelineItem('demo-flex-mom', 'Text mom — short check-in only', 'reminder', '2026-04-30T21:00:00-04:00', null, 'pending', ['life'], ['local', 'confirmed']),
    makeTimelineItem('demo-flex-room-reset', 'Ten-minute room reset', 'routine', '2026-04-30T21:30:00-04:00', '2026-04-30T21:40:00-04:00', 'optional', ['life'], ['local', 'inferred']),
  ];
  const move = [
    makeTimelineItem('demo-move-grocery', 'Move groceries to Saturday pickup block', 'repair', '2026-05-02T11:00:00-04:00', '2026-05-02T12:00:00-04:00', 'planned', ['repair', 'life'], ['local', 'repair', 'confirmed'], 'repair'),
    makeTimelineItem('demo-move-laundry', 'Move laundry to Saturday after groceries', 'repair', '2026-05-02T12:00:00-04:00', '2026-05-02T13:00:00-04:00', 'planned', ['repair', 'life'], ['local', 'repair', 'confirmed'], 'repair'),
  ];
  return {
    date,
    day_plan_id: snapshot.today?.active_day_plan?.id ?? 'acceptance-snapshot-plan',
    mode: 'board',
    what_changed_options: [
      'class schedule changed',
      'energy dropped',
      'admin task slipped',
      'sensory load spiked',
      'move optional work to tomorrow',
    ],
    broken_or_at_risk: broken,
    suggested_repairs: repairActions,
    protected_commitments: protectedItems,
    flexible_items: flexible,
    move_to_tomorrow: move,
    preview_required: true,
    generated_at: nowIso(snapshot),
  };
}

function demoRepairActions(): RepairActionPreview[] {
  return [
    {
      id: 'demo-repair-move-grocery-laundry',
      action_type: 'defer_nonessential',
      title: 'Move groceries + laundry to Saturday',
      reason: 'They slipped after the shift. Moving them keeps tonight from becoming an all-or-nothing failure.',
      severity: 3,
      target_day_plan_entry_id: null,
      target_calendar_entry_id: 'demo-sat-grocery-laundry',
      target_item_id: 'demo-broken-grocery-laundry',
      before: 'Thursday 4:00 PM, blocked',
      after: 'Saturday 11:00 AM recovery block',
      requires_confirmation: false,
    },
    {
      id: 'demo-repair-stat-quiz-first',
      action_type: 'protect_anchor',
      title: 'Protect the STAT quiz window',
      reason: 'The quiz is still recoverable today and matters more than optional chores.',
      severity: 3,
      target_day_plan_entry_id: null,
      target_calendar_entry_id: 'demo-thu-stat-window',
      target_item_id: 'demo-next-stat',
      before: 'Competing with chores and reset work',
      after: 'One focused quiz block before evening admin',
      requires_confirmation: false,
    },
    {
      id: 'demo-repair-bridge-tomorrow',
      action_type: 'bridge_context',
      title: 'Save a tomorrow bridge',
      reason: 'Tomorrow should start from what actually changed, not from memory reconstruction.',
      severity: 2,
      target_day_plan_entry_id: null,
      target_calendar_entry_id: null,
      target_item_id: 'demo-next-bridge',
      before: 'Broken plan only lives in chat',
      after: 'Moved tasks and reasons preserved for tomorrow',
      requires_confirmation: false,
    },
  ];
}

function makeRepairPreview(snapshot: Snapshot, req: RepairPreviewRequest): RepairPreview {
  const actions = demoRepairActions();
  return {
    date: req.date,
    day_plan_id: snapshot.today?.active_day_plan?.id ?? 'acceptance-snapshot-plan',
    summary: `Read-only demo preview for ${req.change_type}. The snapshot already contains the accepted repair history.`,
    actions,
    mutates_state: false,
    generated_at: nowIso(snapshot),
  };
}

function makeRepairApply(snapshot: Snapshot, req: RepairApplyRequest): RepairApplyResult {
  return {
    status: 'unavailable',
    applied_action_ids: [],
    skipped_action_ids: req.preview_action_ids ?? [],
    new_day_plan_id: snapshot.today?.active_day_plan?.id ?? null,
    message: 'Demo mode is read-only. Acceptance repair actions are shown from the saved snapshot.',
  };
}

function makeMedication(snapshot: Snapshot, date: string): MedicationDayView {
  const doses: MedicationDose[] = date === '2026-04-30'
    ? [
        {
          id: 'demo-med-adderall-am',
          medication_id: 'demo-adderall-xr',
          name: 'Adderall XR',
          dose_label: 'morning dose',
          scheduled_at: '2026-04-30T08:30:00-04:00',
          window_start: '2026-04-30T08:00:00-04:00',
          window_end: '2026-04-30T09:30:00-04:00',
          status: 'taken',
          pair_with: ['water', 'breakfast'],
          notes: 'Logged once in the acceptance demo; no repeated proof rows.',
        },
        {
          id: 'demo-med-evening-support',
          medication_id: 'demo-evening-support',
          name: 'Evening support',
          dose_label: 'optional wind-down check',
          scheduled_at: '2026-04-30T21:30:00-04:00',
          window_start: '2026-04-30T21:00:00-04:00',
          window_end: '2026-04-30T22:00:00-04:00',
          status: 'pending',
          pair_with: ['water', 'quiet mode'],
          notes: 'Demo-only placeholder for a low-stimulation evening check.',
        },
      ]
    : [];
  const taken = doses.filter((dose) => dose.status === 'taken').length;
  const pending = doses.filter((dose) => dose.status === 'pending').length;
  return {
    date,
    enabled: true,
    doses,
    history_summary: {
      pending,
      taken,
      skipped: 0,
    },
    last_taken_at: taken > 0 ? '2026-04-30T08:36:00-04:00' : null,
    health_signals: ['No duplicate medication proof rows', 'Pair doses with water/food cues'],
    health: 'ok',
    message: doses.length ? null : 'No medication events in this demo day.',
    generated_at: nowIso(snapshot),
  };
}

function makeMedicationPreview(req: MedicationLogRequest): MedicationLogPreview {
  const dose = {
    id: req.dose_id,
    medication_id: 'demo-med',
    name: req.dose_id.includes('evening') ? 'Evening support' : 'Adderall XR',
    dose_label: 'demo',
    scheduled_at: null,
    window_start: null,
    window_end: null,
    status: 'pending' as const,
    pair_with: [],
    notes: DEMO_LABEL,
  };
  return {
    dose_id: req.dose_id,
    before: dose,
    after: { ...dose, status: req.status },
    summary: 'Demo mode previews medication changes but does not mutate the snapshot.',
    mutates_state: false,
    generated_at: new Date().toISOString(),
  };
}

function makeMedicationApply(req: MedicationLogRequest): MedicationLogResult {
  return {
    status: 'unavailable',
    dose_id: req.dose_id,
    message: 'Demo mode is read-only.',
  };
}

function makeRoutines(snapshot: Snapshot, date: string): RoutineDayView {
  const completedMorning = makeRoutineRun(
    'demo-routine-morning-reset',
    'Tiny Morning Reset',
    'A short start sequence for food, meds, bag, and first class.',
    'completed',
    '2026-04-30T08:05:00-04:00',
    true,
  );
  const activeReset = makeRoutineRun(
    'demo-routine-stabilization-reset',
    'Stabilization reset',
    'A low-energy recovery sequence after the grocery/laundry plan slipped.',
    'active',
    '2026-04-30T16:15:00-04:00',
    false,
  );
  const eveningShutdown = makeRoutineRun(
    'demo-routine-evening-shutdown',
    'Evening shutdown',
    'Close loops, set tomorrow bridge, and reduce sensory load before bed.',
    'pending',
    '2026-04-30T21:30:00-04:00',
    false,
  );
  const runs = date === '2026-04-30' ? [completedMorning, activeReset] : [];
  const upcoming = date === '2026-04-30' ? [eveningShutdown] : [];
  return {
    date,
    runs,
    upcoming,
    health: 'ok',
    message: runs.length ? null : 'No routine runs in this snapshot day.',
    generated_at: nowIso(snapshot),
  };
}

function makeRoutineRun(
  id: string,
  name: string,
  description: string,
  status: RoutineRunView['status'],
  startedAt: string,
  completed: boolean,
): RoutineRunView {
  const steps = [
    {
      index: 0,
      title: 'Orient',
      description: 'Name the next real anchor and remove one avoidable decision.',
      estimated_minutes: 3,
      energy_required: 'low' as const,
      cue: 'Look at Today, not the full week.',
      completed,
    },
    {
      index: 1,
      title: 'Body basics',
      description: 'Water, food check, medication check, and one sensory adjustment.',
      estimated_minutes: 7,
      energy_required: 'low' as const,
      cue: 'Use the smallest version that works.',
      completed,
    },
    {
      index: 2,
      title: 'Commit one next action',
      description: 'Pick the next protected task and defer the rest explicitly.',
      estimated_minutes: 5,
      energy_required: 'medium' as const,
      cue: 'Do not renegotiate the whole day.',
      completed,
    },
  ];
  return {
    id,
    routine_id: id.replace('demo-routine-', ''),
    name,
    description,
    variant: status === 'active' ? 'low_energy' : 'standard',
    status,
    started_at: startedAt,
    estimated_total_minutes: 15,
    steps,
    next_step_index: completed ? null : 1,
  };
}

function makeRoutineApply(req: RoutineActionRequest): RoutineActionResult {
  return {
    status: 'unavailable',
    run_id: req.run_id ?? null,
    message: 'Demo mode is read-only.',
  };
}

function makeVaultContext(snapshot: Snapshot): VaultContextView {
  const memories = makeMemories(snapshot);
  const contextPacks = makeContextPacks(snapshot);
  const futureBridges = makeFutureBridges(snapshot);
  return {
    vault: makeVaultState(snapshot),
    recent_memories: memories,
    corrections: memories.filter((item) => item.certainty === 'correction'),
    uncertain_or_stale: memories.filter((item) => item.certainty === 'stale' || item.certainty === 'unknown'),
    context_packs: contextPacks,
    future_bridges: futureBridges,
    generated_at: nowIso(snapshot),
  };
}

function makeVaultState(snapshot: Snapshot): VaultContextView['vault'] {
  const vault = snapshot.memory?.vault_state ?? {};
  const root = stringValue(vault.root) ?? '/demo/acceptance-vault';
  return {
    enabled: true,
    configured: true,
    path: root,
    memory_root: root,
    obsidian_facing: true,
    health: 'ok',
    message: DEMO_LABEL,
  };
}

function makeMemories(snapshot: Snapshot): VaultMemoryItem[] {
  const persona = asRecord(snapshot.persona);
  const profile = asRecord(persona.profile);
  const school = asRecord(profile.school);
  const housing = asRecord(profile.housing_commute);
  const supportTracks = asRecord(profile.separate_support_tracks);
  const preferences = [
    ...arrayValue(supportTracks.adhd),
    ...arrayValue(supportTracks.autism_sensory),
  ];
  const profileMemory: VaultMemoryItem = {
    id: 'persona-profile',
    title: stringValue(profile.name) ?? 'Maya Rivera',
    body_preview: truncate(
      [
        `${stringValue(profile.name) ?? 'Maya Rivera'} is a ${stringValue(school.year) ?? 'college'} ${stringValue(school.major) ?? 'student'} at ${stringValue(school.name) ?? 'Three Rivers University'}.`,
        stringValue(housing.housing) ?? null,
        `Support tracks: ${arrayValue(profile.conditions).map(String).join(', ')}.`,
      ]
        .filter(Boolean)
        .join(' '),
      240,
    ),
    memory_type: 'persona_profile',
    certainty: 'confirmed',
    tags: ['persona', 'college', 'first-run'],
    entities: [stringValue(profile.name) ?? 'Maya Rivera'],
    provenance: ['local', 'confirmed'],
    vault_note_path: '/demo/acceptance_report.md',
    updated_at: nowIso(snapshot),
  };
  const supportMemory: VaultMemoryItem = {
    id: 'support-preferences',
    title: 'Support preferences',
    body_preview: truncate(preferences.map(String).join(' / ') || 'ADHD support, sensory load reduction, and anxiety-aware repair are enabled for the demo persona.', 240),
    memory_type: 'support_profile',
    certainty: 'confirmed',
    tags: ['adhd', 'autism', 'support'],
    entities: [stringValue(profile.name) ?? 'Maya Rivera'],
    provenance: ['local', 'confirmed'],
    vault_note_path: '/demo/acceptance_demo_snapshot.json',
    updated_at: nowIso(snapshot),
  };
  const communicationMemory: VaultMemoryItem = {
    id: 'communication-priya',
    title: 'Communication preference: text first',
    body_preview: 'For Priya, Marcus, and family check-ins, draft a short text first. Calls are fallback, not the default starting point.',
    memory_type: 'communication_preference',
    certainty: 'correction',
    tags: ['communication', 'low-friction', 'repair'],
    entities: [stringValue(profile.name) ?? 'Maya Rivera', 'Priya', 'Marcus'],
    provenance: ['local', 'corrected'],
    vault_note_path: '/demo/acceptance_conversation.md',
    updated_at: '2026-04-30T14:20:00-04:00',
  };
  const bridgeMemory: VaultMemoryItem = {
    id: 'bridge-grocery-laundry',
    title: 'Groceries and laundry moved deliberately',
    body_preview: 'The Thursday grocery/laundry block slipped after the shift. It was moved to Saturday so school and rent commitments stay protected.',
    memory_type: 'future_bridge',
    certainty: 'confirmed',
    tags: ['repair', 'tomorrow-bridge', 'life-admin'],
    entities: [stringValue(profile.name) ?? 'Maya Rivera'],
    provenance: ['local', 'repair', 'confirmed'],
    vault_note_path: '/demo/acceptance_report.md',
    updated_at: nowIso(snapshot),
  };
  const guessMemory: VaultMemoryItem = {
    id: 'guess-email-avoidance',
    title: 'Email starts may need a two-line draft',
    body_preview: 'Kora is treating admin email starts as a likely friction point and should offer a tiny draft before suggesting a larger task.',
    memory_type: 'support_guess',
    certainty: 'guess',
    tags: ['adhd', 'task-initiation', 'admin'],
    entities: [stringValue(profile.name) ?? 'Maya Rivera'],
    provenance: ['local', 'inferred'],
    vault_note_path: '/demo/acceptance_report.md',
    updated_at: '2026-04-30T16:30:00-04:00',
  };
  const staleMemory: VaultMemoryItem = {
    id: 'stale-sunday-reset',
    title: 'Sunday reset window needs confirmation',
    body_preview: 'A weekly reset is penciled in for Sunday evening, but the time should be refreshed after Saturday errands settle.',
    memory_type: 'schedule_guess',
    certainty: 'stale',
    tags: ['routine', 'planning', 'needs-confirmation'],
    entities: [stringValue(profile.name) ?? 'Maya Rivera'],
    provenance: ['local', 'inferred'],
    vault_note_path: '/demo/acceptance_demo_snapshot.json',
    updated_at: '2026-04-29T18:00:00-04:00',
  };
  return [profileMemory, supportMemory, bridgeMemory, communicationMemory, guessMemory, staleMemory];
}

function makeContextPacks(snapshot: Snapshot): ContextPackSummary[] {
  return [
    {
      id: 'demo-pack-thursday-repair',
      title: 'Thursday repair context',
      pack_type: 'life_os_repair',
      artifact_path: '/demo/acceptance_report.md',
      created_at: '2026-04-30T21:10:00-04:00',
    },
    {
      id: 'demo-pack-school-admin',
      title: 'School + admin anchors',
      pack_type: 'calendar_context',
      artifact_path: '/demo/acceptance_demo_snapshot.json',
      created_at: nowIso(snapshot),
    },
    {
      id: 'demo-pack-support-profile',
      title: 'ADHD and sensory support profile',
      pack_type: 'support_profile',
      artifact_path: '/demo/acceptance_conversation.md',
      created_at: nowIso(snapshot),
    },
  ];
}

function makeFutureBridges(snapshot: Snapshot): FutureBridgeSummary[] {
  void snapshot;
  return [
    {
      id: 'demo-bridge-friday',
      summary: 'Friday starts with the moved errands already acknowledged, plus the doctor portal and COGS review sheet.',
      to_date: '2026-05-01',
      artifact_path: '/demo/acceptance_report.md',
    },
    {
      id: 'demo-bridge-saturday',
      summary: 'Saturday has a protected recovery block for groceries and laundry, not a vague “catch up” pile.',
      to_date: '2026-05-02',
      artifact_path: '/demo/acceptance_demo_snapshot.json',
    },
  ];
}

function makeVaultSearch(snapshot: Snapshot, query: string): VaultSearchView {
  const q = query.toLowerCase();
  const results = makeMemories(snapshot).filter((item) => {
    const haystack = `${item.title} ${item.body_preview} ${item.tags.join(' ')}`.toLowerCase();
    return haystack.includes(q);
  });
  return {
    query,
    results,
    vault: makeVaultState(snapshot),
    generated_at: nowIso(snapshot),
  };
}

function makeVaultCorrectionPreview(snapshot: Snapshot, req: VaultCorrectionRequest): VaultCorrectionPreview {
  const memory = makeMemories(snapshot).find((item) => item.id === req.memory_id) ?? makeMemories(snapshot)[0];
  return {
    memory_id: req.memory_id,
    operation: req.operation,
    before: memory,
    after: memory ? { ...memory, certainty: req.operation === 'mark_stale' ? 'stale' : 'confirmed' } : null,
    summary: 'Demo mode previews memory edits but does not mutate the acceptance snapshot.',
    mutates_state: false,
    generated_at: nowIso(snapshot),
  };
}

function makeVaultCorrectionApply(req: VaultCorrectionRequest): VaultCorrectionResult {
  return {
    status: 'unavailable',
    memory_id: req.memory_id,
    message: 'Demo mode is read-only.',
  };
}

function makeAutonomous(snapshot: Snapshot): AutonomousView {
  return {
    enabled: true,
    active: [],
    queued: [],
    recently_completed: [
      {
        id: 'acceptance-run',
        pipeline_id: 'sanitized-week',
        title: 'Maya Rivera lived-week acceptance run',
        goal: 'Plan a full college week, confirm reality, repair a broken day, bridge tomorrow, and preserve proof.',
        status: 'completed',
        started_at: snapshot.today?.active_day_plan?.plan_date ?? null,
        progress: 1,
        completed_steps: 5,
        total_steps: 5,
        current_step: 'Complete',
        checkpoints: [
          {
            id: 'first-run',
            label: 'Fresh first-run setup',
            status: 'passed',
            occurred_at: nowIso(snapshot),
            summary: 'Snapshot comes from a clean acceptance run with first-run data preserved.',
          },
          {
            id: 'calendar',
            label: 'Week calendar imported',
            status: 'passed',
            occurred_at: '2026-04-30T14:00:00-04:00',
            summary: 'Classes, shift, deadlines, rent/utilities, and reminders were normalized into a demo-safe week.',
          },
          {
            id: 'repair',
            label: 'Broken day repaired',
            status: 'passed',
            occurred_at: '2026-04-30T16:30:00-04:00',
            summary: 'Groceries and laundry moved without dislodging school or housing commitments.',
          },
          {
            id: 'bridge',
            label: 'Tomorrow bridge saved',
            status: 'passed',
            occurred_at: '2026-04-30T21:10:00-04:00',
            summary: 'Friday and Saturday context was preserved as future-facing memory.',
          },
          {
            id: 'conversation',
            label: 'Agent persona conversation',
            status: 'passed',
            occurred_at: nowIso(snapshot),
            summary: `${snapshot.conversation?.message_count ?? 0} turns preserved.`,
          },
        ],
        open_decisions: [],
        last_activity_at: nowIso(snapshot),
      },
    ],
    open_decisions: [],
    health: 'ok',
    message: DEMO_LABEL,
    generated_at: nowIso(snapshot),
  };
}

function makeIntegrations(snapshot: Snapshot): IntegrationsView {
  const visibleCalendarEvents = allCalendarEvents(snapshot).length;
  return {
    integrations: [
      {
        id: 'acceptance-snapshot',
        label: 'Sanitized acceptance snapshot',
        kind: 'workspace',
        enabled: true,
        health: 'ok',
        detail: DEMO_LABEL,
        last_check_at: nowIso(snapshot),
        tools_available: 0,
        tools_failing: 0,
        metadata: {
          messages: snapshot.conversation?.message_count ?? 0,
          visible_calendar_events: visibleCalendarEvents,
        },
      },
      {
        id: 'demo-vault',
        label: 'Demo memory vault',
        kind: 'vault',
        enabled: true,
        health: 'ok',
        detail: 'Curated memory view backed by sanitized acceptance artifacts.',
        last_check_at: nowIso(snapshot),
        tools_available: 2,
        tools_failing: 0,
        metadata: {
          memories: makeMemories(snapshot).length,
          context_packs: makeContextPacks(snapshot).length,
          future_bridges: makeFutureBridges(snapshot).length,
        },
      },
      {
        id: 'browser-demo',
        label: 'Browser demo shell',
        kind: 'browser',
        enabled: true,
        health: 'ok',
        detail: 'Static read-only UI running from the public demo bundle.',
        last_check_at: nowIso(snapshot),
        tools_available: 1,
        tools_failing: 0,
        metadata: {
          mode: 'read_only',
        },
      },
      {
        id: 'claude-code-demo',
        label: 'Acceptance artifact export',
        kind: 'claude_code',
        enabled: true,
        health: 'ok',
        detail: 'Exports report, transcript, snapshot JSON, and event logs for audit.',
        last_check_at: nowIso(snapshot),
        tools_available: 3,
        tools_failing: 0,
        metadata: {
          artifacts: 6,
        },
      },
    ],
    tools: [
      {
        integration_id: 'acceptance-snapshot',
        name: 'load_snapshot',
        description: 'Read the sanitized acceptance snapshot used by the demo UI.',
        status: 'available',
        last_error: null,
      },
      {
        integration_id: 'acceptance-snapshot',
        name: 'calendar_surface',
        description: 'Serve the curated week calendar without raw proof rows.',
        status: 'available',
        last_error: null,
      },
      {
        integration_id: 'demo-vault',
        name: 'memory_context',
        description: 'Render confirmed, guessed, stale, and corrected memories.',
        status: 'available',
        last_error: null,
      },
      {
        integration_id: 'demo-vault',
        name: 'future_bridge',
        description: 'Surface Friday and Saturday carry-forward context.',
        status: 'available',
        last_error: null,
      },
      {
        integration_id: 'browser-demo',
        name: 'read_only_preview',
        description: 'Run the interactive demo without connecting to a local daemon.',
        status: 'available',
        last_error: null,
      },
    ],
    generated_at: nowIso(snapshot),
  };
}

function getDemoSettings(snapshot: Snapshot): DesktopSettings {
  settings ??= {
    theme_family: 'system',
    accent_color: '#2563eb',
    density: 'comfortable',
    motion: 'reduced',
    support_mode_visuals: true,
    command_bar_behavior: 'enabled',
    chat_panel_default_open: true,
    chat_panel_width: 380,
    calendar_default_view: 'week',
    calendar_layers: {
      classes: true,
      life: true,
      repair: true,
      memory: true,
    },
    today_module_order: ['now', 'next', 'later', 'timeline', 'reality', 'bridge'],
    timeline_position: 'below',
    updated_at: nowIso(snapshot),
  };
  return settings;
}

function patchDemoSettings(snapshot: Snapshot, patch: Partial<DesktopSettings>): DesktopSettings {
  settings = {
    ...getDemoSettings(snapshot),
    ...patch,
    updated_at: nowIso(snapshot),
  };
  return settings;
}

function makeCalendarPreview(snapshot: Snapshot, req: CalendarEditRequest): CalendarEditPreview {
  const before = allCalendarEvents(snapshot).find((event) => event.id === req.event_id) ?? null;
  const after = before
    ? {
        ...before,
        starts_at: req.starts_at ?? before.starts_at,
        ends_at: req.ends_at ?? before.ends_at,
        title: req.title ?? before.title,
      }
    : null;
  return {
    operation: req.operation,
    event_id: req.event_id ?? null,
    before,
    after,
    conflicts: [],
    summary: 'Demo mode previews this calendar edit but keeps the acceptance snapshot unchanged.',
    mutates_state: false,
    requires_confirmation: true,
    generated_at: nowIso(snapshot),
  };
}

function makeCalendarApply(req: CalendarEditRequest): CalendarEditResult {
  return {
    status: 'unavailable',
    event_id: req.event_id ?? null,
    message: 'Demo mode is read-only.',
  };
}

function makeDoctor(snapshot: Snapshot): DoctorReport {
  return {
    ok: true,
    generated_at: nowIso(snapshot),
    core: [
      { id: 'snapshot', label: 'Acceptance snapshot loaded', status: 'pass', detail: DEMO_LABEL },
      { id: 'daemon', label: 'Local daemon connection', status: 'warn', detail: 'Disabled by design in demo mode.' },
    ],
    optional: [],
  };
}

function makeSetup(snapshot: Snapshot) {
  return {
    ok: true,
    generated_at: nowIso(snapshot),
    data_dir: '/demo',
    api_token_present: false,
    memory_root: '/demo/acceptance-vault',
    vault_configured: true,
    hints: [DEMO_LABEL],
  };
}

function makePermissions(): PermissionsView {
  return { grants: [] };
}

function makeInspectDoctor(snapshot: Snapshot): InspectDoctorReport {
  return {
    topic: 'doctor',
    summary: 'Acceptance demo is healthy and read-only.',
    healthy: true,
    checks: [
      {
        name: 'snapshot_loaded',
        passed: true,
        detail: `${allCalendarEvents(snapshot).length} visible calendar events, with raw proof artifacts retained separately.`,
      },
      {
        name: 'daemon_connection',
        passed: true,
        detail: 'Skipped by design: this demo never connects to the local daemon.',
      },
    ],
    runtime: {
      mode: 'acceptance-demo',
    },
  };
}

function makeInspectSetup(snapshot: Snapshot): InspectSetupReport {
  return {
    topic: 'setup',
    version: 'acceptance-demo',
    runtime_name: 'Kora acceptance browser demo',
    protocol_version: 'demo',
    data_dir: '/demo',
    operational_db: { path: '/demo/operational.db', exists: false },
    projection_db: { path: '/demo/projection.db', exists: false },
    llm: {
      provider: 'disabled',
      model: 'acceptance-snapshot',
      api_base: 'none',
      timeout: 0,
      max_tokens: 0,
    },
    memory: {
      path: '/demo/acceptance-vault',
      embedding_model: 'snapshot',
      embedding_dims: 0,
    },
    security: {
      api_token_path: '/demo/token',
      token_file_exists: false,
      injection_scan_enabled: true,
      auth_mode: 'demo',
      cors_origins: ['127.0.0.1'],
    },
    daemon: {
      host: '127.0.0.1',
      port: 0,
    },
    supported_inspect_topics: ['doctor', 'setup'],
    capabilities: {
      label: DEMO_LABEL,
      generated_at: nowIso(snapshot),
    },
  };
}

function makeOrchestration(snapshot: Snapshot): OrchestrationStatusView {
  return {
    status: 'ok',
    pipelines: [{ name: 'acceptance-demo-snapshot', stage_count: 5 }],
    live_tasks: [],
    open_decisions_count: 0,
    system_phase: `read-only snapshot generated ${nowIso(snapshot)}`,
  };
}

function makeShutdown(): DaemonShutdownResult {
  return { status: 'unavailable in demo mode' };
}

function asRecord(value: unknown): Record<string, unknown> {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  return {};
}

function arrayValue(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function stringValue(value: unknown): string | null {
  return typeof value === 'string' && value.length > 0 ? value : null;
}

function truncate(value: string, length: number): string {
  if (value.length <= length) return value;
  return `${value.slice(0, length - 1)}…`;
}
