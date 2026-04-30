import { Check, Droplet, Undo2, UtensilsCrossed } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Pill } from '@/components/ui/pill';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';
import type { MedicationDose } from '@/lib/api/types';
import {
  doseTimeLabel,
  formatTimeOnly,
  statusLabel,
  statusToPillStatus,
} from '../utils/format';
import { DoseActionPopover } from './DoseActionPopover';

interface DoseRowProps {
  dose: MedicationDose;
  privacy: boolean;
}

function PairChip({ kind }: { kind: string }): JSX.Element {
  const k = kind.toLowerCase();
  const isWater = k.includes('water');
  const isMeal = k.includes('meal') || k.includes('food');
  const Icon = isWater ? Droplet : isMeal ? UtensilsCrossed : null;
  const label = isWater ? 'Water' : isMeal ? 'Meal' : kind;
  return (
    <span className="inline-flex items-center gap-1 rounded-[var(--r-pill)] border border-[var(--border)] bg-[var(--surface-2)] px-1.5 py-0.5 text-[var(--fs-2xs)] text-[var(--fg-muted)]">
      {Icon ? <Icon className="h-3 w-3" strokeWidth={1.75} aria-hidden="true" /> : null}
      <span>{label}</span>
    </span>
  );
}

export function DoseRow({ dose, privacy }: DoseRowProps): JSX.Element {
  const pillStatus = statusToPillStatus(dose.status);
  const timeText = doseTimeLabel(dose);

  const blurClass = privacy
    ? 'transition-[filter] duration-[var(--motion-fast)] ease-[var(--ease-out)] [filter:blur(6px)] hover:[filter:blur(0)]'
    : '';

  const renderActions = (): JSX.Element => {
    if (dose.status === 'taken') {
      return (
        <div className="flex items-center gap-3 text-[var(--fs-sm)]">
          <span className="inline-flex items-center gap-1.5 text-[var(--ok)]">
            <Check className="h-4 w-4" strokeWidth={2} aria-hidden="true" />
            {dose.scheduled_at && (
              <span className="font-narrative italic text-[var(--fg-muted)]">
                Taken at <span className="font-mono num-tabular">{formatTimeOnly(dose.scheduled_at)}</span>
              </span>
            )}
            {!dose.scheduled_at && (
              <span className="font-narrative italic text-[var(--fg-muted)]">Taken</span>
            )}
          </span>
          <DoseActionPopover
            dose={dose}
            status="skipped"
            triggerLabel="Undo"
            trigger={
              <button
                type="button"
                className="text-[var(--fs-xs)] text-[var(--fg-muted)] underline decoration-dotted underline-offset-4 transition-colors hover:text-[var(--fg)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--bg)] rounded-[var(--r-1)]"
                aria-label={`Undo logging for ${dose.name}`}
              >
                <Undo2 className="mr-1 inline h-3 w-3" strokeWidth={1.75} aria-hidden="true" />
                Undo
              </button>
            }
          />
        </div>
      );
    }

    if (dose.status === 'skipped' || dose.status === 'missed') {
      return (
        <div className="flex items-center gap-3">
          <Pill status="degraded" label={statusLabel(dose.status)} />
          <DoseActionPopover
            dose={dose}
            status="taken"
            triggerLabel="Mark taken"
            trigger={
              <button
                type="button"
                className="text-[var(--fs-xs)] text-[var(--accent)] underline decoration-dotted underline-offset-4 transition-colors hover:brightness-[1.1] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--bg)] rounded-[var(--r-1)]"
                aria-label={`Mark ${dose.name} as taken`}
              >
                Mark taken
              </button>
            }
          />
        </div>
      );
    }

    return (
      <div className="flex items-center gap-2">
        <Pill status={pillStatus} label={statusLabel(dose.status)} />
        <DoseActionPopover
          dose={dose}
          status="taken"
          triggerLabel="Took it"
          trigger={
            <Button variant="default" size="sm" aria-label={`Took ${dose.name}`}>
              Took it
            </Button>
          }
        />
        <DoseActionPopover
          dose={dose}
          status="skipped"
          triggerLabel="Skip"
          trigger={
            <Button variant="ghost" size="sm" aria-label={`Skip ${dose.name}`}>
              Skip
            </Button>
          }
        />
      </div>
    );
  };

  return (
    <div
      className="flex items-center gap-4 py-3"
      data-dose-id={dose.id}
      data-dose-status={dose.status}
    >
      <Tooltip>
        <TooltipTrigger asChild>
          <span
            className="inline-flex min-w-[5.25rem] shrink-0 items-center justify-center rounded-[var(--r-1)] border border-[var(--border)] bg-[var(--surface-1)] px-2 py-1 font-mono text-[var(--fs-xs)] num-tabular text-[var(--fg-muted)]"
            aria-label={
              dose.window_start && dose.window_end
                ? `Window ${timeText}`
                : `Scheduled at ${timeText}`
            }
          >
            {timeText}
          </span>
        </TooltipTrigger>
        <TooltipContent>
          {dose.window_start && dose.window_end
            ? 'Dosing window'
            : 'Scheduled time'}
        </TooltipContent>
      </Tooltip>

      <div className="min-w-0 flex-1 space-y-0.5">
        <div className="flex items-center gap-2">
          <span
            className={cn(
              'font-narrative text-[var(--fs-md)] tracking-[var(--track-tight)] text-[var(--fg)]',
              blurClass,
            )}
          >
            {dose.name}
          </span>
          {dose.pair_with.length > 0 && (
            <span className="flex items-center gap-1">
              {dose.pair_with.map((p) => (
                <PairChip key={p} kind={p} />
              ))}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2 text-[var(--fs-sm)] text-[var(--fg-muted)]">
          <span className={cn(blurClass)}>{dose.dose_label}</span>
          {dose.notes && (
            <>
              <span aria-hidden="true">·</span>
              <span className="italic">{dose.notes}</span>
            </>
          )}
        </div>
      </div>

      <div className="flex shrink-0 items-center">{renderActions()}</div>
    </div>
  );
}
