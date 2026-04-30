import { useState } from 'react';
import { AlertTriangle } from 'lucide-react';
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { Pill } from '@/components/ui/pill';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';
import type {
  RoutineActionRequest,
  RoutineActionResult,
  RoutineRunView,
} from '@/lib/api/types';
import { StepRow } from './StepRow';
import { RoutineActionsBar } from './RoutineActionsBar';

interface RoutineRunCardProps {
  run: RoutineRunView;
  onApply: (req: RoutineActionRequest) => Promise<RoutineActionResult>;
  busy: boolean;
}

function statusToPill(status: RoutineRunView['status']): {
  status: 'ok' | 'warn' | 'unknown' | 'degraded';
  label: string;
} {
  switch (status) {
    case 'active':
      return { status: 'ok', label: 'Active' };
    case 'paused':
      return { status: 'warn', label: 'Paused' };
    case 'pending':
      return { status: 'unknown', label: 'Pending' };
    case 'completed':
      return { status: 'ok', label: 'Completed' };
    case 'skipped':
      return { status: 'degraded', label: 'Cancelled' };
  }
}

export function RoutineRunCard({
  run,
  onApply,
  busy,
}: RoutineRunCardProps): JSX.Element {
  const [unavailable, setUnavailable] = useState<{ message: string } | null>(null);
  const variantTone =
    run.variant === 'low_energy'
      ? { status: 'unknown' as const, label: 'Low energy' }
      : { status: 'ok' as const, label: 'Standard' };
  const runStatus = statusToPill(run.status);

  const fire = async (
    action: RoutineActionRequest['action'],
    extra: Partial<RoutineActionRequest> = {},
  ): Promise<void> => {
    try {
      const res = await onApply({
        action,
        run_id: run.id,
        routine_id: run.routine_id,
        ...extra,
      });
      if (res.status === 'unavailable') {
        setUnavailable({ message: res.message || 'Action not yet wired.' });
      } else {
        setUnavailable(null);
      }
    } catch (err) {
      setUnavailable({
        message: err instanceof Error ? err.message : 'Action failed.',
      });
    }
  };

  const handleAction = (action: 'pause' | 'resume' | 'cancel' | 'reset'): void => {
    if (action === 'reset') {
      void fire('start');
    } else {
      void fire(action);
    }
  };

  return (
    <Card data-run-id={run.id} data-run-status={run.status}>
      <CardHeader className="flex-row items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <CardTitle className="text-[var(--fs-md)]">{run.name}</CardTitle>
          {run.description && (
            <CardDescription>{run.description}</CardDescription>
          )}
          <div className="flex items-center gap-2 pt-0.5">
            <Pill status={runStatus.status} label={runStatus.label} />
            <span className="font-mono num-tabular text-[var(--fs-xs)] text-[var(--fg-muted)]">
              {run.estimated_total_minutes}m total
            </span>
          </div>
        </div>
        <Pill status={variantTone.status} label={variantTone.label}>
          {variantTone.label}
        </Pill>
      </CardHeader>

      <CardContent className="space-y-1.5">
        {run.steps.length === 0 ? (
          <p className="font-narrative text-[var(--fs-sm)] italic text-[var(--fg-muted)]">
            No steps defined for this routine.
          </p>
        ) : (
          <div className="space-y-1">
            {run.steps.map((step, idx) => (
              <StepRow
                key={`${run.id}-${step.index}-${idx}`}
                step={step}
                isNext={
                  run.next_step_index !== null && run.next_step_index === step.index
                }
                isActiveRun={run.status === 'active' || run.status === 'pending'}
                busy={busy}
                onComplete={() =>
                  void fire('complete_step', { step_index: step.index })
                }
                onSkip={() =>
                  void fire('skip_step', { step_index: step.index })
                }
              />
            ))}
          </div>
        )}
        {unavailable && (
          <div className={cn('flex items-center gap-2 pt-2')}>
            <Tooltip>
              <TooltipTrigger asChild>
                <span>
                  <Pill status="warn" label={unavailable.message}>
                    <AlertTriangle
                      className="h-3 w-3"
                      strokeWidth={2}
                      aria-hidden="true"
                    />
                    Action not applied
                  </Pill>
                </span>
              </TooltipTrigger>
              <TooltipContent className="max-w-xs">
                {unavailable.message}
              </TooltipContent>
            </Tooltip>
          </div>
        )}
      </CardContent>

      <CardFooter className="justify-between">
        {run.started_at ? (
          <span className="text-[var(--fs-xs)] text-[var(--fg-subtle)]">
            Started{' '}
            <span className="font-mono num-tabular">
              {new Date(run.started_at).toLocaleTimeString(undefined, {
                hour: '2-digit',
                minute: '2-digit',
              })}
            </span>
          </span>
        ) : (
          <span className="text-[var(--fs-xs)] text-[var(--fg-subtle)]">Not started</span>
        )}
        <RoutineActionsBar run={run} busy={busy} onAction={handleAction} />
      </CardFooter>
    </Card>
  );
}
