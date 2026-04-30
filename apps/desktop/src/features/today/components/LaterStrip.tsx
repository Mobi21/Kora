import { ScrollArea } from '@/components/ui/scroll-area';
import { cn } from '@/lib/utils';
import type { TimelineItem, TodayBlock } from '@/lib/api/types';
import { formatItemStartChip, resolveProvenance } from '../queries';

interface LaterStripProps {
  block: TodayBlock;
}

const PROVENANCE_COLOR: Record<string, string> = {
  local: 'var(--provenance-local)',
  workspace: 'var(--provenance-workspace)',
  inferred: 'var(--provenance-inferred)',
  confirmed: 'var(--provenance-confirmed)',
  repair: 'var(--provenance-repair)',
};

export function LaterStrip({ block }: LaterStripProps): JSX.Element {
  const { items } = block;
  const useScroll = items.length > 6;

  return (
    <section aria-label={block.title} className="flex flex-col gap-3">
      <SectionHeader title={block.title} subtitle={block.subtitle} />
      {items.length === 0 ? (
        <p className="font-narrative italic text-[var(--fs-md)] text-[var(--fg-muted)]">
          {block.empty_label || 'Later opens up.'}
        </p>
      ) : useScroll ? (
        <ScrollArea className="-mx-1 max-w-full">
          <div className="flex w-max gap-2 px-1 pb-2">
            {items.map((item) => (
              <ChipCard key={item.id} item={item} />
            ))}
          </div>
        </ScrollArea>
      ) : (
        <div className="flex flex-wrap gap-2">
          {items.map((item) => (
            <ChipCard key={item.id} item={item} />
          ))}
        </div>
      )}
    </section>
  );
}

function ChipCard({ item }: { item: TimelineItem }): JSX.Element {
  const provKind = resolveProvenance(item.provenance);
  const ruleColor = PROVENANCE_COLOR[provKind] ?? 'var(--border-strong)';
  const time = formatItemStartChip(item);
  const isFlexible = !item.starts_at;

  return (
    <div
      className={cn(
        'group relative flex min-w-[10.5rem] max-w-[16rem] items-stretch overflow-hidden',
        'rounded-[var(--r-1)] border border-[var(--border)] bg-[var(--surface-1)]',
        'transition-colors duration-[var(--motion-fast)] ease-[var(--ease-out)]',
        'hover:border-[var(--border-strong)] hover:bg-[var(--surface-2)]',
      )}
    >
      <span
        aria-hidden
        className="block w-1 shrink-0"
        style={{ background: ruleColor }}
      />
      <div className="flex min-w-0 flex-1 flex-col gap-1 px-2.5 py-2">
        <span className="truncate text-[var(--fs-sm)] font-medium text-[var(--fg)]">
          {item.title}
        </span>
        <span
          className={cn(
            'inline-flex items-center gap-1 self-start',
            'rounded-[var(--r-pill)] border border-[var(--border)] bg-[var(--surface-2)]',
            'px-1.5 py-0.5 font-mono text-[var(--fs-2xs)] num-tabular',
            isFlexible ? 'text-[var(--fg-muted)]' : 'text-[var(--fg)]',
          )}
        >
          {isFlexible ? 'flexible' : time}
        </span>
      </div>
    </div>
  );
}

function SectionHeader({
  title,
  subtitle,
}: {
  title: string;
  subtitle: string | null;
}): JSX.Element {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <h2 className="font-narrative text-[var(--fs-xl)] leading-tight tracking-[var(--track-tight)] text-[var(--fg)]">
        {title}
      </h2>
      {subtitle && (
        <span className="text-[var(--fs-sm)] text-[var(--fg-muted)] truncate">{subtitle}</span>
      )}
    </div>
  );
}
