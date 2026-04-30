import {
  Pencil,
  GitMerge,
  Archive,
  Trash2,
  CheckCircle2,
  type LucideIcon,
} from 'lucide-react';
import type { VaultCorrectionRequest } from '@/lib/api/types';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';

export type RowOperation = VaultCorrectionRequest['operation'];

export interface RowActionDef {
  op: RowOperation;
  label: string;
  icon: LucideIcon;
  tone?: 'default' | 'danger';
}

const ACTIONS: Record<RowOperation, RowActionDef> = {
  correct: { op: 'correct', label: 'Correct', icon: Pencil },
  merge: { op: 'merge', label: 'Merge', icon: GitMerge },
  mark_stale: { op: 'mark_stale', label: 'Mark stale', icon: Archive },
  delete: { op: 'delete', label: 'Delete', icon: Trash2, tone: 'danger' },
  confirm: { op: 'confirm', label: 'Confirm', icon: CheckCircle2 },
};

interface RowActionsProps {
  /** Which actions to show, in display order. */
  operations: RowOperation[];
  /** Always visible (true) or hover/focus only (default false). */
  alwaysVisible?: boolean;
  onSelect: (op: RowOperation) => void;
  className?: string;
}

/**
 * The right-side icon-button cluster on a memory row. Hidden by default,
 * revealed on hover/focus-within of the row, or always visible when the
 * caller asks (used by the Guesses section, where actions are primary).
 */
export function RowActions({
  operations,
  alwaysVisible = false,
  onSelect,
  className,
}: RowActionsProps): JSX.Element {
  return (
    <div
      className={cn(
        'flex items-center gap-1',
        !alwaysVisible &&
          'opacity-0 transition-opacity duration-[var(--motion-fast)] ease-[var(--ease-out)] group-hover/row:opacity-100 group-focus-within/row:opacity-100',
        className,
      )}
    >
      {operations.map((op) => {
        const def = ACTIONS[op];
        if (!def) return null;
        const Icon = def.icon;
        const danger = def.tone === 'danger';
        return (
          <Tooltip key={op}>
            <TooltipTrigger asChild>
              <button
                type="button"
                aria-label={def.label}
                onClick={() => onSelect(op)}
                className={cn(
                  'inline-flex h-8 w-8 items-center justify-center rounded-[var(--r-1)]',
                  'text-[var(--fg-muted)] transition-colors duration-[var(--motion-fast)] ease-[var(--ease-out)]',
                  'hover:bg-[var(--surface-2)] hover:text-[var(--fg)]',
                  danger && 'hover:text-[var(--danger)]',
                  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]',
                )}
              >
                <Icon className="h-4 w-4" strokeWidth={1.5} />
              </button>
            </TooltipTrigger>
            <TooltipContent side="top">{def.label}</TooltipContent>
          </Tooltip>
        );
      })}
    </div>
  );
}

interface PrimaryActionProps {
  op: RowOperation;
  onSelect: (op: RowOperation) => void;
  variant?: 'primary' | 'subtle' | 'ghost';
  /** Override the default action label (e.g. "Refresh" for `correct`). */
  label?: string;
  className?: string;
}

const VARIANT_CLASSES: Record<NonNullable<PrimaryActionProps['variant']>, string> = {
  primary:
    'bg-[var(--accent)] text-[var(--accent-fg)] hover:brightness-[1.05]',
  subtle: 'bg-[var(--surface-2)] text-[var(--fg)] hover:bg-[var(--surface-3)]',
  ghost: 'text-[var(--fg-muted)] hover:bg-[var(--surface-2)] hover:text-[var(--fg)]',
};

/** A right-aligned text button used by Guesses & Stale sections. */
export function PrimaryRowAction({
  op,
  onSelect,
  variant = 'subtle',
  label,
  className,
}: PrimaryActionProps): JSX.Element {
  const def = ACTIONS[op];
  const displayLabel = label ?? def.label;
  return (
    <button
      type="button"
      onClick={() => onSelect(op)}
      aria-label={displayLabel}
      className={cn(
        'inline-flex h-8 items-center gap-1.5 rounded-[var(--r-2)] px-3 text-[var(--fs-sm)]',
        'transition-[background-color,color] duration-[var(--motion-fast)] ease-[var(--ease-out)]',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]',
        'focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--bg)]',
        VARIANT_CLASSES[variant],
        className,
      )}
    >
      <def.icon className="h-3.5 w-3.5" strokeWidth={1.5} />
      {displayLabel}
    </button>
  );
}

export { ACTIONS as ROW_ACTION_DEFS };
