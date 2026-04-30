import { useId } from 'react';
import { HelpCircle } from 'lucide-react';
import { Switch } from '@/components/ui/switch';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';
import { RestartBadge } from './RestartBadge';

export interface SwitchFieldProps {
  label: string;
  description?: string;
  value: boolean;
  onChange?: (value: boolean) => void;
  readOnly?: boolean;
  changed?: boolean;
  restartRequired?: boolean;
  whyTooltip?: string;
  highlight?: boolean;
}

export function SwitchField({
  label,
  description,
  value,
  onChange,
  readOnly,
  changed,
  restartRequired,
  whyTooltip,
  highlight,
}: SwitchFieldProps): JSX.Element {
  const id = useId();
  return (
    <div
      className={cn(
        'flex items-start justify-between gap-4 border-l-[3px] py-1 pl-3 -ml-3',
        changed ? 'border-l-[var(--accent)]' : 'border-l-transparent',
      )}
    >
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <label
            htmlFor={id}
            className="text-[var(--fs-base)] font-medium text-[var(--fg)] cursor-pointer"
          >
            <span
              className={cn(
                highlight && 'rounded-[var(--r-1)] bg-[var(--accent-soft)] px-1',
              )}
            >
              {label}
            </span>
          </label>
          {restartRequired && <RestartBadge />}
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
        {description && (
          <p className="mt-0.5 text-[var(--fs-xs)] text-[var(--fg-muted)]">
            {description}
          </p>
        )}
      </div>
      <Switch
        id={id}
        checked={value}
        disabled={readOnly}
        aria-readonly={readOnly}
        onCheckedChange={(checked) => onChange?.(checked)}
        aria-label={label}
      />
    </div>
  );
}
