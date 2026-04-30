import { forwardRef, type ButtonHTMLAttributes } from 'react';
import { Slot } from '@radix-ui/react-slot';
import { cva, type VariantProps } from 'class-variance-authority';
import { cn } from '@/lib/utils';

const buttonVariants = cva(
  [
    'inline-flex items-center justify-center gap-2 whitespace-nowrap',
    'rounded-[var(--r-2)] font-medium num-tabular',
    'transition-[background-color,color,border-color,box-shadow,opacity]',
    'duration-[var(--motion-fast)] ease-[var(--ease-out)]',
    'select-none disabled:pointer-events-none disabled:opacity-50',
    'focus-visible:outline-none focus-visible:ring-2',
    'focus-visible:ring-[var(--accent)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--bg)]',
  ].join(' '),
  {
    variants: {
      variant: {
        default:
          'bg-[var(--accent)] text-[var(--accent-fg)] hover:brightness-[1.05] active:brightness-[0.97]',
        ghost:
          'text-[var(--fg)] hover:bg-[var(--surface-2)] active:bg-[var(--surface-3)]',
        outline:
          'border border-[var(--border)] bg-transparent text-[var(--fg)] hover:bg-[var(--surface-2)] hover:border-[var(--border-strong)]',
        subtle:
          'bg-[var(--surface-2)] text-[var(--fg)] hover:bg-[var(--surface-3)]',
        danger:
          'bg-[var(--danger)] text-[var(--accent-fg)] hover:brightness-[1.05]',
      },
      size: {
        sm: 'h-8 px-3 text-[var(--fs-sm)]',
        md: 'h-9 px-4 text-[var(--fs-base)]',
        lg: 'h-11 px-5 text-[var(--fs-md)]',
        icon: 'h-9 w-9 p-0',
      },
    },
    defaultVariants: {
      variant: 'default',
      size: 'md',
    },
  },
);

export interface ButtonProps
  extends ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : 'button';
    return (
      <Comp
        ref={ref as never}
        className={cn(buttonVariants({ variant, size }), className)}
        {...props}
      />
    );
  },
);
Button.displayName = 'Button';

export { buttonVariants };
