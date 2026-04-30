import { useEffect, useRef, useState, type ReactElement } from 'react';
import { AlertTriangle } from 'lucide-react';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Pill } from '@/components/ui/pill';
import { Skeleton } from '@/components/ui/skeleton';
import type {
  MedicationDose,
  MedicationLogPreview,
  MedicationLogResult,
} from '@/lib/api/types';
import { useMedicationApply, useMedicationPreview } from '../queries';
import { statusLabel } from '../utils/format';

type ActionStatus = 'taken' | 'skipped' | 'missed';

interface DoseActionPopoverProps {
  dose: MedicationDose;
  status: ActionStatus;
  trigger: ReactElement;
  triggerLabel: string;
}

export function DoseActionPopover({
  dose,
  status,
  trigger,
  triggerLabel,
}: DoseActionPopoverProps): JSX.Element {
  const [open, setOpen] = useState<boolean>(false);
  const [note, setNote] = useState<string>('');
  const [result, setResult] = useState<MedicationLogResult | null>(null);
  const [preview, setPreview] = useState<MedicationLogPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const previewMut = useMedicationPreview();
  const applyMut = useMedicationApply();
  const requested = useRef<boolean>(false);

  useEffect(() => {
    if (!open) {
      setNote('');
      setResult(null);
      setPreview(null);
      setError(null);
      requested.current = false;
      return;
    }
    if (requested.current) return;
    requested.current = true;
    previewMut
      .mutateAsync({ dose_id: dose.id, status })
      .then((p) => setPreview(p))
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : String(err));
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const handleConfirm = async (): Promise<void> => {
    setError(null);
    try {
      const res = await applyMut.mutateAsync({
        dose_id: dose.id,
        status,
        note: note.trim() ? note.trim() : null,
      });
      setResult(res);
      if (res.status === 'applied') {
        window.setTimeout(() => setOpen(false), 750);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const isPreviewLoading = previewMut.isPending && !preview && !error;
  const isApplying = applyMut.isPending;
  const applied = result?.status === 'applied';
  const unavailable = result?.status === 'unavailable';

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>{trigger}</PopoverTrigger>
      <PopoverContent align="end" className="w-80 space-y-3">
        <div className="space-y-1">
          <div className="font-narrative text-[var(--fs-md)] tracking-[var(--track-tight)] text-[var(--fg)]">
            {triggerLabel}
            {' · '}
            <span className="italic text-[var(--fg-muted)]">{dose.name}</span>
          </div>
          {isPreviewLoading && <Skeleton className="h-4 w-full" />}
          {preview && !isPreviewLoading && (
            <p className="font-narrative text-[var(--fs-sm)] italic text-[var(--fg-muted)]">
              {preview.summary || `Will mark as ${statusLabel(status).toLowerCase()}.`}
            </p>
          )}
          {error && !preview && (
            <p className="text-[var(--fs-xs)] text-[var(--danger)]">{error}</p>
          )}
        </div>

        <div className="space-y-1.5">
          <label
            htmlFor={`dose-note-${dose.id}`}
            className="text-[var(--fs-xs)] text-[var(--fg-muted)]"
          >
            Add a note?
          </label>
          <Input
            id={`dose-note-${dose.id}`}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Optional"
            disabled={applied || isApplying}
          />
        </div>

        {unavailable && (
          <div className="flex items-start gap-2 rounded-[var(--r-1)] border border-[var(--border)] bg-[var(--surface-2)] p-2 text-[var(--fs-xs)] text-[var(--fg-muted)]">
            <AlertTriangle
              className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[var(--warn)]"
              strokeWidth={1.75}
              aria-hidden="true"
            />
            <div className="space-y-1">
              <Pill status="warn" label="Logging not yet wired — preview only" />
              {result?.message && (
                <p className="text-[var(--fg-muted)]">{result.message}</p>
              )}
            </div>
          </div>
        )}

        <div className="flex items-center justify-end gap-2 pt-1">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setOpen(false)}
            disabled={isApplying}
          >
            Cancel
          </Button>
          <Button
            variant="default"
            size="sm"
            onClick={handleConfirm}
            disabled={isApplying || applied || isPreviewLoading}
          >
            {applied ? 'Logged' : isApplying ? 'Logging…' : 'Confirm'}
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  );
}
