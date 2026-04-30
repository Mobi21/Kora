import type { ReactNode } from 'react';
import { Card } from '@/components/ui/card';
import { cn } from '@/lib/utils';

export interface DefinitionPair {
  label: string;
  value: ReactNode;
  hint?: string;
  mono?: boolean;
}

interface SectionCardProps {
  title: string;
  description?: string;
  pairs?: DefinitionPair[];
  trailing?: ReactNode;
  footer?: ReactNode;
  children?: ReactNode;
  className?: string;
}

export function SectionCard({
  title,
  description,
  pairs,
  trailing,
  footer,
  children,
  className,
}: SectionCardProps): JSX.Element {
  return (
    <Card
      className={cn(
        'flex flex-col gap-4 p-[var(--pad)]',
        'bg-[var(--surface-1)]',
        className,
      )}
    >
      <header className="flex items-start justify-between gap-3">
        <div className="space-y-1">
          <h3
            className={cn(
              'text-[var(--fs-2xs)] uppercase tracking-[var(--track-label)]',
              'text-[var(--fg-muted)]',
            )}
          >
            {title}
          </h3>
          {description && (
            <p className="text-[var(--fs-sm)] text-[var(--fg-muted)]">{description}</p>
          )}
        </div>
        {trailing && <div className="flex shrink-0 items-center gap-2">{trailing}</div>}
      </header>

      {pairs && pairs.length > 0 && (
        <dl className="flex flex-col gap-2.5">
          {pairs.map((pair) => (
            <div
              key={pair.label}
              className="flex items-baseline justify-between gap-3 text-[var(--fs-sm)]"
            >
              <dt className="text-[var(--fg-muted)]">{pair.label}</dt>
              <dd
                className={cn(
                  'min-w-0 truncate text-right text-[var(--fg)]',
                  pair.mono && 'font-mono num-tabular text-[var(--fs-xs)]',
                  !pair.mono && 'num-tabular',
                )}
                title={typeof pair.value === 'string' ? pair.value : undefined}
              >
                {pair.value}
              </dd>
            </div>
          ))}
        </dl>
      )}

      {children}

      {footer && (
        <div className="border-t border-[var(--border)] pt-3 text-[var(--fs-xs)] text-[var(--fg-muted)]">
          {footer}
        </div>
      )}
    </Card>
  );
}
