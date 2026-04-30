import { useMemo } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { createApiClient, type ApiClient } from '@/lib/api/client';
import { useConnection } from '@/lib/api/connection';
import { queryKeys } from '@/lib/api/queries';
import type {
  DesktopSettings,
  DesktopStatusView,
  InspectSetupReport,
  IntegrationsView,
  SettingsValidationView,
} from '@/lib/api/types';

/**
 * Local namespace for screens that want to query the same data
 * (status / setup / integrations) without churning on the global keys.
 */
export const settingsKeys = {
  settings: queryKeys.settings,
  inspectSetup: () => ['settings', 'inspect', 'setup'] as const,
  status: () => ['settings', 'status'] as const,
  integrations: () => ['settings', 'integrations'] as const,
} as const;

function useApi(): ApiClient | null {
  const conn = useConnection();
  return useMemo(() => (conn ? createApiClient(conn) : null), [conn]);
}

export function useSettingsQuery() {
  const api = useApi();
  return useQuery<DesktopSettings, Error>({
    queryKey: queryKeys.settings(),
    queryFn: () => api!.getSettings(),
    enabled: !!api,
    staleTime: Infinity,
    retry: 1,
  });
}

export function useInspectSetupForSettings() {
  const api = useApi();
  return useQuery<InspectSetupReport, Error>({
    queryKey: settingsKeys.inspectSetup(),
    queryFn: () => api!.getInspectSetup(),
    enabled: !!api,
    staleTime: 60_000,
    retry: 0,
  });
}

export function useStatusForSettings() {
  const api = useApi();
  return useQuery<DesktopStatusView, Error>({
    queryKey: settingsKeys.status(),
    queryFn: () => api!.status(),
    enabled: !!api,
    staleTime: 30_000,
    retry: 0,
  });
}

export function useIntegrationsForSettings() {
  const api = useApi();
  return useQuery<IntegrationsView, Error>({
    queryKey: settingsKeys.integrations(),
    queryFn: () => api!.integrations(),
    enabled: !!api,
    staleTime: 30_000,
    retry: 0,
  });
}

export interface SectionSaveInput {
  patch: Partial<DesktopSettings>;
}

/** Validate the patch first; on success, PATCH the daemon. */
export function useSectionSave() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation<
    { settings: DesktopSettings; validation: SettingsValidationView },
    Error,
    SectionSaveInput
  >({
    mutationFn: async ({ patch }) => {
      if (!api) throw new Error('No daemon connection.');
      const validation = await api.validateSettings(patch);
      if (!validation.valid) {
        return { settings: await api.getSettings(), validation };
      }
      const settings = await api.patchSettings(patch);
      return { settings, validation };
    },
    onSuccess: ({ settings, validation }) => {
      if (validation.valid) {
        qc.setQueryData(queryKeys.settings(), settings);
      }
    },
  });
}
