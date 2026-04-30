import { useCallback, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { AlertTriangle, RefreshCw, Sparkles, Wrench } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { EmptyState } from '@/components/ui/empty-state';
import { Skeleton } from '@/components/ui/skeleton';
import { ApiError } from '@/lib/api/client';
import { isoDate } from '@/lib/dates';
import { cn } from '@/lib/utils';
import type { RepairStateView } from '@/lib/api/types';
import { CHANGE_TYPES, useRepairState } from './queries';
import { GuidedFlow } from './components/GuidedFlow';
import { RepairBoard } from './components/RepairBoard';
import { RepairHeader } from './components/RepairHeader';

type Mode = 'guided' | 'board';

const REPAIR_MAX_WIDTH = '960px';

function describeError(err: unknown): string {
  if (err instanceof ApiError) {
    return `Daemon returned ${err.status}. ${err.body || 'No details available.'}`;
  }
  if (err instanceof Error) return err.message;
  return 'Could not load repair state.';
}

function isStateEmpty(state: RepairStateView): boolean {
  return (
    !state.day_plan_id &&
    state.broken_or_at_risk.length === 0 &&
    state.suggested_repairs.length === 0 &&
    state.protected_commitments.length === 0 &&
    state.flexible_items.length === 0 &&
    state.move_to_tomorrow.length === 0
  );
}

function GuidedSkeleton(): JSX.Element {
  return (
    <div className="mx-auto w-full" style={{ maxWidth: '720px' }}>
      <div className="flex flex-col gap-6">
        <Skeleton className="h-8 w-2/3" />
        <div className="flex flex-wrap gap-2">
          {Array.from({ length: 6 }).map((_, idx) => (
            <Skeleton key={idx} className="h-10 w-40" />
          ))}
        </div>
        <Skeleton className="h-9 w-full" />
        <div className="flex justify-end">
          <Skeleton className="h-11 w-56" />
        </div>
      </div>
    </div>
  );
}

function BoardSkeleton(): JSX.Element {
  return (
    <div className="flex gap-5 overflow-hidden">
      {Array.from({ length: 5 }).map((_, col) => (
        <div key={col} className="flex w-[280px] shrink-0 flex-col gap-3">
          <div className="flex items-center justify-between">
            <Skeleton className="h-5 w-32" />
            <Skeleton className="h-5 w-6 rounded-[var(--r-pill)]" />
          </div>
          {Array.from({ length: 3 }).map((__, i) => (
            <Skeleton key={i} className="h-16 w-full" />
          ))}
        </div>
      ))}
    </div>
  );
}

interface ErrorPanelProps {
  message: string;
  onRetry: () => void;
}

function ErrorPanel({ message, onRetry }: ErrorPanelProps): JSX.Element {
  return (
    <div
      role="alert"
      className={cn(
        'flex flex-col items-start gap-3 rounded-[var(--r-2)] border border-[var(--border)]',
        'bg-[color-mix(in_oklch,var(--danger)_10%,var(--surface-1))] p-4',
      )}
    >
      <div className="flex items-start gap-2">
        <AlertTriangle
          className="mt-0.5 h-4 w-4 shrink-0 text-[var(--danger)]"
          strokeWidth={1.5}
          aria-hidden
        />
        <div className="flex flex-col gap-1">
          <p className="font-narrative text-[var(--fs-md)] text-[var(--fg)]">
            Repair state is unavailable.
          </p>
          <p className="text-[var(--fs-sm)] text-[var(--fg-muted)]">{message}</p>
        </div>
      </div>
      <Button variant="outline" size="sm" onClick={onRetry}>
        <RefreshCw className="h-3.5 w-3.5" strokeWidth={1.5} aria-hidden />
        Try again
      </Button>
    </div>
  );
}

export function RepairScreen(): JSX.Element {
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();

  const initialReason = searchParams.get('reason');
  const reasonValid = useMemo(
    () => CHANGE_TYPES.some((c) => c.id === initialReason),
    [initialReason],
  );

  const [mode, setMode] = useState<Mode>(reasonValid ? 'guided' : 'board');
  const [date, setDate] = useState<string>(() => isoDate());

  const stateQuery = useRepairState(date);

  const handleApplied = useCallback(
    (count: number) => {
      navigate(`/today?repair_applied=${encodeURIComponent(String(count))}`);
    },
    [navigate],
  );

  const handleModeChange = useCallback(
    (next: Mode) => {
      setMode(next);
      if (next !== 'guided' && searchParams.has('reason')) {
        const cleared = new URLSearchParams(searchParams);
        cleared.delete('reason');
        setSearchParams(cleared, { replace: true });
      }
    },
    [searchParams, setSearchParams],
  );

  return (
    <div className="flex h-full w-full justify-center overflow-y-auto">
      <div
        className="w-full px-6 py-8"
        style={{ maxWidth: REPAIR_MAX_WIDTH }}
      >
        <RepairHeader
          date={date}
          onDateChange={setDate}
          mode={mode}
          onModeChange={handleModeChange}
        />

        <div className="mt-8">
          {stateQuery.isLoading ? (
            mode === 'guided' ? <GuidedSkeleton /> : <BoardSkeleton />
          ) : stateQuery.isError ? (
            <ErrorPanel
              message={describeError(stateQuery.error)}
              onRetry={() => {
                void stateQuery.refetch();
              }}
            />
          ) : stateQuery.data && isStateEmpty(stateQuery.data) && mode === 'board' ? (
            <EmptyState
              icon={Wrench}
              title="No plan to repair today."
              description="When today has scheduled work, the board will fill up with what's protected, what's flexible, and what could move."
              action={
                <Button variant="ghost" onClick={() => navigate('/today')}>
                  <Sparkles className="h-4 w-4" strokeWidth={1.5} aria-hidden />
                  Open Today
                </Button>
              }
            />
          ) : mode === 'guided' ? (
            <GuidedFlow
              date={date}
              initialReason={reasonValid ? initialReason : null}
              onApplied={handleApplied}
            />
          ) : stateQuery.data ? (
            <RepairBoard
              state={stateQuery.data}
              date={date}
              onApplied={handleApplied}
            />
          ) : null}
        </div>
      </div>
    </div>
  );
}

export default RepairScreen;
