import { cn } from '@/lib/utils';

interface DividerProps {
  label?: string;
  className?: string;
}

export function Divider({ label, className }: DividerProps): JSX.Element {
  if (!label) {
    return (
      <hr
        role="separator"
        className={cn('h-px w-full border-0 bg-[var(--border)]', className)}
      />
    );
  }
  return (
    <div
      role="separator"
      className={cn('flex items-center gap-3 text-[var(--fg-muted)]', className)}
    >
      <span className="h-px flex-1 bg-[var(--border)]" />
      <span className="text-[var(--fs-2xs)] uppercase tracking-[0.02em]">{label}</span>
      <span className="h-px flex-1 bg-[var(--border)]" />
    </div>
  );
}
