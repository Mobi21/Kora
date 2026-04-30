import type { KeyboardEvent } from 'react';
import { Eye } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { ProvenanceDot, type ProvenanceKind } from '@/components/ui/provenance-dot';
import { Pill } from '@/components/ui/pill';
import { cn } from '@/lib/utils';
import { formatTime } from '@/lib/dates';

interface BoardCardProps {
  title: string;
  time?: string | null;
  endTime?: string | null;
  tags?: string[];
  provenance?: ProvenanceKind;
  severity?: number | null;
  selectable?: boolean;
  selected?: boolean;
  onSelect?: (next: boolean) => void;
  onPreview?: () => void;
  selectLabel?: string;
}

function severityPill(severity: number): JSX.Element {
  if (severity >= 0.6) return <Pill status="degraded" label="High severity">High</Pill>;
  if (severity >= 0.3) return <Pill status="warn" label="Medium severity">Medium</Pill>;
  return <Pill status="ok" label="Low severity">Low</Pill>;
}

export function BoardCard({
  title,
  time,
  endTime,
  tags,
  provenance = 'local',
  severity,
  selectable = false,
  selected = false,
  onSelect,
  onPreview,
  selectLabel,
}: BoardCardProps): JSX.Element {
  const interactive = selectable && !!onSelect;
  const containerProps = interactive
    ? {
        role: 'checkbox' as const,
        'aria-checked': selected,
        tabIndex: 0,
        onClick: () => onSelect!(!selected),
        onKeyDown: (event: KeyboardEvent<HTMLDivElement>) => {
          if (event.key === ' ' || event.key === 'Enter') {
            event.preventDefault();
            onSelect!(!selected);
          }
        },
      }
    : {};

  return (
    <div
      {...containerProps}
      aria-label={interactive ? selectLabel ?? title : undefined}
      className={cn(
        'group relative rounded-[var(--r-2)] border border-[var(--border)] bg-[var(--surface-1)]',
        'pl-[14px] pr-3 py-2.5 transition-colors',
        'duration-[var(--motion-fast)] ease-[var(--ease-out)]',
        interactive && 'cursor-pointer hover:bg-[var(--surface-2)]',
        interactive &&
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]',
        selected &&
          'border-transparent bg-[var(--accent-soft)]',
      )}
      style={{
        boxShadow: `inset 4px 0 0 var(--provenance-${provenance})`,
      }}
    >
      <div className="flex items-start gap-2">
        <ProvenanceDot kind={provenance} className="mt-1.5 shrink-0" />
        <div className="flex min-w-0 flex-1 flex-col gap-1.5">
          <div className="flex items-baseline gap-2">
            <h3 className="min-w-0 flex-1 truncate text-[var(--fs-sm)] font-medium text-[var(--fg)]">
              {title}
            </h3>
            {time && (
              <span className="font-mono text-[var(--fs-2xs)] text-[var(--fg-muted)] num-tabular">
                {formatTime(time)}
                {endTime ? `–${formatTime(endTime)}` : ''}
              </span>
            )}
          </div>

          {(tags?.length || severity != null || onPreview) && (
            <div className="flex flex-wrap items-center gap-1.5">
              {severity != null && severityPill(severity)}
              {tags
                ?.slice(0, 3)
                .filter(Boolean)
                .map((tag, index) => (
                  <span
                    key={`${tag}-${index}`}
                    className={cn(
                      'inline-flex items-center rounded-[var(--r-pill)] border border-[var(--border)]',
                      'bg-[var(--surface-2)] px-2 py-0.5 text-[var(--fs-2xs)] text-[var(--fg-muted)]',
                    )}
                  >
                    {tag.replace(/_/g, ' ')}
                  </span>
                ))}
              {onPreview && (
                <Button
                  variant="ghost"
                  size="sm"
                  className="ml-auto h-6 px-2 text-[var(--fs-xs)]"
                  onClick={(event) => {
                    event.stopPropagation();
                    onPreview();
                  }}
                  aria-label={`Preview ${title}`}
                >
                  <Eye className="h-3 w-3" strokeWidth={1.5} aria-hidden />
                  Preview
                </Button>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
