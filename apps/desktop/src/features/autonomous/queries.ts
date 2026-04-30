import { useQuery, type UseQueryResult } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';
import { createApiClient } from '@/lib/api/client';
import { useConnection } from '@/lib/api/connection';
import type {
  AutonomousPlanView,
  AutonomousView,
} from '@/lib/api/types';
import type { PillStatus } from '@/components/ui/pill';

export const AUTONOMOUS_QUERY_KEY = ['kora', 'autonomous'] as const;

/** Bucket label used for the left provenance rule on plan cards. */
export type PlanBucket = 'active' | 'queued' | 'completed';

/**
 * Map a plan's lifecycle status to a shape-coded pill so screen readers and
 * users that don't perceive color still get a consistent signal.
 */
export function statusToPill(status: AutonomousPlanView['status']): {
  status: PillStatus;
  label: string;
} {
  switch (status) {
    case 'running':
      return { status: 'ok', label: 'running' };
    case 'completed':
      return { status: 'ok', label: 'completed' };
    case 'queued':
      return { status: 'unknown', label: 'queued' };
    case 'paused':
      return { status: 'warn', label: 'paused' };
    case 'failed':
      return { status: 'degraded', label: 'failed' };
    case 'cancelled':
      return { status: 'degraded', label: 'cancelled' };
    default:
      return { status: 'unknown', label: status };
  }
}

/** CSS color token for the 4px left-rule on a plan card given its bucket. */
export function bucketRuleColor(bucket: PlanBucket): string {
  switch (bucket) {
    case 'active':
      return 'var(--ok)';
    case 'queued':
      return 'var(--fg-subtle)';
    case 'completed':
      return 'var(--fg-muted)';
  }
}

/** Format a timestamp as a relative phrase ("in 4m 12s", "12s ago", "overdue 3m"). */
export function formatCountdown(target: string | null, now: number = Date.now()): string {
  if (!target) return '—';
  const t = new Date(target).getTime();
  if (Number.isNaN(t)) return '—';
  const diff = t - now;
  const ms = Math.abs(diff);
  const totalSec = Math.floor(ms / 1000);
  const days = Math.floor(totalSec / 86400);
  const hours = Math.floor((totalSec % 86400) / 3600);
  const minutes = Math.floor((totalSec % 3600) / 60);
  const seconds = totalSec % 60;

  let core: string;
  if (days > 0) {
    core = `${days}d ${hours}h`;
  } else if (hours > 0) {
    core = `${hours}h ${minutes}m`;
  } else if (minutes > 0) {
    core = `${minutes}m ${String(seconds).padStart(2, '0')}s`;
  } else {
    core = `${seconds}s`;
  }
  if (diff >= 0) return `in ${core}`;
  return `overdue ${core}`;
}

/** Format an absolute timestamp as a short relative phrase ("12m ago", "2h ago"). */
export function formatLastActivity(input: string | null, now: number = Date.now()): string {
  if (!input) return '—';
  const t = new Date(input).getTime();
  if (Number.isNaN(t)) return '—';
  const diffSec = Math.floor((now - t) / 1000);
  if (diffSec < 5) return 'just now';
  if (diffSec < 60) return `${diffSec}s ago`;
  const m = Math.floor(diffSec / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  return `${d}d ago`;
}

/** Format a timestamp as a short absolute label suitable for tooltips. */
export function formatTimestamp(input: string | null): string {
  if (!input) return '—';
  const d = new Date(input);
  if (Number.isNaN(d.getTime())) return '—';
  return new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  }).format(d);
}

/** Clamp a 0..1 progress value. */
export function clampProgress(value: number): number {
  if (!Number.isFinite(value)) return 0;
  if (value < 0) return 0;
  if (value > 1) return 1;
  return value;
}

/**
 * A 1Hz "now" hook that drives the deadline / activity counters. Pauses while
 * the document is hidden so we don't burn CPU in the background.
 */
export function useTickingNow(intervalMs: number = 1000): number {
  const [now, setNow] = useState<number>(() => Date.now());
  useEffect(() => {
    let timer: number | null = null;
    const start = (): void => {
      if (timer != null) return;
      timer = window.setInterval(() => setNow(Date.now()), intervalMs);
    };
    const stop = (): void => {
      if (timer != null) {
        window.clearInterval(timer);
        timer = null;
      }
    };
    const onVisibility = (): void => {
      if (document.visibilityState === 'visible') {
        setNow(Date.now());
        start();
      } else {
        stop();
      }
    };
    if (document.visibilityState === 'visible') start();
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      stop();
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [intervalMs]);
  return now;
}

/** Strongly-typed AutonomousView query. */
export function useAutonomous(): UseQueryResult<AutonomousView, Error> {
  const conn = useConnection();
  const api = useMemo(() => (conn ? createApiClient(conn) : null), [conn]);
  return useQuery<AutonomousView, Error>({
    queryKey: AUTONOMOUS_QUERY_KEY,
    queryFn: () => api!.autonomous(),
    enabled: !!api,
    staleTime: 15_000,
    refetchInterval: 30_000,
    refetchOnWindowFocus: true,
  });
}
