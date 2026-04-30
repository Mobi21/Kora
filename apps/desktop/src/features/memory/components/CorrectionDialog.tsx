import { useEffect, useMemo, useState } from 'react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Pill } from '@/components/ui/pill';
import { Skeleton } from '@/components/ui/skeleton';
import { AlertTriangle, CheckCircle2 } from 'lucide-react';
import type {
  VaultCorrectionPreview,
  VaultCorrectionRequest,
  VaultCorrectionResult,
  VaultMemoryItem,
} from '@/lib/api/types';
import { ApiError } from '@/lib/api/client';
import { cn } from '@/lib/utils';
import {
  useVaultCorrectionApply,
  useVaultCorrectionPreview,
} from '../queries';
import type { RowOperation } from './RowActions';
import { CertaintyDot } from './CertaintyDot';
import { certaintyVisual, formatRelativeOr } from '../utils/format';

const TITLES: Record<RowOperation, string> = {
  correct: 'Correct memory',
  merge: 'Merge memories',
  delete: 'Delete memory',
  confirm: 'Confirm memory',
  mark_stale: 'Mark as stale',
};

const DESCRIPTIONS: Record<RowOperation, string> = {
  correct: 'Replace the recorded text with the corrected version.',
  merge: 'Combine this memory into another.',
  delete: 'Remove this memory from the vault. Provenance is preserved.',
  confirm: 'Promote this memory from a guess to confirmed.',
  mark_stale: 'Mark this memory as stale so Kora can refresh it.',
};

interface CorrectionDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  memory: VaultMemoryItem | null;
  operation: RowOperation;
}

export function CorrectionDialog({
  open,
  onOpenChange,
  memory,
  operation,
}: CorrectionDialogProps): JSX.Element {
  const previewMut = useVaultCorrectionPreview();
  const applyMut = useVaultCorrectionApply();
  const [newText, setNewText] = useState('');
  const [note, setNote] = useState('');
  const [preview, setPreview] = useState<VaultCorrectionPreview | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [applyResult, setApplyResult] = useState<VaultCorrectionResult | null>(null);

  const initialText = memory?.body_preview ?? '';
  const requiresText = operation === 'correct';

  useEffect(() => {
    if (!open || !memory) return;
    setNewText(initialText);
    setNote('');
    setPreview(null);
    setPreviewError(null);
    setApplyResult(null);
    previewMut.reset();
    applyMut.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, memory?.id, operation]);

  const requestBase = useMemo<VaultCorrectionRequest | null>(() => {
    if (!memory) return null;
    return {
      memory_id: memory.id,
      operation,
      new_text: requiresText ? newText : null,
      note: note || null,
    };
  }, [memory, operation, newText, note, requiresText]);

  // Optimistic preview: fetch on open and whenever text/note change for ops
  // that take input. Other ops only fire once.
  useEffect(() => {
    if (!open || !requestBase) return;
    if (requiresText && !newText.trim()) {
      setPreview(null);
      return;
    }
    let cancelled = false;
    setPreviewError(null);
    previewMut.mutate(requestBase, {
      onSuccess: (data) => {
        if (cancelled) return;
        setPreview(data);
      },
      onError: (err) => {
        if (cancelled) return;
        setPreviewError(extractMessage(err));
        setPreview(null);
      },
    });
    return () => {
      cancelled = true;
    };
    // We deliberately omit previewMut to avoid re-firing on identity changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, requestBase?.memory_id, requestBase?.operation, requestBase?.new_text, requestBase?.note]);

  if (!memory) {
    // Render nothing when there's no memory; the dialog won't be opened
    // without one, but defend against transient parent state.
    return <></>;
  }

  const before = preview?.before ?? memory;
  const after = preview?.after ?? null;
  const isDelete = operation === 'delete';
  const isPreviewLoading = previewMut.isPending && !preview;
  const applyDisabled =
    applyResult?.status === 'applied' || applyMut.isPending || (requiresText && !newText.trim());

  const applyLabel = (() => {
    if (applyMut.isPending) return 'Applying…';
    if (applyResult?.status === 'unavailable') return 'Backend not yet wired';
    if (applyResult?.status === 'applied') return 'Applied';
    return defaultApplyLabel(operation);
  })();

  function handleApply() {
    if (!requestBase) return;
    setApplyResult(null);
    applyMut.mutate(requestBase, {
      onSuccess: (data) => setApplyResult(data),
    });
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{TITLES[operation]}</DialogTitle>
          <DialogDescription>{DESCRIPTIONS[operation]}</DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-4 pt-2">
          {isDelete ? (
            <DeleteBanner />
          ) : (
            <BeforeAfterDiff
              before={before}
              after={after}
              loading={isPreviewLoading}
              previewSummary={preview?.summary ?? null}
            />
          )}

          {requiresText && (
            <div className="flex flex-col gap-1.5">
              <label
                htmlFor="memory-correction-new-text"
                className="text-[var(--fs-2xs)] uppercase tracking-[var(--track-label)] text-[var(--fg-muted)]"
              >
                Corrected text
              </label>
              <textarea
                id="memory-correction-new-text"
                value={newText}
                onChange={(e) => setNewText(e.target.value)}
                rows={4}
                className={cn(
                  'min-h-[96px] resize-y rounded-[var(--r-2)] border border-[var(--border)]',
                  'bg-[var(--surface-1)] px-3 py-2 text-[var(--fs-base)] text-[var(--fg)]',
                  'placeholder:text-[var(--fg-subtle)]',
                  'transition-[border-color,box-shadow] duration-[var(--motion-fast)] ease-[var(--ease-out)]',
                  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]',
                  'focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--bg)]',
                )}
                placeholder="Write the corrected memory…"
              />
            </div>
          )}

          {(operation === 'mark_stale' || operation === 'confirm' || operation === 'delete') && (
            <div className="flex flex-col gap-1.5">
              <label
                htmlFor="memory-correction-note"
                className="text-[var(--fs-2xs)] uppercase tracking-[var(--track-label)] text-[var(--fg-muted)]"
              >
                Note (optional)
              </label>
              <textarea
                id="memory-correction-note"
                value={note}
                onChange={(e) => setNote(e.target.value)}
                rows={2}
                className={cn(
                  'min-h-[64px] resize-y rounded-[var(--r-2)] border border-[var(--border)]',
                  'bg-[var(--surface-1)] px-3 py-2 text-[var(--fs-base)] text-[var(--fg)]',
                  'placeholder:text-[var(--fg-subtle)]',
                  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]',
                  'focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--bg)]',
                )}
                placeholder="Why are you doing this?"
              />
            </div>
          )}

          {previewError && (
            <div
              role="alert"
              className={cn(
                'flex items-start gap-2 rounded-[var(--r-1)] border border-l-[3px] px-3 py-2',
                'border-[var(--border)] border-l-[var(--danger)] bg-[var(--surface-1)]',
                'text-[var(--fs-xs)] text-[var(--fg)]',
              )}
            >
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 text-[var(--danger)]" strokeWidth={1.5} />
              <span>Preview failed — {previewError}</span>
            </div>
          )}
        </div>

        <DialogFooter>
          <ApplyResultPill result={applyResult} />
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            onClick={handleApply}
            disabled={applyDisabled}
            variant={isDelete ? 'danger' : 'default'}
            aria-label={applyLabel}
          >
            {applyLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function defaultApplyLabel(op: RowOperation): string {
  switch (op) {
    case 'delete':
      return 'Delete';
    case 'merge':
      return 'Merge';
    case 'mark_stale':
      return 'Mark stale';
    case 'confirm':
      return 'Confirm';
    case 'correct':
    default:
      return 'Apply correction';
  }
}

function extractMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return 'Unknown error';
}

interface BeforeAfterDiffProps {
  before: VaultMemoryItem;
  after: VaultMemoryItem | null;
  loading: boolean;
  previewSummary: string | null;
}

function BeforeAfterDiff({
  before,
  after,
  loading,
  previewSummary,
}: BeforeAfterDiffProps): JSX.Element {
  return (
    <div className="flex flex-col gap-3">
      {previewSummary && (
        <p className="text-[var(--fs-sm)] text-[var(--fg-muted)]">{previewSummary}</p>
      )}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <DiffPanel label="Before" memory={before} />
        {loading ? (
          <DiffSkeleton label="After" />
        ) : after ? (
          <DiffPanel label="After" memory={after} highlight />
        ) : (
          <DiffPanel label="After" memory={before} placeholder />
        )}
      </div>
    </div>
  );
}

interface DiffPanelProps {
  label: string;
  memory: VaultMemoryItem;
  highlight?: boolean;
  placeholder?: boolean;
}

function DiffPanel({ label, memory, highlight, placeholder }: DiffPanelProps): JSX.Element {
  const visual = certaintyVisual(memory.certainty);
  return (
    <div
      className={cn(
        'relative flex flex-col gap-2 rounded-[var(--r-2)] border px-3 py-3',
        'bg-[var(--surface-1)]',
        highlight ? 'border-[var(--accent-soft)]' : 'border-[var(--border)]',
      )}
    >
      <span
        aria-hidden
        className="absolute inset-y-2 left-0 w-[3px] rounded-[var(--r-pill)]"
        style={{ background: visual.color }}
      />
      <div className="flex items-center justify-between gap-2 pl-2">
        <span className="text-[var(--fs-2xs)] uppercase tracking-[var(--track-label)] text-[var(--fg-muted)]">
          {label}
        </span>
        <span className="flex items-center gap-1.5 text-[var(--fs-2xs)] text-[var(--fg-muted)]">
          <CertaintyDot certainty={memory.certainty} size={6} />
          {visual.label}
        </span>
      </div>
      <h4 className="font-narrative text-[var(--fs-md)] tracking-[var(--track-tight)] text-[var(--fg)] pl-2">
        {memory.title}
      </h4>
      <p
        className={cn(
          'pl-2 font-narrative text-[var(--fs-sm)] text-[var(--fg)]',
          placeholder && 'italic text-[var(--fg-subtle)]',
        )}
      >
        {placeholder ? 'No preview returned.' : memory.body_preview}
      </p>
      <span className="pl-2 font-mono num-tabular text-[var(--fs-2xs)] text-[var(--fg-subtle)]">
        {formatRelativeOr(memory.updated_at, '—')}
      </span>
    </div>
  );
}

function DiffSkeleton({ label }: { label: string }): JSX.Element {
  return (
    <div className="flex flex-col gap-2 rounded-[var(--r-2)] border border-[var(--border)] bg-[var(--surface-1)] px-3 py-3">
      <span className="text-[var(--fs-2xs)] uppercase tracking-[var(--track-label)] text-[var(--fg-muted)]">
        {label}
      </span>
      <Skeleton className="h-4 w-2/3" />
      <Skeleton className="h-3 w-full" />
      <Skeleton className="h-3 w-5/6" />
    </div>
  );
}

function DeleteBanner(): JSX.Element {
  return (
    <div
      role="alert"
      className={cn(
        'flex items-start gap-3 rounded-[var(--r-2)] border border-l-[3px] px-3 py-3',
        'border-[var(--border)] border-l-[var(--danger)] bg-[var(--surface-1)]',
      )}
    >
      <AlertTriangle className="mt-0.5 h-4 w-4 text-[var(--danger)]" strokeWidth={1.5} />
      <div className="flex flex-col gap-0.5">
        <p className="text-[var(--fs-sm)] text-[var(--fg)]">This will remove the memory.</p>
        <p className="text-[var(--fs-xs)] text-[var(--fg-muted)]">
          Provenance is preserved in the audit log so you can recover later.
        </p>
      </div>
    </div>
  );
}

function ApplyResultPill({ result }: { result: VaultCorrectionResult | null }): JSX.Element {
  if (!result) return <span className="mr-auto" />;
  if (result.status === 'unavailable') {
    return (
      <span
        className={cn(
          'mr-auto inline-flex items-center gap-1.5 rounded-[var(--r-pill)] border border-[var(--border)]',
          'bg-[var(--surface-1)] px-2 py-0.5 text-[var(--fs-xs)] text-[var(--fg)] num-tabular',
        )}
        role="status"
      >
        <Pill status="warn" label="unavailable" />
        <span className="text-[var(--fg-muted)]">{result.message}</span>
      </span>
    );
  }
  if (result.status === 'applied') {
    return (
      <span className="mr-auto inline-flex items-center gap-1.5 text-[var(--fs-xs)] text-[var(--ok)]">
        <CheckCircle2 className="h-3.5 w-3.5" strokeWidth={1.5} />
        {result.message || 'Applied'}
      </span>
    );
  }
  return (
    <span className="mr-auto text-[var(--fs-xs)] text-[var(--fg-muted)]">
      {result.message}
    </span>
  );
}
