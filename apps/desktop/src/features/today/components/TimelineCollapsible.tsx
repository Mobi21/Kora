import { ChevronDown, ChevronRight } from 'lucide-react';
import { Fragment, useState } from 'react';
import { Divider } from '@/components/ui/divider';
import { cn } from '@/lib/utils';
import type { TimelineItem } from '@/lib/api/types';
import { TimelineItemRow } from './TimelineItemRow';

interface TimelineCollapsibleProps {
  items: TimelineItem[];
  defaultOpen?: boolean;
}

export function TimelineCollapsible({
  items,
  defaultOpen = false,
}: TimelineCollapsibleProps): JSX.Element | null {
  const [open, setOpen] = useState(defaultOpen);
  if (items.length === 0) return null;

  return (
    <section
      className={cn(
        'flex flex-col gap-2 rounded-[var(--r-2)] border border-[var(--border)]',
        'bg-[var(--surface-1)]',
      )}
    >
      <button
        type="button"
        aria-expanded={open}
        aria-controls="today-full-timeline"
        onClick={() => setOpen((v) => !v)}
        className={cn(
          'flex w-full items-center justify-between gap-3 px-[var(--pad)] py-3',
          'text-left text-[var(--fs-sm)] text-[var(--fg)]',
          'rounded-[var(--r-2)]',
          'transition-colors duration-[var(--motion-fast)] ease-[var(--ease-out)]',
          'hover:bg-[var(--surface-2)]',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]',
        )}
      >
        <span className="inline-flex items-center gap-2">
          {open ? (
            <ChevronDown className="h-4 w-4" strokeWidth={1.75} aria-hidden />
          ) : (
            <ChevronRight className="h-4 w-4" strokeWidth={1.75} aria-hidden />
          )}
          <span className="font-medium">Full timeline</span>
          <span className="font-mono text-[var(--fs-xs)] text-[var(--fg-muted)] num-tabular">
            {items.length}
          </span>
        </span>
        <span className="text-[var(--fs-xs)] text-[var(--fg-muted)]">
          {open ? 'Hide' : 'Show'}
        </span>
      </button>
      {open && (
        <ul
          id="today-full-timeline"
          className="flex flex-col px-[var(--pad)] pb-3"
        >
          {items.map((item, idx) => (
            <Fragment key={item.id}>
              {idx > 0 && <Divider />}
              <li>
                <TimelineItemRow item={item} variant="compact" showSupportTags={false} />
              </li>
            </Fragment>
          ))}
        </ul>
      )}
    </section>
  );
}
