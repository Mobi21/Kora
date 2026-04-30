import { Badge } from '@/components/ui/badge';
import { Pill } from '@/components/ui/pill';
import { ProvenanceDot } from '@/components/ui/provenance-dot';
import { cn } from '@/lib/utils';
import type { TimelineItem } from '@/lib/api/types';
import {
  formatItemStartChip,
  formatItemTimeRange,
  resolveBadgeProvenance,
  resolveProvenance,
  riskPillStatus,
} from '../queries';

interface TimelineItemRowProps {
  item: TimelineItem;
  variant?: 'list' | 'compact';
  showSupportTags?: boolean;
  className?: string;
}

/**
 * Shared row element used by NextList and TimelineCollapsible.
 * Layout: [time chip] [title + subtitle] [provenance dot + risk pill]
 */
export function TimelineItemRow({
  item,
  variant = 'list',
  showSupportTags = true,
  className,
}: TimelineItemRowProps): JSX.Element {
  const provKind = resolveProvenance(item.provenance);
  const badgeProv = resolveBadgeProvenance(item.provenance);
  const range = formatItemTimeRange(item);
  const startChip = formatItemStartChip(item);
  const risk = riskPillStatus(item.risk);
  const subtitle = item.support_tags.length > 0 ? item.support_tags.join(' · ') : null;

  return (
    <div
      className={cn(
        'group flex w-full items-start gap-3 py-2.5',
        variant === 'compact' && 'py-1.5',
        className,
      )}
    >
      <div
        aria-hidden
        className="mt-1 inline-flex shrink-0 items-baseline gap-2 font-mono text-[var(--fs-xs)] text-[var(--fg-muted)] num-tabular"
      >
        <span className="inline-block min-w-[3.25rem] text-right">{startChip}</span>
      </div>

      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <span className="truncate text-[var(--fs-base)] font-medium text-[var(--fg)]">
          {item.title}
        </span>
        {(range || subtitle) && (
          <span className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[var(--fs-xs)] text-[var(--fg-muted)]">
            {range && <span className="font-mono num-tabular">{range}</span>}
            {range && subtitle && <span className="text-[var(--fg-subtle)]">·</span>}
            {showSupportTags && subtitle && <span className="truncate">{subtitle}</span>}
          </span>
        )}
        {showSupportTags && item.support_tags.length > 0 && variant === 'list' && (
          <div className="mt-0.5 flex flex-wrap items-center gap-1.5">
            {item.support_tags.slice(0, 4).map((tag) => (
              <Badge key={tag} provenance={badgeProv} className="opacity-90">
                {tag}
              </Badge>
            ))}
          </div>
        )}
      </div>

      <div className="ml-2 mt-1 flex shrink-0 items-center gap-2">
        <ProvenanceDot
          kind={provKind}
          size={6}
          aria-label={`${provKind} provenance`}
        />
        {risk && (
          <Pill status={risk.status} label={risk.label}>
            {risk.label}
          </Pill>
        )}
      </div>
    </div>
  );
}
