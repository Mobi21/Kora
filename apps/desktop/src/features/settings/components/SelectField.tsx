import { ChevronDown, HelpCircle } from 'lucide-react';
import { useId, type ReactNode } from 'react';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';
import { RestartBadge } from './RestartBadge';

export interface SelectOption<V extends string = string> {
  value: V;
  label: string;
  description?: string;
}

export interface SelectFieldProps<V extends string = string> {
  label: string;
  value: V;
  options: ReadonlyArray<SelectOption<V>>;
  onChange?: (value: V) => void;
  readOnly?: boolean;
  changed?: boolean;
  restartRequired?: boolean;
  whyTooltip?: string;
  hint?: string;
  highlight?: boolean;
  trailing?: ReactNode;
}

export function SelectField<V extends string = string>({
  label,
  value,
  options,
  onChange,
  readOnly,
  changed,
  restartRequired,
  whyTooltip,
  hint,
  highlight,
  trailing,
}: SelectFieldProps<V>): JSX.Element {
  const id = useId();
  const current = options.find((o) => o.value === value);

  return (
    <div
      className={cn(
        'flex flex-col gap-1.5 border-l-[3px] py-1 pl-3 -ml-3',
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
                className="text-[var(--fg-subtle)] hover:text-[var(--fg-muted)] focus-visible:outline-none focus-visible:text-[var(--accent)]"
              >
                <HelpCircle className="h-3.5 w-3.5" strokeWidth={1.5} />
              </button>
            </TooltipTrigger>
            <TooltipContent>{whyTooltip}</TooltipContent>
          </Tooltip>
        )}
      </div>

      <div className="flex items-center gap-2">
        {readOnly ? (
          <div
            id={id}
            aria-readonly
            className="flex h-9 flex-1 items-center rounded-[var(--r-2)] border border-[var(--border)] bg-[var(--surface-1)] px-3 text-[var(--fs-base)] text-[var(--fg)]"
          >
            {current?.label ?? value}
          </div>
        ) : (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                id={id}
                variant="outline"
                className="h-9 flex-1 justify-between font-normal"
                aria-label={label}
              >
                <span className="truncate">{current?.label ?? value}</span>
                <ChevronDown
                  className="h-3.5 w-3.5 text-[var(--fg-muted)]"
                  strokeWidth={1.5}
                  aria-hidden
                />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent className="min-w-[14rem]" align="start">
              {options.map((opt) => (
                <DropdownMenuItem
                  key={opt.value}
                  onSelect={() => onChange?.(opt.value)}
                  className="flex flex-col items-start gap-0.5"
                >
                  <span className="text-[var(--fs-sm)] text-[var(--fg)]">
                    {opt.label}
                  </span>
                  {opt.description && (
                    <span className="text-[var(--fs-xs)] text-[var(--fg-muted)]">
                      {opt.description}
                    </span>
                  )}
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
        )}
        {trailing}
      </div>

      {hint && (
        <p className="text-[var(--fs-xs)] text-[var(--fg-muted)]">{hint}</p>
      )}
    </div>
  );
}
