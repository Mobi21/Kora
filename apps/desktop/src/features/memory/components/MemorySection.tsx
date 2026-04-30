import type { ReactNode } from 'react';
import { Skeleton } from '@/components/ui/skeleton';
import { Button } from '@/components/ui/button';
import { AlertTriangle } from 'lucide-react';
import { cn } from '@/lib/utils';
import type { VaultMemoryItem } from '@/lib/api/types';
import { MemoryRow, type MemoryRowAction } from './MemoryRow';
import type { RowOperation } from './RowActions';

interface MemorySectionProps {
  title: string;
  subtitle?: string;
  memories: VaultMemoryItem[];
  hoverActions?: RowOperation[];
  primaryActions?: MemoryRowAction[];
  onAction: (op: RowOperation, memory: VaultMemoryItem) => void;
  emptyText?: string;
  /** When true, render skeleton rows instead of memories. */
  loading?: boolean;
  /** When set, render a per-section error chip + retry. */
  error?: { message: string; onRetry?: () => void } | null;
  trailing?: ReactNode;
  /** Override the default 6 skeleton rows. */
  skeletonRows?: number;
}

export function MemorySection({
  title,
  subtitle,
  memories,
  hoverActions,
  primaryActions,
  onAction,
  emptyText,
  loading = false,
  error,
  trailing,
  skeletonRows = 6,
}: MemorySectionProps): JSX.Element {
  return (
    <section className="flex flex-col gap-3" aria-label={title}>
      <header className="flex items-end justify-between gap-3">
        <div className="space-y-0.5">
          <h2 className="font-narrative text-[var(--fs-2xl)] tracking-[var(--track-tight)] text-[var(--fg)]">
            {title}
          </h2>
          {subtitle && (
            <p className="text-[var(--fs-sm)] text-[var(--fg-muted)]">{subtitle}</p>
          )}
        </div>
        {trailing}
      </header>

      <div
        className={cn(
          'overflow-hidden rounded-[var(--r-2)] border border-[var(--border)]',
          'bg-[var(--surface-1)]',
          'divide-y divide-[var(--border)]',
        )}
      >
        {loading ? (
          Array.from({ length: skeletonRows }, (_, i) => <SkeletonRow key={i} />)
        ) : error ? (
          <div className="flex items-center justify-between gap-3 px-4 py-4">
            <div className="flex items-center gap-2 text-[var(--fs-sm)] text-[var(--danger)]">
              <AlertTriangle className="h-4 w-4" strokeWidth={1.5} aria-hidden />
              <span>{error.message}</span>
            </div>
            {error.onRetry && (
              <Button size="sm" variant="outline" onClick={error.onRetry}>
                Retry
              </Button>
            )}
          </div>
        ) : memories.length === 0 ? (
          <div className="px-4 py-8 text-center">
            <p className="font-narrative italic text-[var(--fs-sm)] text-[var(--fg-muted)]">
              {emptyText ?? 'Nothing here yet.'}
            </p>
          </div>
        ) : (
          memories.map((m) => (
            <MemoryRow
              key={m.id}
              memory={m}
              hoverActions={hoverActions}
              primaryActions={primaryActions}
              onAction={onAction}
            />
          ))
        )}
      </div>
    </section>
  );
}

function SkeletonRow(): JSX.Element {
  return (
    <div className="flex items-start gap-3 py-3 pl-4 pr-2">
      <Skeleton className="mt-1 h-1.5 w-1.5 rounded-full" />
      <div className="flex min-w-0 flex-1 flex-col gap-2">
        <Skeleton className="h-4 w-2/3" />
        <Skeleton className="h-3 w-11/12" />
        <div className="flex gap-2">
          <Skeleton className="h-3 w-16" />
          <Skeleton className="h-3 w-12" />
        </div>
      </div>
    </div>
  );
}
