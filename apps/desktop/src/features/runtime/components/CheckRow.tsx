import { useState } from 'react';
import { ChevronDown } from 'lucide-react';
import { Pill, type PillStatus } from '@/components/ui/pill';
import { cn } from '@/lib/utils';
import type { InspectDoctorCheck } from '@/lib/api/types';

const STATUS_FOR_CHECK = (passed: boolean): PillStatus => (passed ? 'ok' : 'degraded');
const STATUS_LABEL = (passed: boolean): string => (passed ? 'pass' : 'fail');

interface CheckRowProps {
  check: InspectDoctorCheck;
}

function humanize(name: string): string {
  return name
    .replace(/_/g, ' ')
    .replace(/\b([a-z])/g, (m) => m.toUpperCase());
}

export function CheckRow({ check }: CheckRowProps): JSX.Element {
  const [expanded, setExpanded] = useState(false);
  const hasDetail = !!check.detail;
  const status = STATUS_FOR_CHECK(check.passed);
  const rowId = `check-${check.name}`;

  return (
    <div
      className={cn(
        'group rounded-[var(--r-2)] border-l-[3px] pl-3 pr-2 py-2.5',
        'transition-colors duration-[var(--motion-fast)] ease-[var(--ease-out)]',
        'hover:bg-[var(--surface-2)]',
        check.passed
          ? 'border-l-[var(--ok)]'
          : 'border-l-[var(--danger)] bg-[color-mix(in_oklch,var(--danger)_4%,transparent)]',
      )}
    >
      <div className="flex items-center gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-[var(--fs-base)] font-medium text-[var(--fg)]">
              {humanize(check.name)}
            </span>
          </div>
          {check.detail && !expanded && (
            <p className="mt-0.5 truncate text-[var(--fs-sm)] text-[var(--fg-muted)]">
              {check.detail}
            </p>
          )}
        </div>
        <Pill status={status} label={STATUS_LABEL(check.passed)} />
        {hasDetail && (
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            aria-expanded={expanded}
            aria-controls={rowId}
            aria-label={expanded ? 'Collapse details' : 'Expand details'}
            className={cn(
              'inline-flex h-7 w-7 items-center justify-center rounded-[var(--r-1)]',
              'text-[var(--fg-muted)] hover:bg-[var(--surface-3)]',
              'focus-visible:outline-none focus-visible:ring-2',
              'focus-visible:ring-[var(--accent)] focus-visible:ring-offset-2',
              'focus-visible:ring-offset-[var(--bg)]',
              'transition-transform duration-[var(--motion-fast)] ease-[var(--ease-out)]',
              expanded && 'rotate-180',
            )}
          >
            <ChevronDown className="h-4 w-4" strokeWidth={1.5} />
          </button>
        )}
      </div>
      {hasDetail && expanded && (
        <pre
          id={rowId}
          className={cn(
            'mt-2 max-h-48 overflow-auto rounded-[var(--r-1)]',
            'border border-[var(--border)] bg-[var(--surface-2)]',
            'px-3 py-2 font-mono text-[var(--fs-xs)] text-[var(--fg)]',
            'whitespace-pre-wrap break-words',
          )}
        >
          {check.detail}
        </pre>
      )}
    </div>
  );
}
