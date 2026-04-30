import { RefreshCcw } from 'lucide-react';
import { cn } from '@/lib/utils';

interface RestartBadgeProps {
  className?: string;
  label?: string;
}

/**
 * Tiny pill that flags a setting as requiring a daemon restart to take
 * effect. Intentionally low-contrast — it's metadata, not an alarm.
 */
export function RestartBadge({
  className,
  label = 'restart required',
}: RestartBadgeProps): JSX.Element {
  return (
    <span
      role="status"
      aria-label={label}
      className={cn(
        'inline-flex items-center gap-1 rounded-[var(--r-pill)]',
        'border border-[var(--border)] bg-[var(--surface-2)]',
        'px-1.5 py-0.5 text-[var(--fs-2xs)] uppercase tracking-[var(--track-label)]',
        'text-[var(--fg-muted)]',
        className,
      )}
    >
      <RefreshCcw className="h-2.5 w-2.5" strokeWidth={1.5} aria-hidden />
      {label}
    </span>
  );
}
