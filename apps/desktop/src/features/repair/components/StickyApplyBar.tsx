import { ArrowRight, Move } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

interface StickyApplyBarProps {
  count: number;
  onApply: () => void;
  onClear: () => void;
  isPending: boolean;
}

export function StickyApplyBar({
  count,
  onApply,
  onClear,
  isPending,
}: StickyApplyBarProps): JSX.Element | null {
  if (count <= 0) return null;
  return (
    <div
      role="region"
      aria-label="Move selected items to tomorrow"
      className={cn(
        'sticky bottom-0 left-0 right-0 z-10 mt-4 flex items-center justify-between gap-3',
        'rounded-[var(--r-2)] border border-[var(--border)] bg-[var(--surface-1)] p-3',
        'shadow-[var(--shadow-2)]',
      )}
    >
      <div className="flex items-center gap-2">
        <Move className="h-4 w-4 text-[var(--accent)]" strokeWidth={1.5} aria-hidden />
        <span className="font-narrative text-[var(--fs-md)] italic text-[var(--fg)]">
          <span className="font-mono num-tabular not-italic">{count}</span>
          {' '}item{count === 1 ? '' : 's'} ready to move to tomorrow
        </span>
      </div>
      <div className="flex items-center gap-2">
        <Button variant="ghost" onClick={onClear} disabled={isPending}>
          Clear
        </Button>
        <Button variant="default" onClick={onApply} disabled={isPending}>
          Move {count} item{count === 1 ? '' : 's'}
          <ArrowRight className="h-4 w-4" strokeWidth={1.5} aria-hidden />
        </Button>
      </div>
    </div>
  );
}
