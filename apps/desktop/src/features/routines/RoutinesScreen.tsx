import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { AlertTriangle, ListChecks, Settings2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { EmptyState } from '@/components/ui/empty-state';
import { Skeleton } from '@/components/ui/skeleton';
import { ApiError } from '@/lib/api/client';
import type { RoutineDayView, RoutineRunView } from '@/lib/api/types';
import { useRoutinesApply, useRoutinesDay } from './queries';
import { todayIso } from '@/features/medication/utils/format';
import { RoutinesHeader } from './components/RoutinesHeader';
import { RoutineRunCard } from './components/RoutineRunCard';
import { UpcomingStrip } from './components/UpcomingStrip';

const WORKSPACE_MAX = '880px';

const ACTIVE_STATUSES = new Set<RoutineRunView['status']>([
  'pending',
  'active',
  'paused',
]);

function RoutinesSkeleton(): JSX.Element {
  return (
    <div className="space-y-6" aria-busy="true">
      <div className="flex items-end justify-between gap-4">
        <div className="space-y-2">
          <Skeleton className="h-7 w-32" />
          <Skeleton className="h-4 w-56" />
        </div>
        <Skeleton className="h-9 w-32" />
      </div>
      <div className="rounded-[var(--r-2)] border border-[var(--border)] bg-[var(--surface-1)] p-4">
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <Skeleton className="h-5 w-1/3" />
            <Skeleton className="h-5 w-20" />
          </div>
          <Skeleton className="h-3 w-2/3" />
        </div>
        <div className="mt-4 space-y-2">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="flex gap-2 py-1">
              <Skeleton className="h-2 w-1" />
              <div className="flex-1 space-y-1">
                <Skeleton className="h-3.5 w-1/2" />
                <Skeleton className="h-3 w-3/4" />
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

interface ContentProps {
  data: RoutineDayView;
  date: string;
}

function RoutinesContent({ data, date }: ContentProps): JSX.Element {
  const apply = useRoutinesApply(date);
  const activeRuns = data.runs.filter((r) => ACTIVE_STATUSES.has(r.status));
  const completedRuns = data.runs.filter((r) => !ACTIVE_STATUSES.has(r.status));

  const onApply = (req: Parameters<typeof apply.mutateAsync>[0]) =>
    apply.mutateAsync(req);

  return (
    <div className="space-y-8">
      <section aria-label="Active routines" className="space-y-3">
        <h2 className="text-label">Active</h2>
        {activeRuns.length === 0 ? (
          <p className="font-narrative text-[var(--fs-md)] italic text-[var(--fg-muted)]">
            Nothing running right now.
          </p>
        ) : (
          <div className="space-y-4">
            {activeRuns.map((run) => (
              <RoutineRunCard
                key={run.id}
                run={run}
                onApply={onApply}
                busy={apply.isPending}
              />
            ))}
          </div>
        )}
      </section>

      <section aria-label="Upcoming routines" className="space-y-3">
        <h2 className="text-label">Upcoming</h2>
        <UpcomingStrip runs={data.upcoming} onApply={onApply} busy={apply.isPending} />
      </section>

      {completedRuns.length > 0 && (
        <section aria-label="Earlier today" className="space-y-3">
          <h2 className="text-label">Earlier today</h2>
          <div className="space-y-4">
            {completedRuns.map((run) => (
              <RoutineRunCard
                key={run.id}
                run={run}
                onApply={onApply}
                busy={apply.isPending}
              />
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

export function RoutinesScreen(): JSX.Element {
  const navigate = useNavigate();
  const [date, setDate] = useState<string>(() => todayIso());

  const query = useRoutinesDay(date);
  const { data, isLoading, isError, error, refetch, isFetching } = query;

  const errorMessage = useMemo<string | null>(() => {
    if (!isError) return null;
    if (error instanceof ApiError) return error.message;
    if (error instanceof Error) return error.message;
    return 'Unable to load routines.';
  }, [isError, error]);

  return (
    <div className="flex h-full w-full justify-center px-6 py-10">
      <div className="w-full" style={{ maxWidth: WORKSPACE_MAX }}>
        <RoutinesHeader date={date} onChangeDate={setDate} />

        {isLoading && <RoutinesSkeleton />}

        {!isLoading && isError && (
          <EmptyState
            icon={AlertTriangle}
            title="Couldn’t load routines"
            description={errorMessage ?? 'The daemon returned an error.'}
            action={
              <Button variant="outline" onClick={() => refetch()}>
                Retry
              </Button>
            }
          />
        )}

        {!isLoading && !isError && data && data.health === 'unconfigured' && (
          <EmptyState
            icon={Settings2}
            title="Routines aren’t set up."
            description={
              data.message ??
              'Define your morning, evening, or recovery routines in Settings to see them here.'
            }
            action={
              <Button onClick={() => navigate('/settings#routines')}>
                Open Settings
              </Button>
            }
          />
        )}

        {!isLoading && !isError && data && data.health === 'unavailable' && (
          <EmptyState
            icon={AlertTriangle}
            title="Routines subsystem isn’t responding."
            description={data.message ?? 'The routines manager is unavailable right now.'}
            action={
              <Button variant="outline" onClick={() => refetch()} disabled={isFetching}>
                {isFetching ? 'Retrying…' : 'Retry'}
              </Button>
            }
          />
        )}

        {!isLoading && !isError && data && data.health !== 'unconfigured' && data.health !== 'unavailable' && (
          <>
            {data.health === 'degraded' && data.message && (
              <div className="mb-4 flex items-start gap-2 rounded-[var(--r-1)] border border-[var(--border)] bg-[var(--surface-2)] p-2 text-[var(--fs-xs)] text-[var(--fg-muted)]">
                <AlertTriangle
                  className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[var(--warn)]"
                  strokeWidth={1.75}
                  aria-hidden="true"
                />
                <span>{data.message}</span>
              </div>
            )}
            {data.runs.length === 0 && data.upcoming.length === 0 ? (
              <EmptyState
                icon={ListChecks}
                title="No routines today."
                description="Anchors and guided sequences will appear here when scheduled."
              />
            ) : (
              <RoutinesContent data={data} date={date} />
            )}
          </>
        )}

        {!isLoading && !isError && !data && (
          <EmptyState
            icon={ListChecks}
            title="No data yet"
            description="The daemon hasn’t returned a routine view."
          />
        )}
      </div>
    </div>
  );
}

export default RoutinesScreen;
