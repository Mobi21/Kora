import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Pill } from '@/components/ui/pill';
import { cn } from '@/lib/utils';
import type { TodayBlock } from '@/lib/api/types';
import { useChatStore } from '@/lib/ws/store';
import {
  formatItemTimeRange,
  resolveBadgeProvenance,
  resolveProvenance,
} from '../queries';

interface NowBlockProps {
  block: TodayBlock;
}

const PROVENANCE_COLOR: Record<string, string> = {
  local: 'var(--provenance-local)',
  workspace: 'var(--provenance-workspace)',
  inferred: 'var(--provenance-inferred)',
  confirmed: 'var(--provenance-confirmed)',
  repair: 'var(--provenance-repair)',
};

export function NowBlock({ block }: NowBlockProps): JSX.Element {
  const items = block.items;
  const empty = items.length === 0;
  return (
    <section aria-label={block.title} className="flex flex-col gap-3">
      <SectionHeader title={block.title} subtitle={block.subtitle} />
      {empty ? <NowEmpty label={block.empty_label} /> : <NowFilled items={items} />}
    </section>
  );
}

function NowFilled({ items }: { items: TodayBlock['items'] }): JSX.Element {
  return (
    <Card className="overflow-hidden p-0 shadow-[var(--shadow-1)]">
      <ul className="divide-y divide-[var(--border)]">
        {items.map((item) => {
          const provKind = resolveProvenance(item.provenance);
          const badgeProv = resolveBadgeProvenance(item.provenance);
          const range = formatItemTimeRange(item);
          const ruleColor = PROVENANCE_COLOR[provKind] ?? 'var(--border-strong)';
          return (
            <li key={item.id} className="relative">
              <span
                aria-hidden
                className="absolute left-0 top-0 h-full w-1"
                style={{ background: ruleColor }}
              />
              <div className="flex items-start gap-4 py-4 pl-5 pr-[var(--pad)]">
                <div className="flex min-w-0 flex-1 flex-col gap-1.5">
                  <div className="flex items-center gap-3">
                    <h3
                      className={cn(
                        'truncate text-[length:1.0625rem] font-medium leading-tight',
                        'text-[var(--fg)]',
                      )}
                    >
                      {item.title}
                    </h3>
                    {range && (
                      <span className="font-mono text-[var(--fs-xs)] text-[var(--fg-muted)] num-tabular">
                        {range}
                      </span>
                    )}
                  </div>
                  {item.support_tags.length > 0 && (
                    <div className="flex flex-wrap items-center gap-1.5 pt-0.5">
                      {item.support_tags.slice(0, 5).map((tag) => (
                        <Badge key={tag} provenance={badgeProv}>
                          {tag}
                        </Badge>
                      ))}
                    </div>
                  )}
                </div>
                {item.risk === 'repair' && (
                  <Pill status="warn" label="needs check" className="mt-1 shrink-0">
                    needs check
                  </Pill>
                )}
                {item.risk === 'watch' && (
                  <Pill status="warn" label="watch" className="mt-1 shrink-0">
                    watch
                  </Pill>
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </Card>
  );
}

function NowEmpty({ label }: { label: string }): JSX.Element {
  const setOpen = useChatStore((s) => s.setPanelOpen);
  return (
    <div
      className={cn(
        'flex flex-col items-start gap-3 rounded-[var(--r-2)] border border-dashed',
        'border-[var(--border-strong)] bg-[var(--surface-1)]',
        'px-[var(--pad)] py-5',
      )}
    >
      <p className="font-narrative italic text-[var(--fs-md)] text-[var(--fg-muted)]">
        {label || 'Nothing anchored right now.'}
      </p>
      <Button
        size="sm"
        variant="outline"
        onClick={() => setOpen(true)}
        aria-label="Open chat to anchor what you'd like to do now"
      >
        Open chat to anchor
      </Button>
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
