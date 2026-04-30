import { useCallback, useEffect, useMemo, useState } from 'react';
import { AlertCircle, Power, RefreshCcw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { Pill, type PillStatus } from '@/components/ui/pill';
import { Badge } from '@/components/ui/badge';
import { useConnection } from '@/lib/api/connection';
import { createApiClient } from '@/lib/api/client';
import type { DesktopStatusView } from '@/lib/api/types';
import { cn } from '@/lib/utils';
import { SectionCard, type DefinitionPair } from './SectionCard';
import { ShutdownDialog } from './ShutdownDialog';

function uptimeFromStarted(startedAt: string | null): string {
  if (!startedAt) return '—';
  const started = new Date(startedAt).getTime();
  if (!Number.isFinite(started)) return '—';
  const elapsedSec = Math.max(0, Math.floor((Date.now() - started) / 1000));
  const days = Math.floor(elapsedSec / 86_400);
  const hours = Math.floor((elapsedSec % 86_400) / 3600);
  const mins = Math.floor((elapsedSec % 3600) / 60);
  const secs = elapsedSec % 60;
  if (days > 0) return `${days}d ${hours}h ${mins}m`;
  if (hours > 0) return `${hours}h ${mins}m`;
  if (mins > 0) return `${mins}m ${secs}s`;
  return `${secs}s`;
}

function ErrorChip({ message, onRetry }: { message: string; onRetry: () => void }): JSX.Element {
  return (
    <div className="flex items-center justify-between gap-3 rounded-[var(--r-1)] border border-[var(--border)] bg-[color-mix(in_oklch,var(--danger)_6%,transparent)] px-3 py-2 text-[var(--fs-sm)]">
      <div className="flex items-center gap-2 text-[var(--fg)]">
        <AlertCircle className="h-4 w-4 text-[var(--danger)]" strokeWidth={1.5} />
        <span className="truncate" title={message}>
          {message}
        </span>
      </div>
      <Button variant="ghost" size="sm" onClick={onRetry} aria-label="Retry">
        <RefreshCcw className="h-3.5 w-3.5" strokeWidth={1.5} />
        Retry
      </Button>
    </div>
  );
}

function SkeletonPairs({ rows = 4 }: { rows?: number }): JSX.Element {
  return (
    <div className="flex flex-col gap-2.5">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="flex items-center justify-between gap-3">
          <Skeleton className="h-3 w-24" />
          <Skeleton className="h-3 w-32" />
        </div>
      ))}
    </div>
  );
}

const SUBSYSTEM_STATUS: PillStatus = 'degraded';

const VAULT_HEALTH_TO_PILL: Record<string, PillStatus> = {
  ok: 'ok',
  unconfigured: 'unknown',
  missing: 'degraded',
  degraded: 'warn',
};

function useRuntimeStatusSnapshot(): {
  data: DesktopStatusView | null;
  error: Error | null;
  isLoading: boolean;
  isError: boolean;
  isFetching: boolean;
  refetch: () => Promise<void>;
} {
  const conn = useConnection();
  const [data, setData] = useState<DesktopStatusView | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isFetching, setIsFetching] = useState(false);

  const refetch = useCallback(async () => {
    if (!conn) {
      setData(null);
      setError(null);
      setIsLoading(false);
      setIsFetching(false);
      return;
    }

    setIsFetching(true);
    try {
      const next = await createApiClient(conn).status();
      setData(next);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err : new Error(String(err)));
    } finally {
      setIsLoading(false);
      setIsFetching(false);
    }
  }, [conn]);

  useEffect(() => {
    let cancelled = false;
    async function load(): Promise<void> {
      await refetch();
      if (cancelled) return;
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [refetch]);

  return {
    data,
    error,
    isLoading,
    isError: !!error,
    isFetching,
    refetch,
  };
}

export function StatusTab(): JSX.Element {
  const conn = useConnection();
  const status = useRuntimeStatusSnapshot();
  const [shutdownOpen, setShutdownOpen] = useState(false);

  const uptime = useMemo(() => uptimeFromStarted(null), []);

  const daemonPairs: DefinitionPair[] = [
    { label: 'Host', value: conn?.host ?? '—', mono: true },
    { label: 'Port', value: conn?.port ?? '—', mono: true },
    {
      label: 'PID',
      value: conn?.pid != null ? String(conn.pid) : '—',
      mono: true,
    },
    {
      label: 'Uptime',
      value: uptime,
      mono: true,
    },
    {
      label: 'Health',
      value: status.isLoading ? (
        <Skeleton className="ml-auto h-4 w-16" />
      ) : status.isError ? (
        <Pill status="degraded" label="unreachable" />
      ) : (
        <Pill
          status={status.data?.status === 'connected' ? 'ok' : 'warn'}
          label={status.data?.status ?? 'unknown'}
        />
      ),
    },
  ];

  const sessionLoading = status.isLoading;
  const sessionError = status.isError;

  return (
    <>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <SectionCard
          title="Daemon"
          trailing={
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                status.refetch();
              }}
              aria-label="Refresh daemon status"
            >
              <RefreshCcw
                className={cn(
                  'h-3.5 w-3.5',
                  status.isFetching && 'opacity-60',
                )}
                strokeWidth={1.5}
              />
            </Button>
          }
          pairs={daemonPairs}
          footer={
            <div className="flex items-center justify-between gap-3">
              <span>
                Stopping the daemon ends the active session for this device.
              </span>
              <Button
                variant="danger"
                size="sm"
                onClick={() => setShutdownOpen(true)}
                aria-label="Stop the Kora daemon"
              >
                <Power className="h-4 w-4" strokeWidth={1.5} />
                Stop daemon
              </Button>
            </div>
          }
        />

        <SectionCard
          title="Session"
          trailing={
            status.data?.support_mode ? (
              <Pill status="ok" label={status.data.support_mode}>
                {status.data.support_mode}
              </Pill>
            ) : undefined
          }
        >
          {sessionLoading ? (
            <SkeletonPairs rows={3} />
          ) : sessionError ? (
            <ErrorChip
              message={status.error?.message ?? 'Failed to load session'}
              onRetry={() => status.refetch()}
            />
          ) : !status.data ? (
            <p className="text-[var(--fs-sm)] text-[var(--fg-muted)]">No data.</p>
          ) : (
            <dl className="flex flex-col gap-2.5 text-[var(--fs-sm)]">
              <div className="flex items-baseline justify-between gap-3">
                <dt className="text-[var(--fg-muted)]">Active</dt>
                <dd className="num-tabular text-[var(--fg)]">
                  {status.data.session_active ? 'Yes' : 'No'}
                </dd>
              </div>
              <div className="flex items-baseline justify-between gap-3">
                <dt className="text-[var(--fg-muted)]">Session ID</dt>
                <dd
                  className="min-w-0 truncate text-right font-mono text-[var(--fs-xs)] text-[var(--fg)] num-tabular"
                  title={status.data.session_id ?? undefined}
                >
                  {status.data.session_id ?? '—'}
                </dd>
              </div>
              <div className="flex items-baseline justify-between gap-3">
                <dt className="text-[var(--fg-muted)]">Turn count</dt>
                <dd className="num-tabular text-[var(--fg)]">
                  {status.data.turn_count}
                </dd>
              </div>
              <div className="flex items-baseline justify-between gap-3">
                <dt className="text-[var(--fg-muted)]">Generated at</dt>
                <dd
                  className="font-mono text-[var(--fs-xs)] text-[var(--fg-muted)] num-tabular"
                  title={status.data.generated_at}
                >
                  {new Date(status.data.generated_at).toLocaleTimeString()}
                </dd>
              </div>
            </dl>
          )}
        </SectionCard>

        <SectionCard title="Subsystems">
          {sessionLoading ? (
            <SkeletonPairs rows={3} />
          ) : sessionError ? (
            <ErrorChip
              message={status.error?.message ?? 'Failed to load subsystems'}
              onRetry={() => status.refetch()}
            />
          ) : status.data?.failed_subsystems.length ? (
            <ul className="flex flex-col gap-2">
              {status.data.failed_subsystems.map((sub) => (
                <li
                  key={sub}
                  className="flex items-center justify-between gap-3 rounded-[var(--r-1)] border-l-[3px] border-l-[var(--danger)] bg-[var(--surface-2)] px-3 py-2 text-[var(--fs-sm)]"
                >
                  <span className="font-mono text-[var(--fs-xs)] text-[var(--fg)]">
                    {sub}
                  </span>
                  <Pill status={SUBSYSTEM_STATUS} label="failed" />
                </li>
              ))}
            </ul>
          ) : (
            <p className="font-narrative text-[var(--fs-md)] text-[var(--fg-muted)]">
              All subsystems nominal.
            </p>
          )}
        </SectionCard>

        <SectionCard
          title="Orchestration"
          trailing={
            <Button
              variant="ghost"
              size="sm"
              onClick={() => status.refetch()}
              aria-label="Refresh orchestration status"
            >
              <RefreshCcw
                className={cn(
                  'h-3.5 w-3.5',
                  status.isFetching && 'opacity-60',
                )}
                strokeWidth={1.5}
              />
            </Button>
          }
        >
          {sessionLoading ? (
            <SkeletonPairs rows={3} />
          ) : sessionError ? (
            <ErrorChip
              message={status.error?.message ?? 'Failed to load orchestration'}
              onRetry={() => status.refetch()}
            />
          ) : !status.data ? (
            <p className="text-[var(--fs-sm)] text-[var(--fg-muted)]">
              Orchestration engine isn't running on this daemon.
            </p>
          ) : (
            <dl className="flex flex-col gap-2.5 text-[var(--fs-sm)]">
              <div className="flex items-baseline justify-between gap-3">
                <dt className="text-[var(--fg-muted)]">Pipelines</dt>
                <dd className="num-tabular text-[var(--fg)]">
                  {status.data.orchestration_pipelines}
                </dd>
              </div>
              <div className="flex items-baseline justify-between gap-3">
                <dt className="text-[var(--fg-muted)]">Session active</dt>
                <dd className="num-tabular text-[var(--fg)]">
                  {status.data.session_active ? 'Yes' : 'No'}
                </dd>
              </div>
              <div className="flex items-baseline justify-between gap-3">
                <dt className="text-[var(--fg-muted)]">Failed subsystems</dt>
                <dd className="num-tabular text-[var(--fg)]">
                  {status.data.failed_subsystems.length}
                </dd>
              </div>
              <div className="flex items-baseline justify-between gap-3">
                <dt className="text-[var(--fg-muted)]">Support mode</dt>
                <dd className="font-mono text-[var(--fs-xs)] text-[var(--fg)] num-tabular">
                  {status.data.support_mode}
                </dd>
              </div>
            </dl>
          )}
        </SectionCard>

        <SectionCard
          title="Vault"
          trailing={
            status.data?.vault.obsidian_facing ? (
              <Badge provenance="workspace">Obsidian</Badge>
            ) : undefined
          }
          className="md:col-span-2"
        >
          {sessionLoading ? (
            <SkeletonPairs rows={3} />
          ) : sessionError ? (
            <ErrorChip
              message={status.error?.message ?? 'Failed to load vault state'}
              onRetry={() => status.refetch()}
            />
          ) : !status.data ? (
            <p className="text-[var(--fs-sm)] text-[var(--fg-muted)]">No data.</p>
          ) : (
            <dl className="flex flex-col gap-2.5 text-[var(--fs-sm)]">
              <div className="flex items-baseline justify-between gap-3">
                <dt className="text-[var(--fg-muted)]">Health</dt>
                <dd>
                  <Pill
                    status={VAULT_HEALTH_TO_PILL[status.data.vault.health] ?? 'unknown'}
                    label={status.data.vault.health}
                  />
                </dd>
              </div>
              <div className="flex items-baseline justify-between gap-3">
                <dt className="text-[var(--fg-muted)]">Path</dt>
                <dd
                  className="min-w-0 truncate text-right font-mono text-[var(--fs-xs)] text-[var(--fg)]"
                  title={status.data.vault.path ?? undefined}
                >
                  {status.data.vault.path ?? '—'}
                </dd>
              </div>
              <div className="flex items-baseline justify-between gap-3">
                <dt className="text-[var(--fg-muted)]">Memory root</dt>
                <dd className="min-w-0 truncate text-right font-mono text-[var(--fs-xs)] text-[var(--fg)]">
                  {status.data.vault.memory_root}
                </dd>
              </div>
              {status.data.vault.message && (
                <p className="text-[var(--fs-sm)] text-[var(--fg-muted)]">
                  {status.data.vault.message}
                </p>
              )}
            </dl>
          )}
        </SectionCard>
      </div>

      {shutdownOpen && (
        <ShutdownDialog open={shutdownOpen} onOpenChange={setShutdownOpen} />
      )}
    </>
  );
}
