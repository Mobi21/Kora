import { cn } from '@/lib/utils';
import type { TodayViewModel } from '@/lib/api/types';
import { LoadIndicator } from './LoadIndicator';

interface TodayHeroProps {
  view: TodayViewModel;
  className?: string;
}

/**
 * Top-of-screen hero. No card chrome — pure typography against the page.
 * - Long-form date in Fraunces 2.25rem.
 * - Italic Fraunces narrative line (summary or graceful fallback).
 * - Right-aligned load + support indicator.
 */
export function TodayHero({ view, className }: TodayHeroProps): JSX.Element {
  const dateLabel = formatHeroDate(view.date);
  const summary = view.summary?.trim() || 'Today is yours.';

  return (
    <header
      className={cn(
        'flex w-full flex-col gap-6 sm:flex-row sm:items-end sm:justify-between',
        className,
      )}
    >
      <div className="flex min-w-0 flex-col gap-2">
        <h1
          className={cn(
            'font-narrative text-[var(--fs-4xl)] leading-[1.05] tracking-[var(--track-tight)]',
            'text-[var(--fg)]',
          )}
        >
          {dateLabel}
        </h1>
        <p
          className={cn(
            'font-narrative italic text-[var(--fs-md)] leading-[var(--lh-narrative)]',
            'text-[var(--fg-muted)]',
          )}
        >
          {summary}
        </p>
      </div>
      <LoadIndicator load={view.load} supportMode={view.support_mode} />
    </header>
  );
}

function formatHeroDate(iso: string): string {
  const [y, m, d] = iso.split('-').map((n) => Number.parseInt(n, 10));
  if (!y || !m || !d) return iso;
  const date = new Date(y, m - 1, d);
  return new Intl.DateTimeFormat(undefined, {
    weekday: 'long',
    month: 'long',
    day: 'numeric',
  }).format(date);
}
