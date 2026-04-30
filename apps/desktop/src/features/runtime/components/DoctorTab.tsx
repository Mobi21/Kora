import { useMemo } from 'react';
import { AlertCircle, RefreshCcw, Stethoscope } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { EmptyState } from '@/components/ui/empty-state';
import { Pill } from '@/components/ui/pill';
import { cn } from '@/lib/utils';
import type { InspectDoctorCheck } from '@/lib/api/types';
import { useInspectDoctorQuery } from '../queries';
import { CheckRow } from './CheckRow';

const CORE_PREFIXES = [
  'operational_db',
  'api_token_file',
  'daemon_localhost_binding',
  'cors_not_wildcard',
  'planner_initialized',
  'executor_initialized',
  'reviewer_initialized',
  'sqlite_checkpointer',
  'module_',
  'python_version_ok',
  'pysqlite3_swap',
];

function partition(checks: InspectDoctorCheck[]) {
  const core: InspectDoctorCheck[] = [];
  const optional: InspectDoctorCheck[] = [];
  for (const check of checks) {
    const isCore = CORE_PREFIXES.some((p) => check.name.startsWith(p));
    (isCore ? core : optional).push(check);
  }
  return { core, optional };
}

function CheckSkeleton(): JSX.Element {
  return (
    <div className="rounded-[var(--r-2)] border-l-[3px] border-l-[var(--border)] pl-3 pr-2 py-2.5">
      <div className="flex items-center gap-3">
        <div className="flex-1 space-y-1">
          <Skeleton className="h-3.5 w-2/5" />
          <Skeleton className="h-3 w-3/5" />
        </div>
        <Skeleton className="h-5 w-12" />
      </div>
    </div>
  );
}

function SectionHeading({
  title,
  description,
  count,
}: {
  title: string;
  description: string;
  count: number;
}): JSX.Element {
  return (
    <div className="mb-3 flex items-baseline justify-between gap-3">
      <div>
        <h3 className="font-narrative text-[var(--fs-xl)] text-[var(--fg)]">
          {title}
        </h3>
        <p className="text-[var(--fs-sm)] text-[var(--fg-muted)]">
          {description}
        </p>
      </div>
      <span className="font-mono text-[var(--fs-xs)] text-[var(--fg-muted)] num-tabular">
        {count}
      </span>
    </div>
  );
}

export function DoctorTab(): JSX.Element {
  const doctor = useInspectDoctorQuery();

  const partitioned = useMemo(() => {
    if (!doctor.data) return { core: [], optional: [] };
    return partition(doctor.data.checks);
  }, [doctor.data]);

  const summaryPill = doctor.data ? (
    <Pill
      status={doctor.data.healthy ? 'ok' : 'warn'}
      label={doctor.data.healthy ? 'all checks pass' : doctor.data.summary}
    >
      {doctor.data.summary}
    </Pill>
  ) : null;

  return (
    <section className="space-y-6">
      <header className="flex items-start justify-between gap-3">
        <div className="space-y-1">
          <h2 className="font-narrative text-[var(--fs-2xl)] tracking-[var(--track-tight)] text-[var(--fg)]">
            Health checks
          </h2>
          <p className="text-[var(--fs-sm)] text-[var(--fg-muted)]">
            Read-only diagnostics from the running daemon. Failures here are
            real and worth investigating; warnings on optional checks are fine.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {summaryPill}
          <Button
            variant="ghost"
            size="sm"
            onClick={() => doctor.refetch()}
            disabled={doctor.isFetching}
            aria-label="Re-run doctor checks"
          >
            <RefreshCcw
              className={cn('h-3.5 w-3.5', doctor.isFetching && 'opacity-60')}
              strokeWidth={1.5}
            />
            Re-run checks
          </Button>
        </div>
      </header>

      {doctor.isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 6 }).map((_, i) => (
            <CheckSkeleton key={i} />
          ))}
        </div>
      ) : doctor.isError ? (
        <EmptyState
          icon={Stethoscope}
          title="Doctor isn't available"
          description="This subsystem isn't implemented in the current daemon, or the call failed. Try refreshing — if it keeps failing, the install may need an update."
          action={
            <Button onClick={() => doctor.refetch()} aria-label="Retry doctor checks">
              <RefreshCcw className="h-4 w-4" strokeWidth={1.5} />
              Refresh
            </Button>
          }
        />
      ) : !doctor.data || doctor.data.checks.length === 0 ? (
        <EmptyState
          icon={AlertCircle}
          title="No checks reported"
          description="The daemon ran but returned no health checks. The doctor module may be partially wired."
          action={
            <Button onClick={() => doctor.refetch()}>
              <RefreshCcw className="h-4 w-4" strokeWidth={1.5} />
              Refresh
            </Button>
          }
        />
      ) : (
        <div className="space-y-8">
          <div>
            <SectionHeading
              title="Core"
              description="Database, security, and worker fundamentals. These should all pass."
              count={partitioned.core.length}
            />
            {partitioned.core.length === 0 ? (
              <p className="text-[var(--fs-sm)] text-[var(--fg-muted)]">
                No core checks reported.
              </p>
            ) : (
              <div className="space-y-1">
                {partitioned.core.map((c) => (
                  <CheckRow key={c.name} check={c} />
                ))}
              </div>
            )}
          </div>

          {partitioned.optional.length > 0 && (
            <div>
              <SectionHeading
                title="Optional"
                description="Capability packs, MCP servers, and embeddings. Failures here degrade specific features but don't stop Kora."
                count={partitioned.optional.length}
              />
              <div className="space-y-1">
                {partitioned.optional.map((c) => (
                  <CheckRow key={c.name} check={c} />
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
