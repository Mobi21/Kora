import { useMutation } from '@tanstack/react-query';
import { useMemo } from 'react';
import { createApiClient, type ApiClient } from '@/lib/api/client';
import { useConnection } from '@/lib/api/connection';
import {
  queryKeys,
  useMedication as useMedicationDay,
  useMedicationApply,
} from '@/lib/api/queries';
import type {
  MedicationDayView,
  MedicationLogPreview,
  MedicationLogRequest,
} from '@/lib/api/types';

function useApi(): ApiClient | null {
  const conn = useConnection();
  return useMemo(() => (conn ? createApiClient(conn) : null), [conn]);
}

export function useMedicationPreview() {
  const api = useApi();
  return useMutation<MedicationLogPreview, Error, MedicationLogRequest>({
    mutationFn: async (req) => {
      if (!api) throw new Error('Not connected');
      return api.medicationPreview(req);
    },
  });
}

export { useMedicationDay, useMedicationApply, queryKeys };
export type { MedicationDayView };
