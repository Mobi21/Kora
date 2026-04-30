import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

interface IntegrationSectionProps {
  title: string;
  count?: number;
  empty?: string;
  isEmpty?: boolean;
  trailing?: ReactNode;
  children?: ReactNode;
  className?: string;
}

export function IntegrationSection({
  title,
  count,
  empty,
  isEmpty = false,
  trailing,
  children,
  className,
}: IntegrationSectionProps): JSX.Element {
  return (
    <section className={cn('flex flex-col gap-3', className)}>
      <header className="flex items-baseline justify-between gap-3">
        <h2
          className={cn(
            'font-narrative text-[var(--fs-xl)] tracking-[var(--track-tight)]',
            'text-[var(--fg)]',
          )}
        >
          {title}
          {typeof count === 'number' && (
            <span className="ml-2 font-mono text-[var(--fs-xs)] text-[var(--fg-muted)] num-tabular">
              · {count}
            </span>
          )}
        </h2>
        {trailing && <div className="flex items-center gap-2">{trailing}</div>}
      </header>

      {isEmpty ? (
        <p
          className={cn(
            'font-narrative italic text-[var(--fs-base)]',
            'text-[var(--fg-muted)]',
          )}
        >
          {empty ?? 'Not configured'}
        </p>
      ) : (
        children
      )}
    </section>
  );
}
