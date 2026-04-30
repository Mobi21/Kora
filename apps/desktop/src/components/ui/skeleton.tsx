import { cn } from '@/lib/utils';
import type { HTMLAttributes } from 'react';

export function Skeleton({ className, ...props }: HTMLAttributes<HTMLDivElement>): JSX.Element {
  return (
    <div
      className={cn('kora-skeleton rounded-[var(--r-2)]', className)}
      aria-hidden
      {...props}
    />
  );
}
