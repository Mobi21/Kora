import { Check, Copy, ExternalLink, HelpCircle } from 'lucide-react';
import { useId, useState, type ReactNode } from 'react';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';
import { RestartBadge } from './RestartBadge';

export interface MonoFieldProps {
  label: string;
  value: string | null | undefined;
  hint?: string;
  whyTooltip?: string;
  restartRequired?: boolean;
  reveal?: boolean;
  highlight?: boolean;
  fallback?: string;
  trailing?: ReactNode;
}

/** Read-only path/ID display in JetBrains Mono with copy + reveal actions. */
export function MonoField({
  label,
  value,
  hint,
  whyTooltip,
  restartRequired,
  reveal,
  highlight,
  fallback = 'Not exposed by daemon yet.',
  trailing,
}: MonoFieldProps): JSX.Element {
  const id = useId();
  const [copied, setCopied] = useState(false);
  const display = value && value.length > 0 ? value : null;
  const bridgeAvailable =
    typeof window !== 'undefined' && !!window.kora?.openExternal;

  async function copy(): Promise<void> {
    if (!display) return;
    try {
      await navigator.clipboard.writeText(display);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      /* ignore */
    }
  }

  function revealInFinder(): void {
    if (!display || !bridgeAvailable) return;
    const target =
      display.startsWith('file://') || display.startsWith('http')
        ? display
        : `file://${display}`;
    void window.kora?.openExternal(target).catch(() => {
      /* ignore */
    });
  }

  return (
    <div className="flex flex-col gap-1.5 border-l-[3px] border-l-transparent py-1 pl-3 -ml-3">
      <div className="flex items-center justify-between gap-2">
        <Label htmlFor={id} className="flex items-center gap-2">
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
        id={id}
        className={cn(
          'flex items-center gap-2 rounded-[var(--r-2)] border border-[var(--border)]',
          'bg-[var(--surface-1)] px-3 py-2',
        )}
      >
        {display ? (
          <span
            className="min-w-0 flex-1 truncate font-mono text-[var(--fs-xs)] text-[var(--fg)] num-tabular"
            title={display}
          >
            {display}
          </span>
        ) : (
          <Tooltip>
            <TooltipTrigger asChild>
              <span
                className="min-w-0 flex-1 truncate font-mono text-[var(--fs-xs)] italic text-[var(--fg-subtle)]"
                aria-label={fallback}
              >
                —
              </span>
            </TooltipTrigger>
            <TooltipContent>{fallback}</TooltipContent>
          </Tooltip>
        )}

        {trailing}

        {reveal && bridgeAvailable && display && (
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7"
                onClick={revealInFinder}
                aria-label={`Reveal ${label} in Finder`}
              >
                <ExternalLink className="h-3.5 w-3.5" strokeWidth={1.5} />
              </Button>
            </TooltipTrigger>
            <TooltipContent>Reveal in Finder</TooltipContent>
          </Tooltip>
        )}

        {display && (
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7"
                onClick={() => void copy()}
                aria-label={`Copy ${label}`}
              >
                {copied ? (
                  <Check
                    className="h-3.5 w-3.5 text-[var(--ok)]"
                    strokeWidth={1.5}
                  />
                ) : (
                  <Copy className="h-3.5 w-3.5" strokeWidth={1.5} />
                )}
              </Button>
            </TooltipTrigger>
            <TooltipContent>{copied ? 'Copied' : 'Copy'}</TooltipContent>
          </Tooltip>
        )}
      </div>

      {hint && (
        <p className="text-[var(--fs-xs)] text-[var(--fg-muted)]">{hint}</p>
      )}
    </div>
  );
}
