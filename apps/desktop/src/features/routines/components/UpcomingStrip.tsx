import { Play } from 'lucide-react';
import { Pill } from '@/components/ui/pill';
import { Button } from '@/components/ui/button';
import type {
  RoutineActionRequest,
  RoutineActionResult,
  RoutineRunView,
} from '@/lib/api/types';

interface UpcomingStripProps {
  runs: RoutineRunView[];
  onApply: (req: RoutineActionRequest) => Promise<RoutineActionResult>;
  busy: boolean;
}

function formatStart(iso: string | null): string {
  if (!iso) return '—:—';
  const d = new Date(iso);
  const h = d.getHours().toString().padStart(2, '0');
  const m = d.getMinutes().toString().padStart(2, '0');
  return `${h}:${m}`;
}

function variantStatus(variant: RoutineRunView['variant']): 'ok' | 'unknown' {
  return variant === 'low_energy' ? 'unknown' : 'ok';
}

export function UpcomingStrip({ runs, onApply, busy }: UpcomingStripProps): JSX.Element {
  if (runs.length === 0) {
    return (
      <p className="font-narrative text-[var(--fs-md)] italic text-[var(--fg-muted)]">
        No upcoming routines today.
      </p>
    );
  }
  return (
    <div className="flex flex-wrap gap-2">
      {runs.map((run) => (
        <div
          key={run.id}
          className="group flex items-center gap-3 rounded-[var(--r-2)] border border-[var(--border)] bg-[var(--surface-1)] px-3 py-2"
          data-run-id={run.id}
        >
          <div className="min-w-0 space-y-0.5">
            <div className="font-narrative text-[var(--fs-sm)] tracking-[var(--track-tight)] text-[var(--fg)]">
              {run.name}
            </div>
            <div className="flex items-center gap-2 text-[var(--fs-xs)] text-[var(--fg-muted)]">
              <span className="font-mono num-tabular">{formatStart(run.started_at)}</span>
              <Pill status={variantStatus(run.variant)} label={run.variant.replace('_', ' ')}>
                <span className="capitalize">{run.variant.replace('_', ' ')}</span>
              </Pill>
            </div>
          </div>
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7 opacity-60 transition-opacity group-hover:opacity-100"
            disabled={busy}
            aria-label={`Start ${run.name}`}
            onClick={() =>
              void onApply({
                action: 'start',
                run_id: run.id,
                routine_id: run.routine_id,
              })
            }
          >
            <Play className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
          </Button>
        </div>
      ))}
    </div>
  );
}
