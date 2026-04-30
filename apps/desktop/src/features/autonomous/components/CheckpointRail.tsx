import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import type { AutonomousCheckpointView } from '@/lib/api/types';
import { cn } from '@/lib/utils';
import { formatTimestamp } from '../queries';

interface CheckpointRailProps {
  checkpoints: readonly AutonomousCheckpointView[];
  expanded?: boolean;
}

const STATUS_LABEL: Record<AutonomousCheckpointView['status'], string> = {
  passed: 'Passed',
  pending: 'Pending',
  failed: 'Failed',
};

/**
 * Horizontal rail of shape-coded dots — passed (filled circle), pending
 * (hollow circle), failed (filled triangle, danger). The same data also
 * powers the "Show plan history" disclosure as a vertical list.
 */
export function CheckpointRail({
  checkpoints,
  expanded = false,
}: CheckpointRailProps): JSX.Element {
  if (checkpoints.length === 0) {
    return (
      <p className="text-[var(--fs-xs)] text-[var(--fg-subtle)]">
        No checkpoints recorded yet.
      </p>
    );
  }
  if (expanded) {
    return (
      <ol
        aria-label="Checkpoint history"
        className="flex flex-col gap-2 border-l border-[var(--border)] pl-3"
      >
        {checkpoints.map((checkpoint) => (
          <li key={checkpoint.id} className="flex items-start gap-3">
            <span className="mt-0.5">
              <CheckpointShape status={checkpoint.status} />
            </span>
            <div className="flex min-w-0 flex-col gap-0.5">
              <div className="flex items-baseline gap-2">
                <span className="text-[var(--fs-sm)] text-[var(--fg)]">
                  {checkpoint.label}
                </span>
                <span className="font-mono num-tabular text-[var(--fs-2xs)] text-[var(--fg-subtle)] uppercase tracking-[var(--track-label)]">
                  {STATUS_LABEL[checkpoint.status]}
                </span>
              </div>
              <span className="font-mono num-tabular text-[var(--fs-xs)] text-[var(--fg-subtle)]">
                {formatTimestamp(checkpoint.occurred_at)}
              </span>
              {checkpoint.summary && (
                <p className="text-[var(--fs-xs)] text-[var(--fg-muted)]">
                  {checkpoint.summary}
                </p>
              )}
            </div>
          </li>
        ))}
      </ol>
    );
  }
  return (
    <ul
      aria-label="Checkpoints"
      className="flex flex-wrap items-center gap-1.5"
    >
      {checkpoints.map((checkpoint) => (
        <CheckpointDot key={checkpoint.id} checkpoint={checkpoint} />
      ))}
    </ul>
  );
}

interface CheckpointDotProps {
  checkpoint: AutonomousCheckpointView;
}

function CheckpointDot({ checkpoint }: CheckpointDotProps): JSX.Element {
  return (
    <li className="inline-flex">
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            className={cn(
              'inline-flex h-5 w-5 items-center justify-center rounded-full',
              'transition-colors duration-[var(--motion-fast)] ease-[var(--ease-out)]',
              'hover:bg-[var(--surface-2)]',
              'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]',
            )}
            aria-label={`${checkpoint.label}: ${STATUS_LABEL[checkpoint.status]}`}
          >
            <CheckpointShape status={checkpoint.status} />
          </button>
        </TooltipTrigger>
        <TooltipContent side="top" className="max-w-[18rem]">
          <div className="flex flex-col gap-1">
            <div className="flex items-baseline gap-2">
              <span className="text-[var(--fs-xs)] text-[var(--fg)]">
                {checkpoint.label}
              </span>
              <span className="font-mono num-tabular text-[var(--fs-2xs)] uppercase tracking-[var(--track-label)] text-[var(--fg-subtle)]">
                {STATUS_LABEL[checkpoint.status]}
              </span>
            </div>
            <span className="font-mono num-tabular text-[var(--fs-2xs)] text-[var(--fg-subtle)]">
              {formatTimestamp(checkpoint.occurred_at)}
            </span>
            {checkpoint.summary && (
              <p className="text-[var(--fs-2xs)] text-[var(--fg-muted)]">
                {checkpoint.summary}
              </p>
            )}
          </div>
        </TooltipContent>
      </Tooltip>
    </li>
  );
}

interface CheckpointShapeProps {
  status: AutonomousCheckpointView['status'];
}

/** Shape-coded checkpoint glyph — circle / hollow circle / triangle. */
function CheckpointShape({ status }: CheckpointShapeProps): JSX.Element {
  if (status === 'passed') {
    return (
      <svg aria-hidden width="10" height="10" viewBox="0 0 10 10">
        <circle cx="5" cy="5" r="4" fill="var(--ok)" />
      </svg>
    );
  }
  if (status === 'failed') {
    return (
      <svg aria-hidden width="10" height="10" viewBox="0 0 10 10">
        <polygon points="5,1 9,9 1,9" fill="var(--danger)" />
      </svg>
    );
  }
  return (
    <svg aria-hidden width="10" height="10" viewBox="0 0 10 10">
      <circle
        cx="5"
        cy="5"
        r="3.5"
        fill="none"
        stroke="var(--fg-subtle)"
        strokeWidth="1"
      />
    </svg>
  );
}
