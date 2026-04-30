import { forwardRef, type HTMLAttributes } from 'react';
import { cn } from '@/lib/utils';

export type PillStatus = 'ok' | 'warn' | 'degraded' | 'unknown';

const COLOR: Record<PillStatus, string> = {
  ok: 'var(--ok)',
  warn: 'var(--warn)',
  degraded: 'var(--danger)',
  unknown: 'var(--fg-subtle)',
};

interface ShapeProps {
  status: PillStatus;
}

function StatusShape({ status }: ShapeProps): JSX.Element {
  const fill = COLOR[status];
  switch (status) {
    case 'ok':
      return (
        <svg aria-hidden width="10" height="10" viewBox="0 0 10 10">
          <circle cx="5" cy="5" r="4" fill={fill} />
        </svg>
      );
    case 'warn':
      return (
        <svg aria-hidden width="10" height="10" viewBox="0 0 10 10">
          <polygon points="5,1 9,9 1,9" fill={fill} />
        </svg>
      );
    case 'degraded':
      return (
        <svg aria-hidden width="10" height="10" viewBox="0 0 10 10">
          <rect x="1.5" y="1.5" width="7" height="7" fill={fill} />
        </svg>
      );
    case 'unknown':
    default:
      return (
        <svg aria-hidden width="10" height="10" viewBox="0 0 10 10">
          <polygon points="5,1 9,5 5,9 1,5" fill={fill} />
        </svg>
      );
  }
}

export interface PillProps extends HTMLAttributes<HTMLSpanElement> {
  status: PillStatus;
  label?: string;
}

export const Pill = forwardRef<HTMLSpanElement, PillProps>(
  ({ status, label, className, children, ...props }, ref) => (
    <span
      ref={ref}
      role="status"
      aria-label={label ?? status}
      className={cn(
        'inline-flex items-center gap-1.5 rounded-[var(--r-pill)] border border-[var(--border)]',
        'bg-[var(--surface-1)] px-2 py-0.5 text-[var(--fs-xs)] text-[var(--fg)] num-tabular',
        className,
      )}
      {...props}
    >
      <StatusShape status={status} />
      {children ?? label ?? status}
    </span>
  ),
);
Pill.displayName = 'Pill';
