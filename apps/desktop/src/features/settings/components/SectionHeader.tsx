import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

interface SectionHeaderProps {
  id: string;
  eyebrow?: string;
  title: string;
  description?: string;
  trailing?: ReactNode;
  className?: string;
}

/**
 * Standard header used at the top of every settings section. Renders a
 * Fraunces title + italic blurb, with an optional trailing slot for
 * actions (e.g. "Reset to defaults", "Rebuild projection").
 */
export function SectionHeader({
  id,
  eyebrow,
  title,
  description,
  trailing,
  className,
}: SectionHeaderProps): JSX.Element {
  return (
    <header
      id={id}
      className={cn(
        'flex scroll-mt-6 items-end justify-between gap-4 border-b border-[var(--border)] pb-4',
        className,
      )}
    >
      <div className="min-w-0 space-y-1">
        {eyebrow && (
          <p className="text-[var(--fs-2xs)] uppercase tracking-[var(--track-label)] text-[var(--fg-subtle)]">
            {eyebrow}
          </p>
        )}
        <h2 className="font-narrative text-[var(--fs-2xl)] tracking-[var(--track-tight)] text-[var(--fg)]">
          {title}
        </h2>
        {description && (
          <p className="font-narrative text-[var(--fs-md)] italic text-[var(--fg-muted)]">
            {description}
          </p>
        )}
      </div>
      {trailing && <div className="flex shrink-0 items-center gap-2">{trailing}</div>}
    </header>
  );
}
