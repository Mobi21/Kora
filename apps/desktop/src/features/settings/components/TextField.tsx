import { useId, type ReactNode } from 'react';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { HelpCircle } from 'lucide-react';
import { cn } from '@/lib/utils';
import { RestartBadge } from './RestartBadge';

export interface TextFieldProps {
  label: string;
  value: string;
  onChange?: (value: string) => void;
  placeholder?: string;
  hint?: string;
  error?: string | null;
  changed?: boolean;
  readOnly?: boolean;
  restartRequired?: boolean;
  whyTooltip?: string;
  highlight?: boolean;
  trailing?: ReactNode;
  type?: 'text' | 'email' | 'url';
}

export function TextField({
  label,
  value,
  onChange,
  placeholder,
  hint,
  error,
  changed,
  readOnly,
  restartRequired,
  whyTooltip,
  highlight,
  trailing,
  type = 'text',
}: TextFieldProps): JSX.Element {
  const id = useId();
  return (
    <div
      className={cn(
        'group flex flex-col gap-1.5 border-l-[3px] py-1 pl-3 -ml-3',
        changed ? 'border-l-[var(--accent)]' : 'border-l-transparent',
      )}
    >
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
                className="text-[var(--fg-subtle)] transition-colors hover:text-[var(--fg-muted)] focus-visible:outline-none focus-visible:text-[var(--accent)]"
              >
                <HelpCircle className="h-3.5 w-3.5" strokeWidth={1.5} />
              </button>
            </TooltipTrigger>
            <TooltipContent>{whyTooltip}</TooltipContent>
          </Tooltip>
        )}
      </div>

      <div className="flex items-center gap-2">
        <Input
          id={id}
          type={type}
          value={value}
          placeholder={placeholder}
          readOnly={readOnly}
          aria-readonly={readOnly}
          aria-invalid={!!error}
          onChange={(e) => onChange?.(e.target.value)}
          className={cn(readOnly && 'cursor-default opacity-90')}
        />
        {trailing}
      </div>

      {hint && !error && (
        <p className="text-[var(--fs-xs)] text-[var(--fg-muted)]">{hint}</p>
      )}
      {error && (
        <p className="text-[var(--fs-xs)] text-[var(--danger)]" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
