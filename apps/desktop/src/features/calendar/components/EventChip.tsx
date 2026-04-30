import { useMemo } from 'react';
import { ProvenanceDot } from '@/components/ui/provenance-dot';
import { cn } from '@/lib/utils';
import { formatTime } from '@/lib/dates';
import type { CalendarEventView } from '@/lib/api/types';
import { PROV_VARS, pickProvenance } from '../utils/provenance';

export interface EventChipProps {
  event: CalendarEventView;
  compact?: boolean;
  onMove?: (id: string) => void;
  onCancel?: (id: string) => void;
  onView?: (id: string) => void;
}

export function EventChip({
  event,
  compact = false,
  onMove,
  onCancel,
  onView,
}: EventChipProps): JSX.Element {
  const prov = useMemo(() => pickProvenance(event.provenance), [event.provenance]);
  const provVar = PROV_VARS[prov];

  const timeLabel = useMemo(() => {
    if (event.all_day) return 'All day';
    const start = formatTime(event.starts_at);
    if (!event.ends_at) return start;
    return `${start} – ${formatTime(event.ends_at)}`;
  }, [event.starts_at, event.ends_at, event.all_day]);

  return (
    <div
      className={cn(
        'kora-event-chip group flex h-full w-full flex-col overflow-hidden',
        'rounded-[6px] border-l-[4px]',
        'transition-shadow duration-[var(--motion-fast)] ease-[var(--ease-out)]',
        compact ? 'gap-0 px-2 py-0.5' : 'gap-0.5 px-2 py-1',
      )}
      style={{
        background: `color-mix(in oklch, ${provVar} 8%, var(--surface-1))`,
        borderLeftColor: provVar,
      }}
    >
      <div className="flex min-w-0 items-center gap-1.5">
        <span
          className={cn(
            'min-w-0 flex-1 truncate text-[var(--fg)]',
            compact
              ? 'text-[var(--fs-xs)] font-medium'
              : 'text-[var(--fs-sm)] font-medium',
          )}
        >
          {event.title || 'Untitled'}
        </span>
        <ProvenanceDot kind={prov} aria-hidden />
      </div>
      <div
        className={cn(
          'font-mono num-tabular text-[var(--fs-2xs)] text-[var(--fg-muted)]',
          'truncate',
        )}
      >
        {timeLabel}
      </div>
      {!compact && (onView || onMove || onCancel) && (
        <div
          className={cn(
            'kora-event-chip-actions mt-auto flex items-center gap-1 pt-1',
            'text-[var(--fs-2xs)] text-[var(--fg-muted)]',
          )}
        >
          {onView && (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                onView(event.id);
              }}
              className={cn(
                'rounded-[var(--r-1)] px-1.5 py-0.5 hover:bg-[var(--surface-2)]',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]',
              )}
              aria-label={`View ${event.title}`}
            >
              View
            </button>
          )}
          {onMove && (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                onMove(event.id);
              }}
              className={cn(
                'rounded-[var(--r-1)] px-1.5 py-0.5 hover:bg-[var(--surface-2)]',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]',
              )}
              aria-label={`Move ${event.title}`}
            >
              Move
            </button>
          )}
          {onCancel && (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                onCancel(event.id);
              }}
              className={cn(
                'rounded-[var(--r-1)] px-1.5 py-0.5 hover:bg-[var(--surface-2)]',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]',
              )}
              aria-label={`Cancel ${event.title}`}
            >
              Cancel
            </button>
          )}
        </div>
      )}
    </div>
  );
}
