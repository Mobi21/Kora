import { useEffect, useState } from 'react';
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
import { cn } from '@/lib/utils';
import {
  useCalendarApply,
  useCalendarPreview,
} from '@/lib/api/queries';
import type {
  CalendarEditPreview,
  CalendarEditRequest,
  CalendarEventView,
} from '@/lib/api/types';
import { EventChip } from './EventChip';

export interface EditPreviewDialogProps {
  open: boolean;
  request: CalendarEditRequest | null;
  onClose: () => void;
  onApplied?: () => void;
}

export function EditPreviewDialog({
  open,
  request,
  onClose,
  onApplied,
}: EditPreviewDialogProps): JSX.Element {
  const preview = useCalendarPreview();
  const apply = useCalendarApply();
  const [data, setData] = useState<CalendarEditPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [applyMessage, setApplyMessage] = useState<string | null>(null);
  const [applyStatus, setApplyStatus] = useState<
    'idle' | 'applied' | 'unavailable' | 'skipped'
  >('idle');

  useEffect(() => {
    if (!open || !request) {
      setData(null);
      setError(null);
      setApplyMessage(null);
      setApplyStatus('idle');
      return;
    }
    let cancelled = false;
    setData(null);
    setError(null);
    setApplyMessage(null);
    setApplyStatus('idle');
    preview
      .mutateAsync(request)
      .then((p) => {
        if (!cancelled) setData(p);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Preview failed.');
        }
      });
    return () => {
      cancelled = true;
    };
    // We deliberately exclude `preview` from deps; mutations are stable
    // enough across renders and including would re-fire on every re-render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, JSON.stringify(request)]);

  const opLabel =
    request?.operation === 'move'
      ? 'Move event'
      : request?.operation === 'resize'
        ? 'Resize event'
        : request?.operation === 'cancel'
          ? 'Cancel event'
          : request?.operation === 'create'
            ? 'Create event'
            : 'Preview change';

  const handleApply = async () => {
    if (!request) return;
    setApplyMessage(null);
    setApplyStatus('idle');
    try {
      const res = await apply.mutateAsync(request);
      setApplyStatus(res.status);
      setApplyMessage(res.message);
      if (res.status === 'applied') {
        onApplied?.();
        // Close after a brief moment so the user sees the confirmation.
        setTimeout(() => onClose(), 600);
      }
    } catch (err) {
      setApplyStatus('unavailable');
      setApplyMessage(err instanceof Error ? err.message : 'Apply failed.');
    }
  };

  const backendReady =
    !!data && (applyStatus === 'idle' || applyStatus === 'applied');

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{opLabel}</DialogTitle>
          <DialogDescription>
            {data?.summary ??
              'Preview the change before it touches your calendar.'}
          </DialogDescription>
        </DialogHeader>

        {error ? (
          <div
            className={cn(
              'rounded-[var(--r-2)] border border-[var(--border)] bg-[var(--surface-2)]',
              'px-3 py-2 text-[var(--fs-sm)] text-[var(--fg-muted)]',
            )}
          >
            {error}
          </div>
        ) : !data ? (
          <PreviewSkeleton />
        ) : (
          <PreviewBody preview={data} />
        )}

        {applyMessage && (
          <div
            className={cn(
              'mt-3 rounded-[var(--r-2)] px-3 py-2 text-[var(--fs-sm)]',
              applyStatus === 'applied'
                ? 'border border-[var(--ok)] text-[var(--fg)]'
                : 'border border-[var(--border)] bg-[var(--surface-2)] text-[var(--fg-muted)]',
            )}
            role="status"
          >
            {applyMessage}
          </div>
        )}

        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          {data && !backendReady ? null : data ? (
            applyStatus === 'unavailable' || applyStatus === 'skipped' ? (
              <Pill status="warn" label="Backend not ready">
                Backend not ready
              </Pill>
            ) : (
              <Button
                onClick={handleApply}
                disabled={apply.isPending || applyStatus === 'applied'}
              >
                {apply.isPending
                  ? 'Applying…'
                  : applyStatus === 'applied'
                    ? 'Applied'
                    : 'Apply'}
              </Button>
            )
          ) : (
            <Button disabled>Apply</Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function PreviewSkeleton(): JSX.Element {
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <Skeleton className="h-24" />
      <Skeleton className="h-24" />
    </div>
  );
}

function PreviewBody({ preview }: { preview: CalendarEditPreview }): JSX.Element {
  return (
    <div className="flex flex-col gap-4">
      <div className="grid gap-3 sm:grid-cols-2">
        <ChipColumn label="Before" event={preview.before} />
        <ChipColumn label="After" event={preview.after} />
      </div>
      {preview.conflicts.length > 0 && (
        <section className="flex flex-col gap-2">
          <span className="text-[var(--fs-2xs)] uppercase tracking-[0.02em] text-[var(--fg-muted)]">
            Conflicts ({preview.conflicts.length})
          </span>
          <ul className="flex flex-wrap gap-2">
            {preview.conflicts.map((c) => (
              <li key={c.id} className="flex items-center gap-2">
                <span
                  className={cn(
                    'rounded-[var(--r-pill)] border border-[var(--warn)]',
                    'bg-[var(--surface-1)] px-2 py-0.5',
                    'text-[var(--fs-2xs)] text-[var(--fg)]',
                  )}
                >
                  {c.title || 'Conflict'}
                </span>
                <Pill status="warn" label="risk" />
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

function ChipColumn({
  label,
  event,
}: {
  label: string;
  event: CalendarEventView | null;
}): JSX.Element {
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-[var(--fs-2xs)] uppercase tracking-[0.02em] text-[var(--fg-muted)]">
        {label}
      </span>
      {event ? (
        <div
          className={cn(
            'min-h-[5rem] rounded-[var(--r-2)] border border-[var(--border)]',
            'bg-[var(--surface-2)] p-2',
          )}
        >
          <EventChip event={event} />
        </div>
      ) : (
        <div
          className={cn(
            'flex min-h-[5rem] items-center justify-center rounded-[var(--r-2)]',
            'border border-dashed border-[var(--border)] bg-[var(--surface-2)]',
            'text-[var(--fs-xs)] text-[var(--fg-muted)]',
          )}
        >
          (none)
        </div>
      )}
    </div>
  );
}
