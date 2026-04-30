import { useNavigate } from 'react-router-dom';
import { ExternalLink, RefreshCcw, Sparkles, Wrench } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { EmptyState } from '@/components/ui/empty-state';
import { Pill, type PillStatus } from '@/components/ui/pill';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';
import type { InspectSetupReport } from '@/lib/api/types';
import { clearFirstRunCompleted } from '@/features/first-run/queries';
import { useInspectSetupQuery } from '../queries';

interface SetupRowSpec {
  key: string;
  label: string;
  description: string;
  resolve: (report: InspectSetupReport) => {
    value: string;
    status: PillStatus;
    statusLabel: string;
    revealable: boolean;
  };
}

const ROWS: SetupRowSpec[] = [
  {
    key: 'data_dir',
    label: 'Data directory',
    description: 'Where Kora stores SQLite databases, the auth token, and lockfile.',
    resolve: (r) => ({
      value: r.data_dir,
      status: r.data_dir ? 'ok' : 'unknown',
      statusLabel: r.data_dir ? 'configured' : 'unconfigured',
      revealable: !!r.data_dir,
    }),
  },
  {
    key: 'memory_path',
    label: 'Memory root',
    description: 'Filesystem-canonical memory store. Kora indexes it on startup.',
    resolve: (r) => ({
      value: r.memory.path,
      status: r.memory.path ? 'ok' : 'unknown',
      statusLabel: r.memory.path ? 'configured' : 'unconfigured',
      revealable: !!r.memory.path,
    }),
  },
  {
    key: 'token_path',
    label: 'API token',
    description: 'Local-only bearer token used by the desktop app to talk to the daemon.',
    resolve: (r) => ({
      value: r.security.api_token_path,
      status: r.security.token_file_exists
        ? 'ok'
        : r.security.api_token_path
        ? 'degraded'
        : 'unknown',
      statusLabel: r.security.token_file_exists
        ? 'present'
        : r.security.api_token_path
        ? 'missing'
        : 'unconfigured',
      revealable: r.security.token_file_exists,
    }),
  },
  {
    key: 'operational_db',
    label: 'Operational DB',
    description: 'Sessions, traces, telemetry, and permission grants live here.',
    resolve: (r) => ({
      value: r.operational_db.path,
      status: r.operational_db.exists ? 'ok' : 'degraded',
      statusLabel: r.operational_db.exists ? 'present' : 'missing',
      revealable: r.operational_db.exists,
    }),
  },
  {
    key: 'projection_db',
    label: 'Projection DB',
    description: 'Memory projection database — derived from the filesystem store.',
    resolve: (r) => ({
      value: r.projection_db.path,
      status: r.projection_db.exists ? 'ok' : 'warn',
      statusLabel: r.projection_db.exists ? 'present' : 'will rebuild',
      revealable: r.projection_db.exists,
    }),
  },
  {
    key: 'auth_mode',
    label: 'Auth mode',
    description: 'Permission policy: "prompt" requires user approval; "trust_all" auto-grants.',
    resolve: (r) => ({
      value: r.security.auth_mode,
      status: r.security.auth_mode === 'prompt' ? 'ok' : 'warn',
      statusLabel: r.security.auth_mode || 'unconfigured',
      revealable: false,
    }),
  },
  {
    key: 'llm',
    label: 'LLM',
    description: 'Provider, model, and Anthropic-compatible base URL.',
    resolve: (r) => ({
      value: `${r.llm.provider} · ${r.llm.model}`,
      status: r.llm.provider && r.llm.model ? 'ok' : 'unknown',
      statusLabel: r.llm.provider && r.llm.model ? 'configured' : 'unconfigured',
      revealable: false,
    }),
  },
];

function reveal(path: string): void {
  if (typeof window === 'undefined' || !window.kora?.openExternal) return;
  // Best-effort. The bridge accepts URL strings; route filesystem paths via file://.
  const target = path.startsWith('file://') || path.startsWith('http')
    ? path
    : `file://${path}`;
  void window.kora.openExternal(target).catch(() => {
    /* ignore — defensive per spec */
  });
}

function RowSkeleton(): JSX.Element {
  return (
    <div className="flex items-center justify-between gap-4 rounded-[var(--r-2)] border border-[var(--border)] bg-[var(--surface-1)] px-4 py-3">
      <div className="flex-1 space-y-1.5">
        <Skeleton className="h-3.5 w-1/4" />
        <Skeleton className="h-3 w-3/4" />
      </div>
      <Skeleton className="h-5 w-20" />
    </div>
  );
}

export function SetupTab(): JSX.Element {
  const setup = useInspectSetupQuery();
  const navigate = useNavigate();
  const bridgeAvailable = typeof window !== 'undefined' && !!window.kora?.openExternal;

  function openWizard(): void {
    // Re-running the wizard means the user wants to revisit setup; clear
    // the completion flag so the gate doesn't bounce them back immediately.
    clearFirstRunCompleted();
    navigate('/first-run');
  }

  return (
    <section className="space-y-6">
      <header className="flex items-start justify-between gap-3">
        <div className="space-y-1">
          <h2 className="font-narrative text-[var(--fs-2xl)] tracking-[var(--track-tight)] text-[var(--fg)]">
            Setup
          </h2>
          <p className="text-[var(--fs-sm)] text-[var(--fg-muted)]">
            The paths and providers Kora resolved at startup.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setup.refetch()}
            disabled={setup.isFetching}
            aria-label="Refresh setup"
          >
            <RefreshCcw
              className={cn('h-3.5 w-3.5', setup.isFetching && 'opacity-60')}
              strokeWidth={1.5}
            />
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={openWizard}
            aria-label="Open first-run wizard"
          >
            <Sparkles className="h-4 w-4" strokeWidth={1.5} />
            First-run wizard
          </Button>
        </div>
      </header>

      {setup.isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 5 }).map((_, i) => (
            <RowSkeleton key={i} />
          ))}
        </div>
      ) : setup.isError ? (
        <EmptyState
          icon={Wrench}
          title="Setup isn't available"
          description="This subsystem isn't implemented in the current daemon, or the call failed. Refreshing may help."
          action={
            <Button onClick={() => setup.refetch()}>
              <RefreshCcw className="h-4 w-4" strokeWidth={1.5} />
              Refresh
            </Button>
          }
        />
      ) : !setup.data ? (
        <EmptyState
          icon={Wrench}
          title="No setup reported"
          description="The daemon returned an empty response."
        />
      ) : (
        <div className="space-y-2">
          {ROWS.map((row) => {
            const r = row.resolve(setup.data!);
            return (
              <div
                key={row.key}
                className="flex items-center justify-between gap-4 rounded-[var(--r-2)] border border-[var(--border)] bg-[var(--surface-1)] px-4 py-3"
              >
                <div className="min-w-0 flex-1 space-y-1">
                  <div className="flex items-center gap-2">
                    <p className="text-[var(--fs-base)] font-medium text-[var(--fg)]">
                      {row.label}
                    </p>
                    <span className="font-mono text-[var(--fs-2xs)] uppercase tracking-[var(--track-label)] text-[var(--fg-subtle)]">
                      {row.key}
                    </span>
                  </div>
                  <p className="text-[var(--fs-sm)] text-[var(--fg-muted)]">
                    {row.description}
                  </p>
                  <p
                    className="truncate font-mono text-[var(--fs-xs)] text-[var(--fg)]"
                    title={r.value || undefined}
                  >
                    {r.value || '—'}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <Pill status={r.status} label={r.statusLabel} />
                  {r.revealable && bridgeAvailable && (
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() => reveal(r.value)}
                          aria-label={`Reveal ${row.label} in Finder`}
                        >
                          <ExternalLink
                            className="h-4 w-4"
                            strokeWidth={1.5}
                          />
                        </Button>
                      </TooltipTrigger>
                      <TooltipContent>Reveal</TooltipContent>
                    </Tooltip>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

    </section>
  );
}
