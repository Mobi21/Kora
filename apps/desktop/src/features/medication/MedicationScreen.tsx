import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { AlertTriangle, Pill as PillIcon, Settings2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Divider } from '@/components/ui/divider';
import { EmptyState } from '@/components/ui/empty-state';
import { Skeleton } from '@/components/ui/skeleton';
import { ApiError } from '@/lib/api/client';
import type { MedicationDayView } from '@/lib/api/types';
import { useMedicationDay } from './queries';
import { todayIso } from './utils/format';
import { MedicationHeader } from './components/MedicationHeader';
import { DoseRow } from './components/DoseRow';
import { DaySummary } from './components/DaySummary';

const PRIVACY_KEY = 'kora.medication.privacy';
const WORKSPACE_MAX = '880px';

function readPrivacy(): boolean {
  try {
    if (typeof window === 'undefined') return false;
    return window.localStorage.getItem(PRIVACY_KEY) === '1';
  } catch {
    return false;
  }
}

function writePrivacy(next: boolean): void {
  try {
    if (typeof window === 'undefined') return;
    window.localStorage.setItem(PRIVACY_KEY, next ? '1' : '0');
  } catch {
    // ignore — storage is best-effort
  }
}

function MedicationSkeleton(): JSX.Element {
  return (
    <div className="space-y-6" aria-busy="true">
      <div className="flex items-end justify-between gap-4">
        <div className="space-y-2">
          <Skeleton className="h-7 w-40" />
          <Skeleton className="h-4 w-64" />
        </div>
        <Skeleton className="h-9 w-44" />
      </div>
      <div className="space-y-3">
        {[0, 1, 2].map((i) => (
          <div key={i} className="flex items-center gap-4 py-1">
            <Skeleton className="h-7 w-[5.25rem]" />
            <div className="flex-1 space-y-1.5">
              <Skeleton className="h-4 w-1/2" />
              <Skeleton className="h-3 w-1/3" />
            </div>
            <Skeleton className="h-8 w-32" />
          </div>
        ))}
      </div>
    </div>
  );
}

interface ContentProps {
  data: MedicationDayView;
  date: string;
  privacy: boolean;
}

function MedicationContent({
  data,
  date,
  privacy,
}: ContentProps): JSX.Element {
  const navigate = useNavigate();
  const hasSchedule = data.doses.length > 0;
  return (
    <div className="space-y-7">
      {hasSchedule ? (
        <section aria-label="Today's schedule" className="space-y-0">
          {data.doses.map((dose, idx) => (
            <div key={dose.id}>
              {idx > 0 && <Divider />}
              <DoseRow dose={dose} privacy={privacy} />
            </div>
          ))}
        </section>
      ) : (
        <section
          aria-label="Today's schedule"
          className="rounded-[var(--r-2)] border border-dashed border-[var(--border)] bg-[var(--surface-1)] px-6 py-10 text-center"
        >
          <p className="font-narrative text-[var(--fs-xl)] tracking-[var(--track-tight)] text-[var(--fg)]">
            Nothing scheduled today.
          </p>
          <p className="pt-1 text-[var(--fs-sm)] text-[var(--fg-muted)]">
            When a dose is on the calendar, it’ll appear here with its window and pairing.
          </p>
        </section>
      )}

      <DaySummary
        doses={data.doses}
        lastTakenAt={data.last_taken_at}
        healthSignals={data.health_signals}
      />

      <div className="flex items-center justify-between pt-2">
        <button
          type="button"
          className="font-narrative text-[var(--fs-sm)] italic text-[var(--fg-muted)] underline decoration-dotted underline-offset-4 transition-colors hover:text-[var(--fg)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--bg)] rounded-[var(--r-1)] px-1"
          onClick={() => navigate(`/medication?date=${date}&history=1`)}
        >
          View earlier days →
        </button>
        {!data.enabled && (
          <span className="text-[var(--fs-xs)] text-[var(--fg-subtle)]">
            Tracking off
          </span>
        )}
      </div>
    </div>
  );
}

export function MedicationScreen(): JSX.Element {
  const navigate = useNavigate();
  const [date, setDate] = useState<string>(() => todayIso());
  const [privacy, setPrivacy] = useState<boolean>(() => readPrivacy());

  useEffect(() => {
    writePrivacy(privacy);
  }, [privacy]);

  const query = useMedicationDay(date);
  const { data, isLoading, isError, error, refetch, isFetching } = query;

  const errorMessage = useMemo<string | null>(() => {
    if (!isError) return null;
    if (error instanceof ApiError) return `${error.message}`;
    if (error instanceof Error) return error.message;
    return 'Unable to load medication data.';
  }, [isError, error]);

  return (
    <div className="flex h-full w-full justify-center px-6 py-10">
      <div className="w-full" style={{ maxWidth: WORKSPACE_MAX }}>
        <MedicationHeader
          date={date}
          onChangeDate={setDate}
          privacy={privacy}
          onChangePrivacy={setPrivacy}
        />

        {isLoading && <MedicationSkeleton />}

        {!isLoading && isError && (
          <EmptyState
            icon={AlertTriangle}
            title="Couldn’t load medication"
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
            title="Medication tracking isn’t set up."
            description={
              data.message ??
              'Add medications and dosing windows in Settings to see them here.'
            }
            action={
              <Button onClick={() => navigate('/settings#medication')}>
                Open Settings
              </Button>
            }
          />
        )}

        {!isLoading && !isError && data && data.health === 'unavailable' && (
          <EmptyState
            icon={AlertTriangle}
            title="Medication subsystem isn’t responding."
            description={data.message ?? 'The medication manager is unavailable right now.'}
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
            <MedicationContent data={data} date={date} privacy={privacy} />
          </>
        )}

        {!isLoading && !isError && !data && (
          <EmptyState
            icon={PillIcon}
            title="No data yet"
            description="The daemon hasn’t returned a medication view."
          />
        )}
      </div>
    </div>
  );
}

export default MedicationScreen;
