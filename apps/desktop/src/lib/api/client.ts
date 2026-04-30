import type { Connection } from './connection';
import { createDemoApiClient } from '@/lib/demo/client';
import { isDemoMode } from '@/lib/demo/mode';
import type {
  AutonomousView,
  CalendarEditPreview,
  CalendarEditRequest,
  CalendarEditResult,
  CalendarRangeView,
  DaemonHealth,
  DaemonShutdownResult,
  DaemonStatus,
  DesktopSettings,
  DesktopStatusView,
  DoctorReport,
  InspectDoctorReport,
  InspectSetupReport,
  IntegrationsView,
  MedicationDayView,
  MedicationLogPreview,
  MedicationLogRequest,
  MedicationLogResult,
  OrchestrationStatusView,
  PermissionsView,
  RepairApplyRequest,
  RepairApplyResult,
  RepairPreview,
  RepairPreviewRequest,
  RepairStateView,
  RoutineActionRequest,
  RoutineActionResult,
  RoutineDayView,
  SettingsValidationView,
  SetupReport,
  TodayViewModel,
  VaultContextView,
  VaultCorrectionPreview,
  VaultCorrectionRequest,
  VaultCorrectionResult,
  VaultSearchView,
} from './types';

export class ApiError extends Error {
  readonly status: number;
  readonly url: string;
  readonly body: string;
  constructor(status: number, url: string, body: string, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.url = url;
    this.body = body;
  }
}

export interface ApiClient {
  // /desktop/*
  status(): Promise<DesktopStatusView>;
  today(date: string): Promise<TodayViewModel>;
  calendar(start: string, end: string, view?: 'day' | 'week' | 'month' | 'agenda'): Promise<CalendarRangeView>;
  calendarPreview(req: CalendarEditRequest): Promise<CalendarEditPreview>;
  calendarApply(req: CalendarEditRequest): Promise<CalendarEditResult>;
  medication(date: string): Promise<MedicationDayView>;
  medicationPreview(req: MedicationLogRequest): Promise<MedicationLogPreview>;
  medicationApply(req: MedicationLogRequest): Promise<MedicationLogResult>;
  routines(date: string): Promise<RoutineDayView>;
  routinesApply(req: RoutineActionRequest): Promise<RoutineActionResult>;
  repairState(date: string): Promise<RepairStateView>;
  repairPreview(req: RepairPreviewRequest): Promise<RepairPreview>;
  repairApply(req: RepairApplyRequest): Promise<RepairApplyResult>;
  vaultSearch(q: string): Promise<VaultSearchView>;
  vaultContext(): Promise<VaultContextView>;
  vaultCorrectionPreview(req: VaultCorrectionRequest): Promise<VaultCorrectionPreview>;
  vaultCorrectionApply(req: VaultCorrectionRequest): Promise<VaultCorrectionResult>;
  autonomous(): Promise<AutonomousView>;
  integrations(): Promise<IntegrationsView>;
  getSettings(): Promise<DesktopSettings>;
  patchSettings(patch: Partial<DesktopSettings>): Promise<DesktopSettings>;
  validateSettings(body: Partial<DesktopSettings>): Promise<SettingsValidationView>;

  // /api/v1/* daemon-level
  health(): Promise<DaemonHealth>;
  daemonStatus(): Promise<DaemonStatus>;
  doctor(): Promise<DoctorReport>;
  setup(): Promise<SetupReport>;
  permissions(): Promise<PermissionsView>;

  // RuntimeInspector endpoints — match the live `/inspect/*` shapes.
  getInspectDoctor(): Promise<InspectDoctorReport>;
  getInspectSetup(): Promise<InspectSetupReport>;
  orchestrationStatus(): Promise<OrchestrationStatusView>;

  // Daemon control
  shutdownDaemon(): Promise<DaemonShutdownResult>;

  // Log tailing — backed by the Electron preload bridge
  // (`window.kora.daemon.logs`). Returns the last N lines or an empty
  // array when the bridge is unavailable.
  daemonLogsTail(lines?: number): Promise<string[]>;
}

export interface CreateClientOptions {
  baseUrlPrefix?: string;
  fetchImpl?: typeof fetch;
  timeoutMs?: number;
}

export function createApiClient(
  conn: Connection,
  opts: CreateClientOptions = {},
): ApiClient {
  if (isDemoMode()) return createDemoApiClient();

  const fetchImpl = opts.fetchImpl ?? fetch.bind(globalThis);
  const baseUrlPrefix = opts.baseUrlPrefix ?? '/api/v1';
  const baseUrl = `http://${conn.host}:${conn.port}${baseUrlPrefix}`;
  const timeoutMs = opts.timeoutMs ?? 20_000;

  async function request<T>(
    method: 'GET' | 'POST' | 'PATCH' | 'PUT' | 'DELETE',
    path: string,
    init?: { body?: unknown; query?: Record<string, string | number | undefined | null> },
  ): Promise<T> {
    const url = new URL(`${baseUrl}${path}`);
    if (init?.query) {
      for (const [k, v] of Object.entries(init.query)) {
        if (v == null) continue;
        url.searchParams.set(k, String(v));
      }
    }
    const headers: Record<string, string> = {
      Authorization: `Bearer ${conn.token}`,
      Accept: 'application/json',
    };
    let body: BodyInit | undefined;
    if (init?.body !== undefined) {
      headers['Content-Type'] = 'application/json';
      body = JSON.stringify(init.body);
    }

    const ac = new AbortController();
    const t = setTimeout(() => ac.abort(), timeoutMs);
    let res: Response;
    try {
      res = await fetchImpl(url.toString(), { method, headers, body, signal: ac.signal });
    } finally {
      clearTimeout(t);
    }

    const text = await res.text();
    if (!res.ok) {
      throw new ApiError(res.status, url.toString(), text, `${method} ${path} failed: ${res.status}`);
    }
    if (!text) return undefined as T;
    try {
      return JSON.parse(text) as T;
    } catch (err) {
      throw new ApiError(res.status, url.toString(), text, `Invalid JSON in response: ${(err as Error).message}`);
    }
  }

  return {
    status: () => request<DesktopStatusView>('GET', '/desktop/status'),
    today: (date) => request<TodayViewModel>('GET', '/desktop/today', { query: { date } }),
    calendar: (start, end, view) =>
      request<CalendarRangeView>('GET', '/desktop/calendar', { query: { start, end, view } }),
    calendarPreview: (body) =>
      request<CalendarEditPreview>('POST', '/desktop/calendar/preview', { body }),
    calendarApply: (body) =>
      request<CalendarEditResult>('POST', '/desktop/calendar/apply', { body }),
    medication: (date) => request<MedicationDayView>('GET', '/desktop/medication', { query: { date } }),
    medicationPreview: (body) =>
      request<MedicationLogPreview>('POST', '/desktop/medication/preview', { body }),
    medicationApply: (body) =>
      request<MedicationLogResult>('POST', '/desktop/medication/apply', { body }),
    routines: (date) => request<RoutineDayView>('GET', '/desktop/routines', { query: { date } }),
    routinesApply: (body) =>
      request<RoutineActionResult>('POST', '/desktop/routines/apply', { body }),
    repairState: (date) =>
      request<RepairStateView>('GET', '/desktop/repair/state', { query: { date } }),
    repairPreview: (body) => request<RepairPreview>('POST', '/desktop/repair/preview', { body }),
    repairApply: (body) => request<RepairApplyResult>('POST', '/desktop/repair/apply', { body }),
    vaultSearch: (q) => request<VaultSearchView>('GET', '/desktop/vault/search', { query: { q } }),
    vaultContext: () => request<VaultContextView>('GET', '/desktop/vault/context'),
    vaultCorrectionPreview: (body) =>
      request<VaultCorrectionPreview>('POST', '/desktop/vault/correction/preview', { body }),
    vaultCorrectionApply: (body) =>
      request<VaultCorrectionResult>('POST', '/desktop/vault/correction/apply', { body }),
    autonomous: () => request<AutonomousView>('GET', '/desktop/autonomous'),
    integrations: () => request<IntegrationsView>('GET', '/desktop/integrations'),
    getSettings: () => request<DesktopSettings>('GET', '/desktop/settings'),
    patchSettings: (patch) => request<DesktopSettings>('PATCH', '/desktop/settings', { body: patch }),
    validateSettings: (body) =>
      request<SettingsValidationView>('POST', '/desktop/settings/validate', { body }),

    health: () => request<DaemonHealth>('GET', '/health'),
    daemonStatus: () => request<DaemonStatus>('GET', '/status'),
    doctor: () => request<DoctorReport>('GET', '/inspect/doctor'),
    setup: () => request<SetupReport>('GET', '/inspect/setup'),
    permissions: () => request<PermissionsView>('GET', '/permissions'),

    getInspectDoctor: () => request<InspectDoctorReport>('GET', '/inspect/doctor'),
    getInspectSetup: () => request<InspectSetupReport>('GET', '/inspect/setup'),
    orchestrationStatus: () =>
      request<OrchestrationStatusView>('GET', '/orchestration/status'),

    shutdownDaemon: () => request<DaemonShutdownResult>('POST', '/daemon/shutdown'),

    daemonLogsTail: async (lines = 80) => {
      if (typeof window === 'undefined' || !window.kora?.daemon?.logs) {
        return [];
      }
      try {
        return await window.kora.daemon.logs(lines);
      } catch {
        return [];
      }
    },
  };
}
