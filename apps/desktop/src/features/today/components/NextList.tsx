import { Fragment } from 'react';
import { Divider } from '@/components/ui/divider';
import type { TodayBlock } from '@/lib/api/types';
import { TimelineItemRow } from './TimelineItemRow';

interface NextListProps {
  block: TodayBlock;
  limit?: number;
}

export function NextList({ block, limit = 5 }: NextListProps): JSX.Element {
  const items = block.items.slice(0, limit);

  return (
    <section aria-label={block.title} className="flex flex-col gap-3">
      <SectionHeader title={block.title} subtitle={block.subtitle} />
      {items.length === 0 ? (
        <p className="font-narrative italic text-[var(--fs-md)] text-[var(--fg-muted)]">
          {block.empty_label || 'Nothing scheduled in the next few hours.'}
        </p>
      ) : (
        <ul className="flex flex-col">
          {items.map((item, idx) => (
            <Fragment key={item.id}>
              {idx > 0 && <Divider />}
              <li>
                <TimelineItemRow item={item} />
              </li>
            </Fragment>
          ))}
        </ul>
      )}
    </section>
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
