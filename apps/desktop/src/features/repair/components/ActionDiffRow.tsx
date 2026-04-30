import { ArrowRight, Plus } from 'lucide-react';
import { Switch } from '@/components/ui/switch';
import { Label } from '@/components/ui/label';
import { cn } from '@/lib/utils';
import type { RepairActionPreview } from '@/lib/api/types';

interface ActionDiffRowProps {
  action: RepairActionPreview;
  included: boolean;
  onIncludedChange: (next: boolean) => void;
}

function severityColor(severity: number): string {
  return severity >= 0.6 ? 'var(--danger)' : 'var(--warn)';
}

function severityLabel(severity: number): string {
  if (severity >= 0.6) return 'High severity';
  if (severity >= 0.3) return 'Medium severity';
  return 'Low severity';
}

export function ActionDiffRow({
  action,
  included,
  onIncludedChange,
}: ActionDiffRowProps): JSX.Element {
  const switchId = `repair-include-${action.id}`;
  const beforeText = action.before ?? '—';
  const afterText = action.after ?? '—';
  const isAddedAfter = action.before == null && action.after != null;

  return (
    <article
      className={cn(
        'rounded-[var(--r-2)] border border-[var(--border)] bg-[var(--surface-1)]',
        'transition-opacity duration-[var(--motion-fast)] ease-[var(--ease-out)]',
        !included && 'opacity-60',
      )}
    >
      <div className="flex items-start gap-3 p-4">
        <span
          aria-label={severityLabel(action.severity)}
          title={severityLabel(action.severity)}
          className="mt-1.5 inline-block h-2 w-2 shrink-0 rounded-full"
          style={{ backgroundColor: severityColor(action.severity) }}
        />

        <div className="flex min-w-0 flex-1 flex-col gap-3">
          <div className="text-label">{action.action_type.replace(/_/g, ' ')}</div>
          <h3 className="font-narrative text-[var(--fs-lg)] tracking-[var(--track-tight)] text-[var(--fg)]">
            {action.title}
          </h3>

          <div className="grid grid-cols-1 gap-2 sm:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] sm:items-stretch sm:gap-3">
            <div
              className={cn(
                'rounded-[var(--r-2)] border border-[var(--border)] px-3 py-2',
                'bg-[color-mix(in_oklch,var(--danger)_8%,var(--surface-1))]',
              )}
            >
              <div className="text-label">Before</div>
              <div
                className="mt-1 text-[var(--fs-sm)] text-[var(--fg)] [text-decoration-thickness:1px] [text-decoration-color:var(--fg-subtle)] line-through"
              >
                {beforeText}
              </div>
            </div>

            <div className="hidden items-center justify-center text-[var(--accent)] sm:flex">
              <ArrowRight className="h-4 w-4" strokeWidth={1.5} aria-hidden />
            </div>

            <div
              className={cn(
                'relative rounded-[var(--r-2)] border border-[var(--border)] py-2 pl-[14px] pr-3',
                'bg-[var(--accent-soft)]',
              )}
              style={{ boxShadow: 'inset 4px 0 0 var(--accent)' }}
            >
              <div className="text-label">After</div>
              <div className="mt-1 flex items-start gap-1.5 text-[var(--fs-sm)] text-[var(--fg)]">
                {isAddedAfter && (
                  <Plus
                    className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[var(--accent)]"
                    strokeWidth={1.5}
                    aria-label="Added"
                  />
                )}
                <span>{afterText}</span>
              </div>
            </div>
          </div>

          {action.reason && (
            <p className="font-narrative text-[var(--fs-sm)] italic text-[var(--fg-muted)]">
              {action.reason}
            </p>
          )}
        </div>

        <div className="flex shrink-0 items-center gap-2 pt-1">
          <Label htmlFor={switchId} className="cursor-pointer">
            Include
          </Label>
          <Switch
            id={switchId}
            checked={included}
            onCheckedChange={onIncludedChange}
            aria-label={`Include ${action.title} in repair`}
          />
        </div>
      </div>
    </article>
  );
}
