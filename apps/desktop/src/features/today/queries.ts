import { useQuery, type UseQueryResult } from '@tanstack/react-query';
import { useMemo } from 'react';
import { createApiClient } from '@/lib/api/client';
import { useConnection } from '@/lib/api/connection';
import type {
  LoadBand,
  TimelineItem,
  TodayViewModel,
} from '@/lib/api/types';
import type { ProvenanceKind } from '@/components/ui/provenance-dot';
import type { PillStatus } from '@/components/ui/pill';
import type { BadgeProvenance } from '@/components/ui/badge';

const PROVENANCE_KINDS: readonly ProvenanceKind[] = [
  'local',
  'workspace',
  'inferred',
  'confirmed',
  'repair',
];

/** Today's local-calendar ISO date (YYYY-MM-DD), regardless of UTC offset. */
export function localIsoDate(d: Date = new Date()): string {
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  return `${yyyy}-${mm}-${dd}`;
}

/** Tomorrow as a local-calendar ISO date string. */
export function tomorrowIsoDate(): string {
  const d = new Date();
  d.setDate(d.getDate() + 1);
  return localIsoDate(d);
}

/** Resolve the first known provenance kind from an item's provenance array. */
export function resolveProvenance(provenance: readonly string[]): ProvenanceKind {
  for (const tag of provenance) {
    const kind = tag.toLowerCase() as ProvenanceKind;
    if (PROVENANCE_KINDS.includes(kind)) return kind;
  }
  return 'inferred';
}

/** ProvenanceKind narrowed for the <Badge> primitive (which adds 'neutral'). */
export function resolveBadgeProvenance(provenance: readonly string[]): BadgeProvenance {
  return resolveProvenance(provenance);
}

/** Map a load band to a shape-coded pill status. */
export function loadBandToPill(band: LoadBand): PillStatus {
  switch (band) {
    case 'light':
    case 'normal':
      return 'ok';
    case 'high':
    case 'stabilization':
      return 'warn';
    case 'overloaded':
      return 'degraded';
    case 'unknown':
    default:
      return 'unknown';
  }
}

/** Human label for a load band. */
export function loadBandLabel(band: LoadBand): string {
  switch (band) {
    case 'light':
      return 'Light load';
    case 'normal':
      return 'Normal load';
    case 'high':
      return 'High load';
    case 'overloaded':
      return 'Overloaded';
    case 'stabilization':
      return 'Stabilizing';
    case 'unknown':
    default:
      return 'Load unknown';
  }
}

/** Format an ISO range for inline display (e.g. "10:00–11:00"). Falls back to a single time. */
export function formatItemTimeRange(item: TimelineItem): string | null {
  const fmt = (s: string): string =>
    new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' }).format(
      new Date(s),
    );
  if (item.starts_at && item.ends_at) return `${fmt(item.starts_at)}–${fmt(item.ends_at)}`;
  if (item.starts_at) return fmt(item.starts_at);
  return null;
}

/** Format only the start time as a short chip (e.g. "10:30"). */
export function formatItemStartChip(item: TimelineItem): string {
  if (!item.starts_at) return 'flex';
  return new Intl.DateTimeFormat(undefined, {
    hour: 'numeric',
    minute: '2-digit',
  }).format(new Date(item.starts_at));
}

/** Risk pill status, or null when there's nothing to show. */
export function riskPillStatus(
  risk: TimelineItem['risk'],
): { status: PillStatus; label: string } | null {
  if (risk === 'watch') return { status: 'warn', label: 'watch' };
  if (risk === 'repair') return { status: 'degraded', label: 'needs repair' };
  return null;
}

/**
 * Today view-model query, strongly typed.
 * Shares the cache key with the global `useToday` so cross-cutting invalidations
 * (calendar apply, repair apply) refresh this screen automatically.
 */
export function useToday(date: string): UseQueryResult<TodayViewModel, Error> {
  const conn = useConnection();
  const api = useMemo(() => (conn ? createApiClient(conn) : null), [conn]);
  return useQuery<TodayViewModel, Error>({
    queryKey: ['kora', 'today', date],
    queryFn: () => api!.today(date),
    enabled: !!api,
    staleTime: 30_000,
    refetchInterval: 60_000,
    refetchOnWindowFocus: true,
  });
}
