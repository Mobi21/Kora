import { forwardRef, type HTMLAttributes } from 'react';
import { cva, type VariantProps } from 'class-variance-authority';
import { cn } from '@/lib/utils';

const badgeVariants = cva(
  [
    'inline-flex items-center gap-1.5 rounded-[var(--r-pill)]',
    'border-l-[3px] py-0.5 pl-2 pr-2.5 text-[var(--fs-xs)]',
    'bg-[var(--surface-2)] text-[var(--fg)]',
  ].join(' '),
  {
    variants: {
      provenance: {
        local: 'border-l-[var(--provenance-local)]',
        workspace: 'border-l-[var(--provenance-workspace)]',
        inferred: 'border-l-[var(--provenance-inferred)]',
        confirmed: 'border-l-[var(--provenance-confirmed)]',
        repair: 'border-l-[var(--provenance-repair)]',
        neutral: 'border-l-[var(--border-strong)]',
      },
    },
    defaultVariants: {
      provenance: 'neutral',
    },
  },
);

export type BadgeProvenance = NonNullable<VariantProps<typeof badgeVariants>['provenance']>;

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement>, VariantProps<typeof badgeVariants> {}

const PROVENANCE_DOT: Record<BadgeProvenance, string> = {
  local: 'var(--provenance-local)',
  workspace: 'var(--provenance-workspace)',
  inferred: 'var(--provenance-inferred)',
  confirmed: 'var(--provenance-confirmed)',
  repair: 'var(--provenance-repair)',
  neutral: 'var(--border-strong)',
};

export const Badge = forwardRef<HTMLSpanElement, BadgeProps>(
  ({ className, provenance, children, ...props }, ref) => {
    const dotColor = PROVENANCE_DOT[(provenance ?? 'neutral') as BadgeProvenance];
    return (
      <span ref={ref} className={cn(badgeVariants({ provenance }), className)} {...props}>
        <span
          aria-hidden
          className="inline-block h-1.5 w-1.5 rounded-full"
          style={{ background: dotColor }}
        />
        {children}
      </span>
    );
  },
);
Badge.displayName = 'Badge';

export { badgeVariants };
