import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

interface BoardColumnProps {
  title: string;
  count: number;
  emptyLabel: string;
  children?: ReactNode;
  description?: string;
  className?: string;
}

export function BoardColumn({
  title,
  count,
  emptyLabel,
  children,
  description,
  className,
}: BoardColumnProps): JSX.Element {
  const isEmpty = count === 0;
  return (
    <section
      aria-label={title}
      className={cn(
        'flex h-full min-w-0 flex-col gap-3',
        className,
      )}
    >
      <header className="flex items-baseline justify-between gap-2">
        <h2 className="font-narrative text-[var(--fs-lg)] tracking-[var(--track-tight)] text-[var(--fg)]">
          {title}
        </h2>
        <span
          aria-label={`${count} items`}
          className={cn(
            'inline-flex h-5 min-w-[20px] items-center justify-center rounded-[var(--r-pill)]',
            'border border-[var(--border)] bg-[var(--surface-1)] px-1.5',
            'font-mono text-[var(--fs-2xs)] text-[var(--fg-muted)] num-tabular',
          )}
        >
          {count}
        </span>
      </header>

      {description && (
        <p className="text-[var(--fs-xs)] text-[var(--fg-subtle)]">{description}</p>
      )}

      <div className="flex flex-col gap-2">
        {isEmpty ? (
          <p
            className={cn(
              'rounded-[var(--r-2)] border border-dashed border-[var(--border)]',
              'bg-transparent px-3 py-4',
              'font-narrative text-[var(--fs-sm)] italic text-[var(--fg-subtle)]',
            )}
          >
            {emptyLabel}
          </p>
        ) : (
          children
        )}
      </div>
    </section>
  );
}
