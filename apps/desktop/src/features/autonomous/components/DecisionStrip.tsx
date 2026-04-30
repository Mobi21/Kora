import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import type { AutonomousDecisionView } from '@/lib/api/types';
import { cn } from '@/lib/utils';
import { formatCountdown, useTickingNow } from '../queries';

interface DecisionStripProps {
  decisions: readonly AutonomousDecisionView[];
}

/**
 * Top-of-screen strip surfacing decisions Kora is waiting on. Buttons are
 * inert — there's no apply endpoint defined yet, but the prompt and options
 * are still visible so the user knows what's pending.
 */
export function DecisionStrip({ decisions }: DecisionStripProps): JSX.Element {
  if (decisions.length === 0) {
    return (
      <p
        className="font-narrative italic text-[var(--fg-muted)]"
        style={{ fontSize: '0.9375rem' }}
      >
        No open decisions.
      </p>
    );
  }
  return (
    <section
      aria-label="Open decisions"
      className="flex flex-col gap-3"
    >
      {decisions.map((decision) => (
        <DecisionCard key={decision.id} decision={decision} />
      ))}
    </section>
  );
}

interface DecisionCardProps {
  decision: AutonomousDecisionView;
  compact?: boolean;
}

/**
 * A single decision card. Used both at the top of the screen and inline on
 * a plan card (compact mode shrinks the prompt and tightens spacing).
 */
export function DecisionCard({ decision, compact = false }: DecisionCardProps): JSX.Element {
  const now = useTickingNow();
  const countdown = formatCountdown(decision.deadline_at, now);
  const overdue = decision.deadline_at
    ? new Date(decision.deadline_at).getTime() < now
    : false;

  return (
    <Card
      className={cn(
        'flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between',
        compact && 'gap-2 p-3',
      )}
    >
      <div className="flex min-w-0 flex-col gap-2">
        <p
          className={cn(
            'font-narrative text-[var(--fg)]',
            compact ? 'text-[var(--fs-base)]' : '',
          )}
          style={
            compact
              ? { fontSize: '0.9375rem', lineHeight: 1.45 }
              : { fontSize: '1.0625rem', lineHeight: 1.4 }
          }
        >
          {decision.prompt}
        </p>
        <div className="flex flex-wrap items-center gap-2">
          {decision.options.length === 0 ? (
            <span className="text-[var(--fs-xs)] text-[var(--fg-subtle)]">
              No options provided.
            </span>
          ) : (
            decision.options.map((option, idx) => (
              <Tooltip key={`${decision.id}-${idx}-${option}`}>
                <TooltipTrigger asChild>
                  {/* The wrapping span gives the tooltip a hover target even
                      while the underlying button is disabled. */}
                  <span tabIndex={0} className="inline-flex">
                    <Button
                      type="button"
                      size="sm"
                      variant={idx === 0 ? 'default' : 'outline'}
                      disabled
                      aria-disabled
                      aria-label={`Option ${idx + 1}: ${option} (disabled)`}
                    >
                      {option}
                    </Button>
                  </span>
                </TooltipTrigger>
                <TooltipContent>
                  Decision endpoint not exposed by daemon
                </TooltipContent>
              </Tooltip>
            ))
          )}
        </div>
      </div>

      <div className="flex flex-col items-start gap-0.5 sm:items-end">
        <span
          className={cn(
            'font-mono num-tabular text-[var(--fs-sm)]',
            overdue ? 'text-[var(--danger)]' : 'text-[var(--fg)]',
          )}
        >
          {countdown}
        </span>
        <span className="font-mono num-tabular text-[var(--fs-xs)] text-[var(--fg-subtle)]">
          {decision.id}
        </span>
      </div>
    </Card>
  );
}
