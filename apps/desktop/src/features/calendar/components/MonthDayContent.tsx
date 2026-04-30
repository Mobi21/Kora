import { useMemo } from 'react';
import { cn } from '@/lib/utils';
import { PROV_VARS, pickProvenance } from '../utils/provenance';
import type { CalendarEventView } from '@/lib/api/types';

const MAX_DOTS = 4;

export interface MonthDayContentProps {
  date: Date;
  dayNumberText: string;
  isOther: boolean;
  isToday: boolean;
  events: CalendarEventView[];
  onSelect?: (eventId: string) => void;
}

function isSameLocalDay(iso: string, ref: Date): boolean {
  const d = new Date(iso);
  return (
    d.getFullYear() === ref.getFullYear() &&
    d.getMonth() === ref.getMonth() &&
    d.getDate() === ref.getDate()
  );
}

export function MonthDayContent({
  date,
  dayNumberText,
  isOther,
  isToday,
  events,
  onSelect,
}: MonthDayContentProps): JSX.Element {
  const dayEvents = useMemo(
    () => events.filter((e) => isSameLocalDay(e.starts_at, date)),
    [events, date],
  );
  const visible = dayEvents.slice(0, MAX_DOTS);
  const overflow = dayEvents.length - visible.length;

  return (
    <div
      className={cn(
        'flex h-full w-full flex-col gap-1 px-2 py-1.5',
        isOther && 'opacity-60',
      )}
    >
      <div
        className={cn(
          'inline-flex items-center self-start font-mono num-tabular',
          'text-[var(--fs-xs)]',
          isToday
            ? 'rounded-full bg-[var(--accent)] px-1.5 py-0 text-[var(--accent-fg)]'
            : 'text-[var(--fg-muted)]',
        )}
      >
        {dayNumberText}
      </div>
      <div className="flex flex-wrap items-center gap-1">
        {visible.map((ev) => {
          const prov = pickProvenance(ev.provenance);
          return (
            <button
              key={ev.id}
              type="button"
              onClick={() => onSelect?.(ev.id)}
              aria-label={ev.title || 'Event'}
              title={ev.title}
              className={cn(
                'h-1.5 w-1.5 rounded-full transition-transform',
                'duration-[var(--motion-fast)] ease-[var(--ease-out)]',
                'hover:scale-150 focus-visible:outline-none focus-visible:ring-2',
                'focus-visible:ring-[var(--accent)]',
              )}
              style={{ background: PROV_VARS[prov] }}
            />
          );
        })}
        {overflow > 0 && (
          <span className="font-mono num-tabular text-[var(--fs-2xs)] text-[var(--fg-muted)]">
            +{overflow}
          </span>
        )}
      </div>
    </div>
  );
}
