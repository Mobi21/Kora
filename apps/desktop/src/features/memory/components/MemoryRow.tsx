import type { VaultMemoryItem } from '@/lib/api/types';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';
import { CertaintyDot } from './CertaintyDot';
import { PrimaryRowAction, RowActions, type RowOperation } from './RowActions';
import { certaintyVisual, formatRelativeOr, softTrim } from '../utils/format';

export interface MemoryRowAction {
  op: RowOperation;
  variant?: 'primary' | 'subtle' | 'ghost';
  /** Optional UI label override (icon stays from operation). */
  label?: string;
}

interface MemoryRowProps {
  memory: VaultMemoryItem;
  /** Hover-revealed icon actions (right side). */
  hoverActions?: RowOperation[];
  /** Always-visible primary actions (right side). */
  primaryActions?: MemoryRowAction[];
  onAction: (op: RowOperation, memory: VaultMemoryItem) => void;
}

/**
 * A single memory row. Per spec this is NOT a card — it's a list row with
 * a 4px certainty-color left rule, the 6px certainty dot, and a hairline
 * between rows (handled by the parent section).
 */
export function MemoryRow({
  memory,
  hoverActions,
  primaryActions,
  onAction,
}: MemoryRowProps): JSX.Element {
  const visual = certaintyVisual(memory.certainty);
  const showHover = hoverActions && hoverActions.length > 0;
  const showPrimary = primaryActions && primaryActions.length > 0;
  const hasTags = memory.tags.length > 0;
  const updated = formatRelativeOr(memory.updated_at, '');

  return (
    <div
      className={cn(
        'group/row relative flex items-start gap-3 py-3 pl-4 pr-2',
        'transition-colors duration-[var(--motion-fast)] ease-[var(--ease-out)]',
        'hover:bg-[var(--surface-2)] focus-within:bg-[var(--surface-2)]',
      )}
    >
      <span
        aria-hidden
        className="absolute inset-y-2 left-0 w-[4px] rounded-[var(--r-pill)]"
        style={{ background: visual.color }}
      />
      <div className="flex shrink-0 items-center pt-[6px]">
        <CertaintyDot
          certainty={memory.certainty}
          size={6}
          ariaLabel={`${visual.label} memory`}
        />
      </div>

      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <h3
          className={cn(
            'font-narrative text-[var(--fs-md)] tracking-[var(--track-tight)] text-[var(--fg)]',
            'truncate',
          )}
          title={memory.title}
        >
          {memory.title}
        </h3>
        {memory.body_preview && (
          <p
            className={cn(
              'font-narrative text-[var(--fs-sm)] italic text-[var(--fg-muted)]',
              'overflow-hidden',
            )}
            style={{
              display: '-webkit-box',
              WebkitBoxOrient: 'vertical',
              WebkitLineClamp: 2,
              maxWidth: '56ch',
            }}
          >
            {softTrim(memory.body_preview)}
          </p>
        )}
        <div className="flex items-center gap-2 pt-0.5">
          {hasTags && (
            <div
              className={cn(
                'flex flex-wrap items-center gap-1.5',
                showHover &&
                  'transition-opacity duration-[var(--motion-fast)] ease-[var(--ease-out)] group-hover/row:opacity-0 group-focus-within/row:opacity-0',
              )}
            >
              {memory.tags.slice(0, 4).map((tag) => (
                <Badge key={tag} className="text-[var(--fs-2xs)]">
                  {tag}
                </Badge>
              ))}
            </div>
          )}
          {updated && (
            <span className="font-mono num-tabular text-[var(--fs-2xs)] text-[var(--fg-subtle)]">
              {updated}
            </span>
          )}
        </div>
      </div>

      <div className="flex shrink-0 items-center gap-2 pt-1">
        {showPrimary &&
          primaryActions!.map(({ op, variant, label }) => (
            <PrimaryRowAction
              key={`${op}-${label ?? ''}`}
              op={op}
              variant={variant}
              label={label}
              onSelect={(o) => onAction(o, memory)}
            />
          ))}
        {showHover && (
          <RowActions
            operations={hoverActions!}
            onSelect={(o) => onAction(o, memory)}
          />
        )}
      </div>
    </div>
  );
}
