import { ChevronLeft, ChevronRight, Eye, EyeOff } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/switch';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { dayOffsetFromToday, formatDayLabel, shiftIsoDate } from '../utils/format';

interface MedicationHeaderProps {
  date: string;
  onChangeDate: (iso: string) => void;
  privacy: boolean;
  onChangePrivacy: (next: boolean) => void;
}

const MAX_OFFSET = 7;

export function MedicationHeader({
  date,
  onChangeDate,
  privacy,
  onChangePrivacy,
}: MedicationHeaderProps): JSX.Element {
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
          Medication
        </h1>
        <p className="font-narrative text-[var(--fs-md)] italic text-[var(--fg-muted)]">
          What you’ve taken today, what’s next.
        </p>
      </div>
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2">
          <Tooltip>
            <TooltipTrigger asChild>
              <label
                htmlFor="medication-privacy"
                className="flex cursor-pointer items-center gap-1.5 text-[var(--fs-xs)] text-[var(--fg-muted)]"
              >
                {privacy ? (
                  <EyeOff className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                ) : (
                  <Eye className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                )}
                Privacy
              </label>
            </TooltipTrigger>
            <TooltipContent>
              Blur dose names until hover. Persists locally.
            </TooltipContent>
          </Tooltip>
          <Switch
            id="medication-privacy"
            checked={privacy}
            onCheckedChange={onChangePrivacy}
            aria-label="Toggle privacy blur"
          />
        </div>
        <div
          className="flex items-center gap-1 rounded-[var(--r-2)] border border-[var(--border)] bg-[var(--surface-1)] p-0.5"
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
            <ChevronLeft className="h-4 w-4" strokeWidth={1.75} aria-hidden />
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
            <ChevronRight className="h-4 w-4" strokeWidth={1.75} aria-hidden />
          </Button>
        </div>
      </div>
    </header>
  );
}
