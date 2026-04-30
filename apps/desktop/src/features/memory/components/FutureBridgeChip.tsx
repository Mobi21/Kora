import type { FutureBridgeSummary } from '@/lib/api/types';
import { ArrowUpRight } from 'lucide-react';
import { cn } from '@/lib/utils';
import { formatRelativeOr, softTrim } from '../utils/format';

interface FutureBridgeChipProps {
  bridge: FutureBridgeSummary;
  onOpen: (bridge: FutureBridgeSummary) => void;
}

const CHIP_CLASS = cn(
  'group/chip flex w-72 shrink-0 flex-col gap-2 rounded-[var(--r-2)]',
  'border border-[var(--border)] bg-[var(--surface-1)] p-3 text-left',
  'transition-colors duration-[var(--motion-fast)] ease-[var(--ease-out)]',
);

export function FutureBridgeChip({ bridge, onOpen }: FutureBridgeChipProps): JSX.Element {
  const canOpen = !!bridge.artifact_path;

  const inner = (
    <>
      <p
        className="font-narrative text-[var(--fs-sm)] italic text-[var(--fg)] overflow-hidden"
        style={{
          display: '-webkit-box',
          WebkitBoxOrient: 'vertical',
          WebkitLineClamp: 2,
        }}
      >
        {softTrim(bridge.summary)}
      </p>
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono num-tabular text-[var(--fs-2xs)] text-[var(--fg-subtle)]">
          {formatRelativeOr(bridge.to_date, '—')}
        </span>
        {canOpen && (
          <span
            className={cn(
              'inline-flex items-center gap-1 text-[var(--fs-2xs)] text-[var(--fg-muted)]',
              'group-hover/chip:text-[var(--fg)]',
            )}
          >
            Open <ArrowUpRight className="h-3 w-3" strokeWidth={1.5} aria-hidden />
          </span>
        )}
      </div>
    </>
  );

  if (canOpen) {
    return (
      <button
        type="button"
        onClick={() => onOpen(bridge)}
        aria-label={`Open future bridge ${bridge.id}`}
        className={cn(
          CHIP_CLASS,
          'hover:border-[var(--border-strong)] hover:bg-[var(--surface-2)]',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]',
        )}
      >
        {inner}
      </button>
    );
  }
  return <div className={cn(CHIP_CLASS, 'opacity-80')}>{inner}</div>;
}
