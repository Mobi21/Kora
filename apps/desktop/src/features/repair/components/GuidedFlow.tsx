import { useCallback, useMemo, useState } from 'react';
import { ApiError } from '@/lib/api/client';
import type {
  RepairActionPreview,
  RepairApplyResult,
  RepairPreview,
} from '@/lib/api/types';
import { useRepairApply, useRepairPreview } from '../queries';
import { ConfirmDialog } from './ConfirmDialog';
import { PreviewStep } from './PreviewStep';
import { WhatChangedStep } from './WhatChangedStep';

interface GuidedFlowProps {
  date: string;
  initialReason: string | null;
  onApplied: (appliedCount: number) => void;
}

type Step = 'select' | 'preview';

function describeError(err: unknown): string {
  if (err instanceof ApiError) {
    return `Daemon returned ${err.status}. ${err.body || 'No details available.'}`;
  }
  if (err instanceof Error) return err.message;
  return 'Something went wrong.';
}

export function GuidedFlow({
  date,
  initialReason,
  onApplied,
}: GuidedFlowProps): JSX.Element {
  const [step, setStep] = useState<Step>('select');
  const [preview, setPreview] = useState<RepairPreview | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [applyResult, setApplyResult] = useState<RepairApplyResult | null>(null);
  const [applyError, setApplyError] = useState<string | null>(null);

  const previewMutation = useRepairPreview();
  const applyMutation = useRepairApply();

  const handlePreview = useCallback(
    (changeType: string, note: string) => {
      setApplyResult(null);
      setApplyError(null);
      previewMutation.mutate(
        {
          date,
          change_type: changeType,
          note: note || null,
          selected_entry_ids: [],
        },
        {
          onSuccess: (data) => {
            setPreview(data);
            setSelectedIds(new Set(data.actions.map((a) => a.id)));
            setStep('preview');
          },
        },
      );
    },
    [date, previewMutation],
  );

  const handleCancel = useCallback(() => {
    setPreview(null);
    setSelectedIds(new Set());
    setStep('select');
    previewMutation.reset();
  }, [previewMutation]);

  const includedActions: RepairActionPreview[] = useMemo(() => {
    if (!preview) return [];
    return preview.actions.filter((a) => selectedIds.has(a.id));
  }, [preview, selectedIds]);

  const handleConfirm = useCallback(() => {
    if (!preview) return;
    setApplyError(null);
    applyMutation.mutate(
      {
        date,
        preview_action_ids: Array.from(selectedIds),
        user_confirmed: true,
      },
      {
        onSuccess: (data) => {
          setApplyResult(data);
          if (data.status === 'applied') {
            const count = data.applied_action_ids.length || includedActions.length;
            window.setTimeout(() => {
              setConfirmOpen(false);
              onApplied(count);
            }, 600);
          }
        },
        onError: (err) => {
          setApplyError(describeError(err));
        },
      },
    );
  }, [applyMutation, date, includedActions.length, onApplied, preview, selectedIds]);

  const handleDialogOpenChange = useCallback(
    (next: boolean) => {
      setConfirmOpen(next);
      if (!next) {
        setApplyError(null);
        if (applyResult?.status !== 'applied') {
          setApplyResult(null);
        }
      }
    },
    [applyResult?.status],
  );

  return (
    <div className="mx-auto w-full" style={{ maxWidth: '720px' }}>
      {step === 'select' ? (
        <WhatChangedStep
          initialReason={initialReason}
          isSubmitting={previewMutation.isPending}
          errorMessage={
            previewMutation.isError ? describeError(previewMutation.error) : null
          }
          onSubmit={handlePreview}
        />
      ) : preview ? (
        <PreviewStep
          preview={preview}
          selectedIds={selectedIds}
          onSelectedChange={setSelectedIds}
          onCancel={handleCancel}
          onApply={() => {
            setApplyResult(null);
            setApplyError(null);
            setConfirmOpen(true);
          }}
        />
      ) : null}

      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={handleDialogOpenChange}
        actions={includedActions}
        isPending={applyMutation.isPending}
        result={applyResult}
        errorMessage={applyError}
        onConfirm={handleConfirm}
      />
    </div>
  );
}
