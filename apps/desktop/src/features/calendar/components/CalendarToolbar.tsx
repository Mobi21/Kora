import { ChevronLeft, ChevronRight, CircleDot, Sparkles } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';
import type { CalendarView, VisibleRange } from '../utils/range';
import { formatRangeLabel } from '../utils/range';

export interface CalendarToolbarProps {
  view: CalendarView;
  range: VisibleRange;
  onPrev: () => void;
  onNext: () => void;
  onToday: () => void;
  onViewChange: (view: CalendarView) => void;
  onShowMeWhy: () => void;
  showMeWhyActive: boolean;
}

export function CalendarToolbar({
  view,
  range,
  onPrev,
  onNext,
  onToday,
  onViewChange,
  onShowMeWhy,
  showMeWhyActive,
}: CalendarToolbarProps): JSX.Element {
  const label = formatRangeLabel(view, range);
  return (
    <div
      className={cn(
        'flex items-center justify-between gap-4 border-b border-[var(--border)]',
        'bg-[var(--bg)] px-4 py-3',
      )}
    >
      <div className="flex min-w-0 items-center gap-3">
        <h1
          className={cn(
            'font-narrative text-[var(--fs-2xl)] tracking-[var(--track-tight)]',
            'truncate text-[var(--fg)]',
          )}
        >
          {label}
        </h1>
        <div className="flex items-center gap-1">
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                onClick={onPrev}
                aria-label="Previous period"
                className="h-8 w-8"
              >
                <ChevronLeft className="h-4 w-4" strokeWidth={1.5} />
              </Button>
            </TooltipTrigger>
            <TooltipContent>Previous (←)</TooltipContent>
          </Tooltip>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                onClick={onToday}
                aria-label="Jump to today"
                className="h-8 w-8"
              >
                <CircleDot className="h-4 w-4" strokeWidth={1.5} />
              </Button>
            </TooltipTrigger>
            <TooltipContent>Today (T)</TooltipContent>
          </Tooltip>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                onClick={onNext}
                aria-label="Next period"
                className="h-8 w-8"
              >
                <ChevronRight className="h-4 w-4" strokeWidth={1.5} />
              </Button>
            </TooltipTrigger>
            <TooltipContent>Next (→)</TooltipContent>
          </Tooltip>
        </div>
      </div>

      <div className="flex items-center gap-3">
        <Tabs
          value={view}
          onValueChange={(v) => onViewChange(v as CalendarView)}
          aria-label="Calendar view"
        >
          <TabsList className="h-8 gap-0 rounded-[var(--r-2)] border border-[var(--border)] bg-[var(--surface-1)] p-0.5">
            {(['day', 'week', 'month', 'agenda'] as CalendarView[]).map((v) => (
              <TabsTrigger
                key={v}
                value={v}
                className={cn(
                  'h-7 rounded-[6px] px-3 text-[var(--fs-xs)]',
                  'after:hidden',
                  'data-[state=active]:bg-[var(--surface-3)]',
                  'data-[state=active]:text-[var(--fg)]',
                )}
              >
                {v === 'day'
                  ? 'Day'
                  : v === 'week'
                    ? 'Week'
                    : v === 'month'
                      ? 'Month'
                      : 'Agenda'}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>

        <Button
          variant="ghost"
          size="sm"
          onClick={onShowMeWhy}
          aria-label="Show provenance for selected event"
          className={cn(
            'gap-1.5 text-[var(--fs-sm)]',
            showMeWhyActive && 'bg-[var(--surface-2)] text-[var(--fg)]',
          )}
        >
          <Sparkles className="h-3.5 w-3.5" strokeWidth={1.5} />
          Show me why
        </Button>
      </div>
    </div>
  );
}
