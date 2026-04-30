import { ChevronLeft, ChevronRight } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  dayOffsetFromToday,
  formatDayLabel,
  shiftIsoDate,
} from '@/features/medication/utils/format';

interface RoutinesHeaderProps {
  date: string;
  onChangeDate: (iso: string) => void;
}

const MAX_OFFSET = 7;

export function RoutinesHeader({ date, onChangeDate }: RoutinesHeaderProps): JSX.Element {
  const offset = dayOffsetFromToday(date);
  const canPrev = offset > -MAX_OFFSET;
  const canNext = offset < MAX_OFFSET;

  return (
    <header className="flex flex-col gap-3 pb-6 sm:flex-row sm:items-end sm:justify-between">
      <div className="space-y-1">
        <h1
          className="font-narrative text-[var(--fs-3xl)] tracking-[var(--track-tight)] text-[var(--fg)]"
          style={{ lineHeight: 1.15 }}
        >
          Routines
        </h1>
        <p className="font-narrative text-[var(--fs-md)] italic text-[var(--fg-muted)]">
          Anchors and guided sequences.
        </p>
      </div>
      <div
        className="flex items-center gap-1 self-start rounded-[var(--r-2)] border border-[var(--border)] bg-[var(--surface-1)] p-0.5 sm:self-auto"
        role="group"
        aria-label="Date stepper"
      >
        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7"
          disabled={!canPrev}
          aria-label="Previous day"
          onClick={() => onChangeDate(shiftIsoDate(date, -1))}
        >
          <ChevronLeft className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
        </Button>
        <span
          className="font-mono num-tabular px-2 text-[var(--fs-xs)] text-[var(--fg-muted)]"
          aria-live="polite"
        >
          {formatDayLabel(date)}
        </span>
        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7"
          disabled={!canNext}
          aria-label="Next day"
          onClick={() => onChangeDate(shiftIsoDate(date, 1))}
        >
          <ChevronRight className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
        </Button>
      </div>
    </header>
  );
}
