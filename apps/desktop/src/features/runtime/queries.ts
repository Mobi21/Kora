import { useMemo } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { createApiClient, type ApiClient } from '@/lib/api/client';
import { useConnection } from '@/lib/api/connection';
import type {
  DaemonHealth,
  DaemonStatus,
  InspectDoctorReport,
  InspectSetupReport,
  OrchestrationStatusView,
} from '@/lib/api/types';

// Local query-key namespace for the Runtime screen. We deliberately do
// not collide with the shared `queryKeys` in `lib/api/queries.ts` so
// that other screens don't churn when this one refetches.
export const runtimeKeys = {
  health: () => ['runtime', 'health'] as const,
  daemonStatus: () => ['runtime', 'daemon-status'] as const,
  inspectDoctor: () => ['runtime', 'inspect', 'doctor'] as const,
  inspectSetup: () => ['runtime', 'inspect', 'setup'] as const,
  orchestrationStatus: () => ['runtime', 'orchestration', 'status'] as const,
  daemonLogs: (lines: number) => ['runtime', 'logs', lines] as const,
} as const;

function useApi(): ApiClient | null {
  const conn = useConnection();
  return useMemo(() => (conn ? createApiClient(conn) : null), [conn]);
}

export function useDaemonHealthQuery() {
  const api = useApi();
  return useQuery<DaemonHealth, Error>({
    queryKey: runtimeKeys.health(),
    queryFn: () => api!.health(),
    enabled: !!api,
    staleTime: 5_000,
    retry: 1,
    notifyOnChangeProps: ['data', 'error', 'isLoading', 'isError', 'isFetching'],
  });
}

export function useDaemonStatusQuery() {
  const api = useApi();
  return useQuery<DaemonStatus, Error>({
    queryKey: runtimeKeys.daemonStatus(),
    queryFn: () => api!.daemonStatus(),
    enabled: !!api,
    staleTime: 5_000,
    retry: 1,
    notifyOnChangeProps: ['data', 'error', 'isLoading', 'isError', 'isFetching'],
  });
}

export function useInspectDoctorQuery() {
  const api = useApi();
  return useQuery<InspectDoctorReport, Error>({
    queryKey: runtimeKeys.inspectDoctor(),
    queryFn: () => api!.getInspectDoctor(),
    enabled: !!api,
    staleTime: 30_000,
    retry: 0,
    notifyOnChangeProps: ['data', 'error', 'isLoading', 'isError', 'isFetching'],
  });
}

export function useInspectSetupQuery() {
  const api = useApi();
  return useQuery<InspectSetupReport, Error>({
    queryKey: runtimeKeys.inspectSetup(),
    queryFn: () => api!.getInspectSetup(),
    enabled: !!api,
    staleTime: 30_000,
    retry: 0,
    notifyOnChangeProps: ['data', 'error', 'isLoading', 'isError', 'isFetching'],
  });
}

export function useOrchestrationStatusQuery() {
  const api = useApi();
  return useQuery<OrchestrationStatusView, Error>({
    queryKey: runtimeKeys.orchestrationStatus(),
    queryFn: () => api!.orchestrationStatus(),
    enabled: !!api,
    staleTime: 5_000,
    retry: 1,
    notifyOnChangeProps: ['data', 'error', 'isLoading', 'isError', 'isFetching'],
  });
}

export function useDaemonLogsQuery(lines = 80, enabled = false) {
  const api = useApi();
  return useQuery<string[], Error>({
    queryKey: runtimeKeys.daemonLogs(lines),
    queryFn: () => api!.daemonLogsTail(lines),
    enabled: !!api && enabled,
    staleTime: 2_000,
    retry: 0,
    notifyOnChangeProps: ['data', 'error', 'isLoading', 'isError', 'isFetching'],
  });
}

export function useShutdownDaemon() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () => api!.shutdownDaemon(),
    onSuccess: () => {
      // The daemon will tear down the listener within ~100ms — invalidate
      // every runtime key so the next render reflects the new world.
      qc.invalidateQueries({ queryKey: ['runtime'] });
      qc.invalidateQueries({ queryKey: ['kora'] });
    },
  });
}
