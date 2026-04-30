import { useId } from 'react';
import { HelpCircle, Minus, Plus } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';
import { RestartBadge } from './RestartBadge';

export interface NumberFieldProps {
  label: string;
  value: number;
  onChange?: (value: number) => void;
  step?: number;
  min?: number;
  max?: number;
  hint?: string;
  unit?: string;
  error?: string | null;
  readOnly?: boolean;
  changed?: boolean;
  restartRequired?: boolean;
  whyTooltip?: string;
  highlight?: boolean;
}

export function NumberField({
  label,
  value,
  onChange,
  step = 1,
  min,
  max,
  hint,
  unit,
  error,
  readOnly,
  changed,
  restartRequired,
  whyTooltip,
  highlight,
}: NumberFieldProps): JSX.Element {
  const id = useId();

  function clamp(n: number): number {
    let next = n;
    if (typeof min === 'number' && next < min) next = min;
    if (typeof max === 'number' && next > max) next = max;
    return next;
  }

  function adjust(delta: number): void {
    if (readOnly) return;
    const next = clamp(value + delta);
    onChange?.(next);
  }

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
        <Button
          variant="outline"
          size="icon"
          className="h-9 w-9 shrink-0"
          onClick={() => adjust(-step)}
          disabled={readOnly || (typeof min === 'number' && value <= min)}
          aria-label={`Decrease ${label}`}
        >
          <Minus className="h-3.5 w-3.5" strokeWidth={1.5} />
        </Button>
        <Input
          id={id}
          type="number"
          value={Number.isFinite(value) ? value : 0}
          step={step}
          min={min}
          max={max}
          readOnly={readOnly}
          aria-readonly={readOnly}
          aria-invalid={!!error}
          onChange={(e) => {
            const raw = Number.parseFloat(e.target.value);
            if (Number.isNaN(raw)) return;
            onChange?.(clamp(raw));
          }}
          className={cn(
            'text-center font-mono num-tabular',
            readOnly && 'cursor-default opacity-90',
          )}
        />
        <Button
          variant="outline"
          size="icon"
          className="h-9 w-9 shrink-0"
          onClick={() => adjust(step)}
          disabled={readOnly || (typeof max === 'number' && value >= max)}
          aria-label={`Increase ${label}`}
        >
          <Plus className="h-3.5 w-3.5" strokeWidth={1.5} />
        </Button>
        {unit && (
          <span className="text-[var(--fs-xs)] uppercase tracking-[var(--track-label)] text-[var(--fg-subtle)]">
            {unit}
          </span>
        )}
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
