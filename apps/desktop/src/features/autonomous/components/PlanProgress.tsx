import { cn } from '@/lib/utils';
import { clampProgress, formatLastActivity, useTickingNow } from '../queries';

interface PlanProgressProps {
  progress: number;
  completedSteps: number;
  totalSteps: number;
  currentStep: string | null;
  lastActivityAt: string | null;
}

/**
 * Hairline progress bar + tabular step counter + "Now: …" caption.
 *
 * The bar itself is a 1px-outlined rail filled with `--accent-soft` to a 4px
 * height; the indeterminate / running state is conveyed by the surrounding
 * status pill (we explicitly avoid spinners).
 */
export function PlanProgress({
  progress,
  completedSteps,
  totalSteps,
  currentStep,
  lastActivityAt,
}: PlanProgressProps): JSX.Element {
  const value = clampProgress(progress);
  const pct = Math.round(value * 100);
  const now = useTickingNow(15_000);
  const activityLabel = formatLastActivity(lastActivityAt, now);

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-3">
        <div
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={pct}
          aria-label={`Progress: ${pct}%`}
          className={cn(
            'relative h-1 flex-1 overflow-hidden rounded-[var(--r-pill)]',
            'border border-[var(--border)] bg-[var(--surface-2)]',
          )}
        >
          <span
            aria-hidden
            className="block h-full rounded-[var(--r-pill)] bg-[var(--accent-soft)]"
            style={{
              width: `${pct}%`,
              transition: 'width var(--motion) var(--ease-out)',
            }}
          />
        </div>
        <span className="font-mono num-tabular text-[var(--fs-xs)] text-[var(--fg-muted)]">
          {completedSteps} / {totalSteps}
        </span>
      </div>

      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <p
          className="font-narrative italic text-[var(--fg-muted)]"
          style={{ fontSize: '0.875rem', lineHeight: 1.45 }}
        >
          {currentStep ? <>Now: {currentStep}</> : <>Awaiting next step.</>}
        </p>
        <span className="font-mono num-tabular text-[var(--fs-xs)] text-[var(--fg-subtle)]">
          {activityLabel}
        </span>
      </div>
    </div>
  );
}
