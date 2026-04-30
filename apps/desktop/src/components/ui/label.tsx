import { forwardRef, type LabelHTMLAttributes } from 'react';
import { cn } from '@/lib/utils';

export const Label = forwardRef<HTMLLabelElement, LabelHTMLAttributes<HTMLLabelElement>>(
  ({ className, ...props }, ref) => (
    <label
      ref={ref}
      className={cn(
        'text-[var(--fs-xs)] uppercase tracking-[0.02em] text-[var(--fg-muted)]',
        className,
      )}
      {...props}
    />
  ),
);
Label.displayName = 'Label';
