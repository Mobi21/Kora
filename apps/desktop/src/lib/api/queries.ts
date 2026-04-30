import { useMutation, useQuery, useQueryClient, type UseQueryOptions } from '@tanstack/react-query';
import { useMemo } from 'react';
import { useConnection } from './connection';
import { createApiClient, type ApiClient } from './client';
import type {
  AutonomousView,
  CalendarEditRequest,
  CalendarRangeView,
  DaemonHealth,
  DaemonStatus,
  DesktopSettings,
  DesktopStatusView,
  DoctorReport,
  IntegrationsView,
  MedicationDayView,
  MedicationLogRequest,
  PermissionsView,
  RepairApplyRequest,
  RepairPreviewRequest,
  RepairStateView,
  RoutineActionRequest,
  RoutineDayView,
  SetupReport,
  VaultContextView,
  VaultCorrectionRequest,
  VaultSearchView,
} from './types';

export const queryKeys = {
  status: () => ['kora', 'status'] as const,
  health: () => ['kora', 'health'] as const,
  daemonStatus: () => ['kora', 'daemon-status'] as const,
  doctor: () => ['kora', 'doctor'] as const,
  setup: () => ['kora', 'setup'] as const,
  permissions: () => ['kora', 'permissions'] as const,
  today: (date: string) => ['kora', 'today', date] as const,
  calendar: (start: string, end: string, view: string | undefined) =>
    ['kora', 'calendar', start, end, view ?? 'default'] as const,
  medication: (date: string) => ['kora', 'medication', date] as const,
  routines: (date: string) => ['kora', 'routines', date] as const,
  repair: (date: string) => ['kora', 'repair', date] as const,
  vaultSearch: (q: string) => ['kora', 'vault', 'search', q] as const,
  vaultContext: () => ['kora', 'vault', 'context'] as const,
  autonomous: () => ['kora', 'autonomous'] as const,
  integrations: () => ['kora', 'integrations'] as const,
  settings: () => ['kora', 'settings'] as const,
} as const;

function useApi(): ApiClient | null {
  const conn = useConnection();
  return useMemo(() => (conn ? createApiClient(conn) : null), [conn]);
}

type Opts<T> = Omit<UseQueryOptions<T, Error>, 'queryKey' | 'queryFn'>;

export function useStatus(opts?: Opts<DesktopStatusView>) {
  const api = useApi();
  return useQuery({
    queryKey: queryKeys.status(),
    queryFn: () => api!.status(),
    enabled: !!api,
    staleTime: 5_000,
    ...opts,
  });
}

export function useDaemonHealth(opts?: Opts<DaemonHealth>) {
  const api = useApi();
  return useQuery({
    queryKey: queryKeys.health(),
    queryFn: () => api!.health(),
    enabled: !!api,
    staleTime: 5_000,
    ...opts,
  });
}

export function useDaemonStatus(opts?: Opts<DaemonStatus>) {
  const api = useApi();
  return useQuery({
    queryKey: queryKeys.daemonStatus(),
    queryFn: () => api!.daemonStatus(),
    enabled: !!api,
    staleTime: 5_000,
    ...opts,
  });
}

export function useDoctor(opts?: Opts<DoctorReport>) {
  const api = useApi();
  return useQuery({
    queryKey: queryKeys.doctor(),
    queryFn: () => api!.doctor(),
    enabled: !!api,
    staleTime: 30_000,
    ...opts,
  });
}

export function useSetup(opts?: Opts<SetupReport>) {
  const api = useApi();
  return useQuery({
    queryKey: queryKeys.setup(),
    queryFn: () => api!.setup(),
    enabled: !!api,
    staleTime: 60_000,
    ...opts,
  });
}

export function usePermissions(opts?: Opts<PermissionsView>) {
  const api = useApi();
  return useQuery({
    queryKey: queryKeys.permissions(),
    queryFn: () => api!.permissions(),
    enabled: !!api,
    staleTime: 30_000,
    ...opts,
  });
}

export function useToday(date: string, opts?: Opts<unknown>) {
  const api = useApi();
  return useQuery({
    queryKey: queryKeys.today(date),
    queryFn: () => api!.today(date),
    enabled: !!api,
    staleTime: 30_000,
    ...opts,
  });
}

export function useCalendar(
  start: string,
  end: string,
  view?: 'day' | 'week' | 'month' | 'agenda',
  opts?: Opts<CalendarRangeView>,
) {
  const api = useApi();
  return useQuery({
    queryKey: queryKeys.calendar(start, end, view),
    queryFn: () => api!.calendar(start, end, view),
    enabled: !!api,
    staleTime: 60_000,
    ...opts,
  });
}

export function useMedication(date: string, opts?: Opts<MedicationDayView>) {
  const api = useApi();
  return useQuery({
    queryKey: queryKeys.medication(date),
    queryFn: () => api!.medication(date),
    enabled: !!api,
    staleTime: 30_000,
    ...opts,
  });
}

export function useRoutines(date: string, opts?: Opts<RoutineDayView>) {
  const api = useApi();
  return useQuery({
    queryKey: queryKeys.routines(date),
    queryFn: () => api!.routines(date),
    enabled: !!api,
    staleTime: 30_000,
    ...opts,
  });
}

export function useRepairState(date: string, opts?: Opts<RepairStateView>) {
  const api = useApi();
  return useQuery({
    queryKey: queryKeys.repair(date),
    queryFn: () => api!.repairState(date),
    enabled: !!api,
    staleTime: 15_000,
    ...opts,
  });
}

export function useVaultSearch(q: string, opts?: Opts<VaultSearchView>) {
  const api = useApi();
  return useQuery({
    queryKey: queryKeys.vaultSearch(q),
    queryFn: () => api!.vaultSearch(q),
    enabled: !!api && q.length > 0,
    staleTime: 60_000,
    ...opts,
  });
}

export function useVaultContext(opts?: Opts<VaultContextView>) {
  const api = useApi();
  return useQuery({
    queryKey: queryKeys.vaultContext(),
    queryFn: () => api!.vaultContext(),
    enabled: !!api,
    staleTime: 60_000,
    ...opts,
  });
}

export function useAutonomous(opts?: Opts<AutonomousView>) {
  const api = useApi();
  return useQuery({
    queryKey: queryKeys.autonomous(),
    queryFn: () => api!.autonomous(),
    enabled: !!api,
    staleTime: 10_000,
    refetchInterval: 10_000,
    ...opts,
  });
}

export function useIntegrations(opts?: Opts<IntegrationsView>) {
  const api = useApi();
  return useQuery({
    queryKey: queryKeys.integrations(),
    queryFn: () => api!.integrations(),
    enabled: !!api,
    staleTime: 30_000,
    ...opts,
  });
}

export function useSettings(opts?: Opts<DesktopSettings>) {
  const api = useApi();
  return useQuery({
    queryKey: queryKeys.settings(),
    queryFn: () => api!.getSettings(),
    enabled: !!api,
    staleTime: Infinity,
    ...opts,
  });
}

export function usePatchSettings() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (patch: Partial<DesktopSettings>) => api!.patchSettings(patch),
    onSuccess: (data) => {
      qc.setQueryData(queryKeys.settings(), data);
    },
  });
}

export function useCalendarPreview() {
  const api = useApi();
  return useMutation({
    mutationFn: async (req: CalendarEditRequest) => api!.calendarPreview(req),
  });
}

export function useCalendarApply() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (req: CalendarEditRequest) => api!.calendarApply(req),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['kora', 'calendar'] });
      qc.invalidateQueries({ queryKey: ['kora', 'today'] });
    },
  });
}

export function useMedicationApply() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (req: MedicationLogRequest) => api!.medicationApply(req),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['kora', 'medication'] });
    },
  });
}

export function useRoutinesApply() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (req: RoutineActionRequest) => api!.routinesApply(req),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['kora', 'routines'] });
    },
  });
}

export function useRepairPreview() {
  const api = useApi();
  return useMutation({
    mutationFn: async (req: RepairPreviewRequest) => api!.repairPreview(req),
  });
}

export function useRepairApply() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (req: RepairApplyRequest) => api!.repairApply(req),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['kora', 'today'] });
      qc.invalidateQueries({ queryKey: ['kora', 'repair'] });
    },
  });
}

export function useVaultCorrectionApply() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (req: VaultCorrectionRequest) => api!.vaultCorrectionApply(req),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['kora', 'vault'] });
    },
  });
}
