import { useEffect, useMemo, useState } from 'react';
import { AlertTriangle, RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { EmptyState } from '@/components/ui/empty-state';
import { Skeleton } from '@/components/ui/skeleton';
import { Divider } from '@/components/ui/divider';
import { cn } from '@/lib/utils';
import { formatRelative } from '@/lib/dates';
import type { TodayViewModel } from '@/lib/api/types';
import { TodayHero } from './components/TodayHero';
import { NowBlock } from './components/NowBlock';
import { NextList } from './components/NextList';
import { LaterStrip } from './components/LaterStrip';
import { TimelineCollapsible } from './components/TimelineCollapsible';
import { RealityCheckStrip } from './components/RealityCheckStrip';
import { TomorrowBridgeFooter } from './components/TomorrowBridgeFooter';
import { localIsoDate, useToday } from './queries';

export function TodayScreen(): JSX.Element {
  const date = useMemo(() => localIsoDate(), []);
  const query = useToday(date);

  return (
    <div className="flex h-full w-full justify-center overflow-y-auto">
      <div
        className="flex w-full flex-col gap-10 px-6 pb-16 pt-10 sm:px-8 lg:px-10"
        style={{ maxWidth: 'var(--ws-today)' }}
      >
        {query.isPending ? (
          <TodaySkeleton />
        ) : query.isError ? (
          <TodayErrorState onRetry={() => void query.refetch()} />
        ) : query.data ? (
          <TodayContent view={query.data} />
        ) : (
          <TodaySkeleton />
        )}
      </div>
    </div>
  );
}

interface TodayContentProps {
  view: TodayViewModel;
}

function TodayContent({ view }: TodayContentProps): JSX.Element {
  return (
    <>
      <TodayHero view={view} />

      <div className="flex flex-col" style={{ gap: 'var(--space-y-card)' }}>
        <NowBlock block={view.now} />
        <NextList block={view.next} />
        <LaterStrip block={view.later} />
      </div>

      <TimelineCollapsible items={view.timeline} />

      <Divider />

      <RealityCheckStrip date={view.date} repairAvailable={view.repair_available} />

      <TomorrowBridgeFooter />

      <FooterMeta view={view} />
    </>
  );
}

function FooterMeta({ view }: { view: TodayViewModel }): JSX.Element {
  const planSuffix = view.plan_id ? view.plan_id.slice(-8) : 'no-plan';
  const revision = view.revision ?? 0;
  const [relative, setRelative] = useState(() =>
    formatGeneratedAt(view.generated_at),
  );

  useEffect(() => {
    setRelative(formatGeneratedAt(view.generated_at));
    const id = window.setInterval(() => {
      setRelative(formatGeneratedAt(view.generated_at));
    }, 15_000);
    return () => window.clearInterval(id);
  }, [view.generated_at]);

  return (
    <div
      role="contentinfo"
      className={cn(
        'flex w-full flex-wrap items-center gap-x-4 gap-y-1 pt-2',
        'font-mono text-[var(--fs-2xs)] text-[var(--fg-subtle)] num-tabular',
      )}
    >
      <span>
        plan <span className="text-[var(--fg-muted)]">{planSuffix}</span>
      </span>
      <span aria-hidden>·</span>
      <span>
        rev <span className="text-[var(--fg-muted)]">{revision}</span>
      </span>
      <span aria-hidden>·</span>
      <span>{relative}</span>
    </div>
  );
}

function formatGeneratedAt(iso: string): string {
  if (!iso) return 'updated just now';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return 'updated just now';
  const diffMs = date.getTime() - Date.now();
  const seconds = Math.round(diffMs / 1000);
  if (Math.abs(seconds) < 60) {
    const s = Math.max(1, Math.abs(seconds));
    return `updated ${s}s ago`;
  }
  return `updated ${formatRelative(date)}`;
}

function TodayErrorState({ onRetry }: { onRetry: () => void }): JSX.Element {
  return (
    <div className="flex flex-1 items-center justify-center pt-16">
      <EmptyState
        icon={AlertTriangle}
        title="Today couldn't load."
        description="We'll keep trying. The daemon may be starting."
        action={
          <Button onClick={onRetry} aria-label="Retry loading today">
            <RefreshCw className="h-4 w-4" strokeWidth={1.75} aria-hidden />
            Try again
          </Button>
        }
      />
    </div>
  );
}

function TodaySkeleton(): JSX.Element {
  return (
    <div aria-busy aria-live="polite" className="flex flex-col gap-10">
      <header className="flex w-full flex-col gap-6 sm:flex-row sm:items-end sm:justify-between">
        <div className="flex min-w-0 flex-col gap-3">
          <Skeleton className="h-9 w-72" />
          <Skeleton className="h-5 w-56" />
        </div>
        <div className="flex flex-col items-end gap-2">
          <Skeleton className="h-6 w-28 rounded-[var(--r-pill)]" />
          <Skeleton className="h-5 w-32 rounded-[var(--r-pill)]" />
          <Skeleton className="h-3 w-32" />
        </div>
      </header>

      <div className="flex flex-col" style={{ gap: 'var(--space-y-card)' }}>
        <SectionSkeleton title rows={1} elevated />
        <SectionSkeleton title rows={3} />
        <SectionSkeleton title rows={1} stripChips />
      </div>

      <Skeleton className="h-12 w-full rounded-[var(--r-2)]" />

      <div className="flex flex-col gap-2">
        <Skeleton className="h-3 w-72" />
      </div>
    </div>
  );
}

interface SectionSkeletonProps {
  title?: boolean;
  rows: number;
  elevated?: boolean;
  stripChips?: boolean;
}

function SectionSkeleton({
  title,
  rows,
  elevated,
  stripChips,
}: SectionSkeletonProps): JSX.Element {
  return (
    <section className="flex flex-col gap-3">
      {title && (
        <div className="flex items-center justify-between">
          <Skeleton className="h-5 w-24" />
          <Skeleton className="h-3 w-32" />
        </div>
      )}
      {stripChips ? (
        <div className="flex flex-wrap gap-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-12 w-40 rounded-[var(--r-1)]" />
          ))}
        </div>
      ) : elevated ? (
        <Skeleton className="h-[5.5rem] w-full" />
      ) : (
        <div className="flex flex-col gap-1">
          {Array.from({ length: rows }).map((_, i) => (
            <div key={i} className="flex items-center gap-3 py-2">
              <Skeleton className="h-3 w-12" />
              <Skeleton className="h-4 flex-1" />
              <Skeleton className="h-3 w-10" />
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

export default TodayScreen;
