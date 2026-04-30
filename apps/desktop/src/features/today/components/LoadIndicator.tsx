import { Pill } from '@/components/ui/pill';
import { cn } from '@/lib/utils';
import type { LoadState } from '@/lib/api/types';
import { loadBandLabel, loadBandToPill } from '../queries';

interface LoadIndicatorProps {
  load: LoadState;
  supportMode: string;
  className?: string;
}

/**
 * Right-aligned load read-out.
 * Stacked top-down: band pill, support-mode badge, optional 0–100 score
 * with a thin progress meter (rendered only when confidence > 0).
 */
export function LoadIndicator({
  load,
  supportMode,
  className,
}: LoadIndicatorProps): JSX.Element {
  const pillStatus = loadBandToPill(load.band);
  const bandLabel = loadBandLabel(load.band);
  const confidence = load.confidence ?? 0;
  const score = load.score;
  const supportModeLabel = formatSupportMode(supportMode);

  const showScore = confidence > 0 && typeof score === 'number';
  const clampedScore = showScore ? Math.max(0, Math.min(100, score!)) : null;

  return (
    <div className={cn('flex flex-col items-end gap-1.5', className)}>
      <Pill status={pillStatus} label={bandLabel}>
        {bandLabel}
      </Pill>
      <div
        className={cn(
          'inline-flex items-center gap-1.5 rounded-[var(--r-pill)]',
          'border border-[var(--border)] bg-[var(--surface-2)] px-2 py-0.5',
          'text-[var(--fs-2xs)] uppercase tracking-[var(--track-label)] text-[var(--fg-muted)]',
        )}
      >
        <span aria-hidden>support</span>
        <span className="text-[var(--fg)] normal-case tracking-normal">{supportModeLabel}</span>
      </div>
      {clampedScore !== null && (
        <div
          className="flex w-32 flex-col items-end gap-1"
          aria-label={`Load score ${clampedScore} out of 100`}
        >
          <span className="font-mono text-[var(--fs-xs)] text-[var(--fg-muted)] num-tabular">
            {clampedScore}/100
          </span>
          <div
            className="h-1 w-full overflow-hidden rounded-[var(--r-pill)] bg-[var(--surface-2)]"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={clampedScore}
          >
            <div
              className="h-full rounded-[var(--r-pill)] transition-[width]"
              style={{
                width: `${clampedScore}%`,
                background: 'var(--accent-soft)',
              }}
            />
          </div>
        </div>
      )}
    </div>
  );
}

function formatSupportMode(mode: string): string {
  if (!mode) return 'unknown';
  return mode
    .split(/[_\s-]+/)
    .filter(Boolean)
    .map((part) => part[0]!.toUpperCase() + part.slice(1))
    .join(' ');
}
