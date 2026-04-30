import { useEffect, useMemo, useState } from 'react';
import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryOptions,
} from '@tanstack/react-query';
import { createApiClient, type ApiClient } from '@/lib/api/client';
import { useConnection } from '@/lib/api/connection';
import type {
  VaultContextView,
  VaultCorrectionPreview,
  VaultCorrectionRequest,
  VaultCorrectionResult,
  VaultSearchView,
} from '@/lib/api/types';

export const memoryKeys = {
  context: () => ['memory', 'context'] as const,
  search: (q: string) => ['memory', 'search', q] as const,
  preview: (req: VaultCorrectionRequest) => ['memory', 'preview', req] as const,
} as const;

function useApi(): ApiClient | null {
  const conn = useConnection();
  return useMemo(() => (conn ? createApiClient(conn) : null), [conn]);
}

type Opts<T> = Omit<UseQueryOptions<T, Error>, 'queryKey' | 'queryFn'>;

/** Returns the live debounced value of `value` after `delay` ms of stillness. */
export function useDebouncedValue<T>(value: T, delay = 250): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(t);
  }, [value, delay]);
  return debounced;
}

export function useVaultContextQuery(opts?: Opts<VaultContextView>) {
  const api = useApi();
  return useQuery<VaultContextView, Error>({
    queryKey: memoryKeys.context(),
    queryFn: () => api!.vaultContext(),
    enabled: !!api,
    staleTime: 30_000,
    refetchOnWindowFocus: false,
    retry: 1,
    ...opts,
  });
}

export function useVaultSearchQuery(query: string, opts?: Opts<VaultSearchView>) {
  const api = useApi();
  const trimmed = query.trim();
  return useQuery<VaultSearchView, Error>({
    queryKey: memoryKeys.search(trimmed),
    queryFn: () => api!.vaultSearch(trimmed),
    enabled: !!api && trimmed.length > 0,
    staleTime: 30_000,
    refetchOnWindowFocus: false,
    retry: 1,
    ...opts,
  });
}

export function useVaultCorrectionPreview() {
  const api = useApi();
  return useMutation<VaultCorrectionPreview, Error, VaultCorrectionRequest>({
    mutationFn: (req) => api!.vaultCorrectionPreview(req),
  });
}

export function useVaultCorrectionApply() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation<VaultCorrectionResult, Error, VaultCorrectionRequest>({
    mutationFn: (req) => api!.vaultCorrectionApply(req),
    onSuccess: (result) => {
      if (result.status === 'applied') {
        qc.invalidateQueries({ queryKey: ['memory'] });
        qc.invalidateQueries({ queryKey: ['kora', 'vault'] });
      }
    },
  });
}
