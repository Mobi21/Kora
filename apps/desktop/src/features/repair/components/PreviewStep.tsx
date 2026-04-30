import { ArrowLeft, Sparkles } from 'lucide-react';
import { Button } from '@/components/ui/button';
import type { RepairPreview } from '@/lib/api/types';
import { ActionDiffRow } from './ActionDiffRow';

interface PreviewStepProps {
  preview: RepairPreview;
  selectedIds: Set<string>;
  onSelectedChange: (next: Set<string>) => void;
  onCancel: () => void;
  onApply: () => void;
}

export function PreviewStep({
  preview,
  selectedIds,
  onSelectedChange,
  onCancel,
  onApply,
}: PreviewStepProps): JSX.Element {
  const hasActions = preview.actions.length > 0;
  const includedCount = preview.actions.filter((a) => selectedIds.has(a.id)).length;

  return (
    <section aria-labelledby="repair-preview" className="flex flex-col gap-6">
      <div className="flex flex-col gap-1.5">
        <h2
          id="repair-preview"
          className="font-narrative text-[var(--fs-2xl)] tracking-[var(--track-tight)] text-[var(--fg)]"
        >
          Here&rsquo;s what I&rsquo;d repair.
        </h2>
        {preview.summary && (
          <p className="font-narrative text-[var(--fs-md)] italic text-[var(--fg-muted)]">
            {preview.summary}
          </p>
        )}
      </div>

      {hasActions ? (
        <div className="flex flex-col gap-3">
          {preview.actions.map((action) => {
            const included = selectedIds.has(action.id);
            return (
              <ActionDiffRow
                key={action.id}
                action={action}
                included={included}
                onIncludedChange={(next) => {
                  const updated = new Set(selectedIds);
                  if (next) {
                    updated.add(action.id);
                  } else {
                    updated.delete(action.id);
                  }
                  onSelectedChange(updated);
                }}
              />
            );
          })}
        </div>
      ) : (
        <div
          className="rounded-[var(--r-2)] border border-[var(--border)] bg-[var(--surface-1)] p-6 text-center"
        >
          <p className="font-narrative text-[var(--fs-md)] italic text-[var(--fg-muted)]">
            Nothing here needs repair right now.
          </p>
        </div>
      )}

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div
          className="flex items-start gap-2 rounded-[var(--r-2)] py-2 pl-[14px] pr-3"
          style={{
            backgroundColor: 'var(--accent-soft)',
            boxShadow: 'inset 4px 0 0 var(--accent)',
          }}
        >
          <Sparkles
            className="mt-0.5 h-4 w-4 shrink-0 text-[var(--accent)]"
            strokeWidth={1.5}
            aria-hidden
          />
          <p className="font-narrative text-[var(--fs-sm)] italic text-[var(--fg)]">
            Preview only. Apply to confirm.
          </p>
        </div>

        <div className="flex items-center justify-end gap-2">
          <Button variant="ghost" onClick={onCancel} aria-label="Cancel and go back">
            <ArrowLeft className="h-4 w-4" strokeWidth={1.5} aria-hidden />
            Cancel
          </Button>
          <Button
            variant="default"
            onClick={onApply}
            disabled={!hasActions || includedCount === 0}
          >
            Apply repair
          </Button>
        </div>
      </div>
    </section>
  );
}
