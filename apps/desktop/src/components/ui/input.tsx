import { forwardRef, type InputHTMLAttributes } from 'react';
import { cn } from '@/lib/utils';

export type InputProps = InputHTMLAttributes<HTMLInputElement>;

export const Input = forwardRef<HTMLInputElement, InputProps>(({ className, type, ...props }, ref) => (
  <input
    ref={ref}
    type={type}
    className={cn(
      'flex h-9 w-full rounded-[var(--r-2)] border border-[var(--border)]',
      'bg-[var(--surface-1)] px-3 text-[var(--fs-base)] text-[var(--fg)]',
      'placeholder:text-[var(--fg-subtle)]',
      'transition-[border-color,box-shadow] duration-[var(--motion-fast)] ease-[var(--ease-out)]',
      'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]',
      'focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--bg)]',
      'disabled:cursor-not-allowed disabled:opacity-50',
      className,
    )}
    {...props}
  />
));
Input.displayName = 'Input';
