import { useEffect, useMemo, useState } from 'react';
import { AlertTriangle, CheckCircle2, Loader2 } from 'lucide-react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Pill } from '@/components/ui/pill';
import { cn } from '@/lib/utils';
import type { RepairActionPreview, RepairApplyResult } from '@/lib/api/types';

interface ConfirmDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  actions: RepairActionPreview[];
  isPending: boolean;
  result: RepairApplyResult | null;
  errorMessage: string | null;
  onConfirm: () => void;
}

const CONFIRM_PHRASE = 'APPLY';

interface SummaryGroup {
  key: string;
  label: string;
  count: number;
}

function groupActions(actions: RepairActionPreview[]): SummaryGroup[] {
  const groups = new Map<string, SummaryGroup>();
  for (const action of actions) {
    const key = action.action_type || 'repair';
    const existing = groups.get(key);
    if (existing) {
      existing.count += 1;
    } else {
      groups.set(key, {
        key,
        label: humanLabel(key),
        count: 1,
      });
    }
  }
  return Array.from(groups.values());
}

function humanLabel(actionType: string): string {
  const map: Record<string, string> = {
    move_to_tomorrow: 'moved to tomorrow',
    make_smaller: 'made smaller',
    add_buffer: 'buffer added',
    confirm_or_drop: 'awaiting confirmation',
    defer_nonessential: 'deferred',
    behind: 'caught up',
    tired: 'eased for low energy',
    event_changed: 'reconciled with calendar',
    skipped: 'logged as skipped',
  };
  return map[actionType] ?? actionType.replace(/_/g, ' ');
}

export function ConfirmDialog({
  open,
  onOpenChange,
  actions,
  isPending,
  result,
  errorMessage,
  onConfirm,
}: ConfirmDialogProps): JSX.Element {
  const [phrase, setPhrase] = useState('');
  const [checkboxConfirmed, setCheckboxConfirmed] = useState(false);

  useEffect(() => {
    if (!open) {
      setPhrase('');
      setCheckboxConfirmed(false);
    }
  }, [open]);

  const groups = useMemo(() => groupActions(actions), [actions]);

  const phraseMatches = phrase.trim().toUpperCase() === CONFIRM_PHRASE;
  const canConfirm = (phraseMatches || checkboxConfirmed) && !isPending && actions.length > 0;

  const isUnavailable = result?.status === 'unavailable';
  const isSkipped = result?.status === 'skipped';
  const isApplied = result?.status === 'applied';

  return (
    <Dialog open={open} onOpenChange={(next) => !isPending && onOpenChange(next)}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Apply repair?</DialogTitle>
          <DialogDescription>
            These changes will be applied to today&rsquo;s plan. Kora keeps a record so you can
            see what changed.
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-3 pt-2">
          {groups.length > 0 && (
            <ul className="flex flex-col gap-1.5" aria-label="Repair summary">
              {groups.map((group) => (
                <li key={group.key} className="flex items-center gap-2">
                  <Pill status="ok" label={group.label}>
                    <span className="font-mono num-tabular">{group.count}</span>
                    <span>{group.label}</span>
                  </Pill>
                </li>
              ))}
            </ul>
          )}

          {!isApplied && !isUnavailable && !isSkipped && (
            <div className="flex flex-col gap-2 pt-2">
              <Label htmlFor="repair-confirm-input" className="text-[var(--fs-xs)]">
                Type <span className="font-mono">{CONFIRM_PHRASE}</span> to confirm
              </Label>
              <Input
                id="repair-confirm-input"
                value={phrase}
                onChange={(event) => setPhrase(event.target.value)}
                autoComplete="off"
                spellCheck={false}
                disabled={isPending}
                placeholder={CONFIRM_PHRASE}
              />
              <label className="mt-1 inline-flex cursor-pointer items-center gap-2 text-[var(--fs-sm)] text-[var(--fg-muted)]">
                <input
                  type="checkbox"
                  className="h-3.5 w-3.5 cursor-pointer accent-[var(--accent)]"
                  checked={checkboxConfirmed}
                  onChange={(event) => setCheckboxConfirmed(event.target.checked)}
                  disabled={isPending}
                />
                <span>I confirm the changes</span>
              </label>
            </div>
          )}

          {errorMessage && !result && (
            <div
              role="alert"
              className={cn(
                'flex items-start gap-2 rounded-[var(--r-2)] border border-[var(--border)]',
                'bg-[color-mix(in_oklch,var(--danger)_10%,var(--surface-1))] p-3',
              )}
            >
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-[var(--danger)]" strokeWidth={1.5} aria-hidden />
              <p className="text-[var(--fs-sm)] text-[var(--fg)]">{errorMessage}</p>
            </div>
          )}

          {isUnavailable && result && (
            <div
              role="status"
              className={cn(
                'flex items-start gap-2 rounded-[var(--r-2)] border border-[var(--border)]',
                'bg-[color-mix(in_oklch,var(--warn)_12%,var(--surface-1))] p-3',
              )}
            >
              <Pill status="warn" label="Unavailable">
                Unavailable
              </Pill>
              <p className="text-[var(--fs-sm)] text-[var(--fg)]">{result.message}</p>
            </div>
          )}

          {isSkipped && result && (
            <div
              role="status"
              className={cn(
                'flex items-start gap-2 rounded-[var(--r-2)] border border-[var(--border)]',
                'bg-[color-mix(in_oklch,var(--warn)_8%,var(--surface-1))] p-3',
              )}
            >
              <Pill status="warn" label="Skipped">
                Skipped
              </Pill>
              <p className="text-[var(--fs-sm)] text-[var(--fg)]">
                {result.message || 'No changes were applied.'}
              </p>
            </div>
          )}

          {isApplied && result && (
            <div
              role="status"
              className={cn(
                'flex items-start gap-2 rounded-[var(--r-2)] border border-[var(--border)]',
                'bg-[color-mix(in_oklch,var(--ok)_10%,var(--surface-1))] p-3',
              )}
            >
              <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-[var(--ok)]" strokeWidth={1.5} aria-hidden />
              <p className="text-[var(--fs-sm)] text-[var(--fg)]">{result.message || 'Repair applied.'}</p>
            </div>
          )}
        </div>

        <DialogFooter>
          {isUnavailable || isSkipped ? (
            <Button variant="ghost" onClick={() => onOpenChange(false)}>
              Close
            </Button>
          ) : isApplied ? (
            <Button variant="default" onClick={() => onOpenChange(false)}>
              Done
            </Button>
          ) : (
            <>
              <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={isPending}>
                Cancel
              </Button>
              <Button variant="default" onClick={onConfirm} disabled={!canConfirm}>
                {isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" strokeWidth={1.5} aria-hidden />
                ) : null}
                {isPending ? 'Applying…' : 'Apply repair'}
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
