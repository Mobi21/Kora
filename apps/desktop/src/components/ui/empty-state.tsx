import type { LucideIcon } from 'lucide-react';
import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

interface EmptyStateProps {
  icon?: LucideIcon;
  title: string;
  description?: string;
  action?: ReactNode;
  className?: string;
}

export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  className,
}: EmptyStateProps): JSX.Element {
  return (
    <div
      className={cn(
        'mx-auto flex w-full max-w-md flex-col items-center justify-center text-center',
        'gap-4 px-6 py-12',
        className,
      )}
    >
      {Icon && (
        <div
          className={cn(
            'flex h-12 w-12 items-center justify-center rounded-[var(--r-2)]',
            'border border-[var(--border)] bg-[var(--surface-2)] text-[var(--fg-muted)]',
          )}
        >
          <Icon className="h-5 w-5" strokeWidth={1.5} />
        </div>
      )}
      <div className="space-y-1.5">
        <h2 className="font-narrative text-[var(--fs-2xl)] tracking-[var(--track-tight)] text-[var(--fg)]">
          {title}
        </h2>
        {description && (
          <p className="text-[var(--fs-base)] text-[var(--fg-muted)]">{description}</p>
        )}
      </div>
      {action && <div className="pt-1">{action}</div>}
    </div>
  );
}
