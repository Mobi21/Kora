import { cn } from '@/lib/utils';
import type { HTMLAttributes } from 'react';

export function Kbd({
  className,
  children,
  ...props
}: HTMLAttributes<HTMLElement>): JSX.Element {
  return (
    <kbd
      className={cn(
        'font-mono inline-flex items-center justify-center',
        'min-w-[1.5rem] rounded-[var(--r-1)] border border-[var(--border)]',
        'bg-[var(--surface-2)] px-1.5 py-0.5',
        'text-[var(--fs-2xs)] text-[var(--fg-muted)]',
        className,
      )}
      {...props}
    >
      {children}
    </kbd>
  );
}
