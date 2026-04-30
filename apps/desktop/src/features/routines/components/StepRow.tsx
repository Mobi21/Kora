import { Check } from 'lucide-react';
import { Pill, type PillStatus } from '@/components/ui/pill';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import type { RoutineStepView } from '@/lib/api/types';

interface StepRowProps {
  step: RoutineStepView;
  isNext: boolean;
  isActiveRun: boolean;
  busy: boolean;
  onComplete: () => void;
  onSkip: () => void;
}

function energyToPill(level: RoutineStepView['energy_required']): PillStatus {
  switch (level) {
    case 'low':
      return 'ok';
    case 'medium':
      return 'unknown';
    case 'high':
      return 'warn';
  }
}

export function StepRow({
  step,
  isNext,
  isActiveRun,
  busy,
  onComplete,
  onSkip,
}: StepRowProps): JSX.Element {
  const railColor =
    isNext && isActiveRun ? 'var(--provenance-local)' : 'var(--fg-subtle)';

  return (
    <div
      className={cn(
        'relative flex gap-3 rounded-[var(--r-2)] py-2 pl-4 pr-3 transition-colors',
        isNext && isActiveRun && 'border border-[var(--accent)] bg-[var(--surface-1)]',
        !isNext && 'border border-transparent',
      )}
      data-step-index={step.index}
      data-completed={step.completed ? 'true' : 'false'}
    >
      <span
        aria-hidden="true"
        className="absolute left-1 top-2 bottom-2 w-[3px] rounded-full"
        style={{ background: railColor, opacity: step.completed ? 0.4 : 1 }}
      />
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex items-center gap-2">
          {step.completed && (
            <Check
              className="h-3.5 w-3.5 text-[var(--ok)]"
              strokeWidth={2}
              aria-label="Completed"
            />
          )}
          <span
            className={cn(
              'font-narrative text-[var(--fs-sm)] tracking-[var(--track-tight)] text-[var(--fg)]',
              step.completed && 'line-through opacity-80',
            )}
          >
            {step.title}
          </span>
          <Pill status={energyToPill(step.energy_required)} label={`${step.energy_required} energy`}>
            <span className="capitalize">{step.energy_required}</span>
          </Pill>
          <span className="font-mono num-tabular text-[var(--fs-2xs)] text-[var(--fg-subtle)]">
            {step.estimated_minutes}m
          </span>
        </div>
        {step.description && (
          <p
            className={cn(
              'text-[var(--fs-sm)] text-[var(--fg-muted)]',
              step.completed && 'opacity-70',
            )}
          >
            {step.description}
          </p>
        )}
        {step.cue && (
          <p className="text-[var(--fs-xs)] italic text-[var(--fg-subtle)]">
            Cue · {step.cue}
          </p>
        )}
        {isNext && isActiveRun && !step.completed && (
          <div className="flex items-center gap-2 pt-1.5">
            <Button
              variant="default"
              size="sm"
              onClick={onComplete}
              disabled={busy}
              aria-label={`Mark step "${step.title}" complete`}
            >
              Mark complete
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={onSkip}
              disabled={busy}
              aria-label={`Skip step "${step.title}"`}
            >
              Skip step
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}
