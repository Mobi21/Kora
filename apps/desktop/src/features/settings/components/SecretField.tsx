import { HelpCircle, ShieldCheck } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Pill, type PillStatus } from '@/components/ui/pill';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';
import { RestartBadge } from './RestartBadge';

export interface SecretFieldProps {
  label: string;
  /** True if the secret has a value on the daemon. The value itself is never sent. */
  present: boolean;
  /** Optional last-4 characters when daemon exposes them. Otherwise omit. */
  lastFour?: string | null;
  hint?: string;
  whyTooltip?: string;
  restartRequired?: boolean;
  /** When provided, enables a "Test" button. */
  onTest?: () => void;
  testDisabled?: boolean;
  testLabel?: string;
  highlight?: boolean;
}

/**
 * Renders a "set" / "not set" status pill for a secret. Never renders the
 * raw secret. If a `lastFour` is supplied by the backend, shows it behind a
 * mask (e.g. `••••0a91`). The eye toggle is intentionally absent —
 * revealing client-side requires the secret to round-trip, which we forbid.
 */
export function SecretField({
  label,
  present,
  lastFour,
  hint,
  whyTooltip,
  restartRequired,
  onTest,
  testDisabled,
  testLabel = 'Test',
  highlight,
}: SecretFieldProps): JSX.Element {
  const status: PillStatus = present ? 'ok' : 'unknown';
  const statusLabel = present ? 'set' : 'not set';

  return (
    <div className="flex flex-col gap-1.5 border-l-[3px] border-l-transparent py-1 pl-3 -ml-3">
      <div className="flex items-center justify-between gap-2">
        <Label className="flex items-center gap-2">
          <span
            className={cn(
              highlight && 'rounded-[var(--r-1)] bg-[var(--accent-soft)] px-1',
            )}
          >
            {label}
          </span>
          {restartRequired && <RestartBadge />}
        </Label>
        {whyTooltip && (
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                type="button"
                aria-label={`Why is ${label} read-only?`}
                className="text-[var(--fg-subtle)] hover:text-[var(--fg-muted)] focus-visible:outline-none focus-visible:text-[var(--accent)]"
              >
                <HelpCircle className="h-3.5 w-3.5" strokeWidth={1.5} />
              </button>
            </TooltipTrigger>
            <TooltipContent>{whyTooltip}</TooltipContent>
          </Tooltip>
        )}
      </div>

      <div
        className={cn(
          'flex items-center gap-3 rounded-[var(--r-2)] border border-[var(--border)]',
          'bg-[var(--surface-1)] px-3 py-2',
        )}
      >
        <ShieldCheck
          className={cn(
            'h-4 w-4 shrink-0',
            present ? 'text-[var(--ok)]' : 'text-[var(--fg-subtle)]',
          )}
          strokeWidth={1.5}
          aria-hidden
        />
        <Pill status={status} label={statusLabel} />
        {present && lastFour && (
          <span
            className="font-mono text-[var(--fs-xs)] text-[var(--fg-muted)] num-tabular"
            aria-label={`ends in ${lastFour}`}
          >
            ••••{lastFour}
          </span>
        )}
        <span className="flex-1" />
        {onTest && (
          <Button
            variant="ghost"
            size="sm"
            onClick={onTest}
            disabled={testDisabled || !present}
            aria-label={`${testLabel} ${label}`}
          >
            {testLabel}
          </Button>
        )}
      </div>

      {hint && (
        <p className="text-[var(--fs-xs)] text-[var(--fg-muted)]">{hint}</p>
      )}
    </div>
  );
}
