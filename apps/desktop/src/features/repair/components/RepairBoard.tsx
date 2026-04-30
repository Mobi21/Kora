import { useCallback, useMemo, useState } from 'react';
import { ApiError } from '@/lib/api/client';
import type {
  RepairActionPreview,
  RepairApplyResult,
  RepairPreview,
  RepairStateView,
  TimelineItem,
} from '@/lib/api/types';
import type { ProvenanceKind } from '@/components/ui/provenance-dot';
import { useRepairApply, useRepairPreview } from '../queries';
import { BoardCard } from './BoardCard';
import { BoardColumn } from './BoardColumn';
import { ConfirmDialog } from './ConfirmDialog';
import { StickyApplyBar } from './StickyApplyBar';
import { PreviewStep } from './PreviewStep';

interface RepairBoardProps {
  state: RepairStateView;
  date: string;
  onApplied: (count: number) => void;
}

function describeError(err: unknown): string {
  if (err instanceof ApiError) {
    return `Daemon returned ${err.status}. ${err.body || 'No details available.'}`;
  }
  if (err instanceof Error) return err.message;
  return 'Something went wrong.';
}

function provenanceFor(item: TimelineItem): ProvenanceKind {
  if (item.risk === 'repair') return 'repair';
  if (item.provenance.includes('confirmed')) return 'confirmed';
  if (item.provenance.includes('inferred')) return 'inferred';
  if (item.provenance.includes('workspace')) return 'workspace';
  return 'local';
}

export function RepairBoard({
  state,
  date,
  onApplied,
}: RepairBoardProps): JSX.Element {
  const [selectedTimelineIds, setSelectedTimelineIds] = useState<Set<string>>(new Set());
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [boardPreview, setBoardPreview] = useState<RepairPreview | null>(null);
  const [boardSelectedIds, setBoardSelectedIds] = useState<Set<string>>(new Set());
  const [boardError, setBoardError] = useState<string | null>(null);
  const [applyResult, setApplyResult] = useState<RepairApplyResult | null>(null);
  const [singlePreviewMode, setSinglePreviewMode] = useState(false);

  const previewMutation = useRepairPreview();
  const applyMutation = useRepairApply();

  const toggleSelected = useCallback((id: string, next: boolean) => {
    setSelectedTimelineIds((prev) => {
      const updated = new Set(prev);
      if (next) updated.add(id);
      else updated.delete(id);
      return updated;
    });
  }, []);

  const startMoveTomorrow = useCallback(() => {
    setBoardError(null);
    setApplyResult(null);
    setSinglePreviewMode(false);
    previewMutation.mutate(
      {
        date,
        change_type: 'move_to_tomorrow',
        note: null,
        selected_entry_ids: Array.from(selectedTimelineIds),
      },
      {
        onSuccess: (data) => {
          setBoardPreview(data);
          setBoardSelectedIds(new Set(data.actions.map((a) => a.id)));
          setConfirmOpen(true);
        },
        onError: (err) => {
          setBoardError(describeError(err));
          setConfirmOpen(true);
        },
      },
    );
  }, [date, previewMutation, selectedTimelineIds]);

  const startSinglePreview = useCallback(
    (action: RepairActionPreview) => {
      setBoardError(null);
      setApplyResult(null);
      setSinglePreviewMode(true);
      const synthetic: RepairPreview = {
        date,
        day_plan_id: state.day_plan_id,
        summary: 'Preview of one suggested repair from the board.',
        actions: [action],
        mutates_state: false,
        generated_at: state.generated_at,
      };
      setBoardPreview(synthetic);
      setBoardSelectedIds(new Set([action.id]));
    },
    [date, state.day_plan_id, state.generated_at],
  );

  const handleConfirm = useCallback(() => {
    if (!boardPreview) return;
    setBoardError(null);
    applyMutation.mutate(
      {
        date,
        preview_action_ids: Array.from(boardSelectedIds),
        user_confirmed: true,
      },
      {
        onSuccess: (data) => {
          setApplyResult(data);
          if (data.status === 'applied') {
            const count = data.applied_action_ids.length || boardSelectedIds.size;
            window.setTimeout(() => {
              setConfirmOpen(false);
              setBoardPreview(null);
              setBoardSelectedIds(new Set());
              setSelectedTimelineIds(new Set());
              onApplied(count);
            }, 600);
          }
        },
        onError: (err) => {
          setBoardError(describeError(err));
        },
      },
    );
  }, [applyMutation, boardPreview, boardSelectedIds, date, onApplied]);

  const handleDialogOpenChange = useCallback(
    (next: boolean) => {
      setConfirmOpen(next);
      if (!next) {
        setBoardError(null);
        if (applyResult?.status !== 'applied') {
          setApplyResult(null);
        }
      }
    },
    [applyResult?.status],
  );

  const includedActions = useMemo(() => {
    if (!boardPreview) return [];
    return boardPreview.actions.filter((a) => boardSelectedIds.has(a.id));
  }, [boardPreview, boardSelectedIds]);

  return (
    <div className="flex flex-col">
      <div role="region" aria-label="Repair board columns">
        <div className="grid grid-cols-[repeat(auto-fit,minmax(220px,1fr))] gap-5 pb-4">
          <BoardColumn
            title="Broken / At Risk"
            count={state.broken_or_at_risk.length}
            emptyLabel="Nothing broken right now."
          >
            {state.broken_or_at_risk.map((item) => (
              <BoardCard
                key={item.id}
                title={item.title}
                time={item.starts_at}
                endTime={item.ends_at}
                tags={[item.reality_state, ...item.support_tags]}
                provenance={provenanceFor(item)}
              />
            ))}
          </BoardColumn>

          <BoardColumn
            title="Suggested Repairs"
            count={state.suggested_repairs.length}
            emptyLabel="No repairs suggested."
          >
            {state.suggested_repairs.map((action) => (
              <BoardCard
                key={action.id}
                title={action.title}
                provenance="repair"
                severity={action.severity}
                tags={[action.action_type]}
                onPreview={() => startSinglePreview(action)}
              />
            ))}
          </BoardColumn>

          <BoardColumn
            title="Protected"
            count={state.protected_commitments.length}
            emptyLabel="No protected items today."
          >
            {state.protected_commitments.map((item) => (
              <BoardCard
                key={item.id}
                title={item.title}
                time={item.starts_at}
                endTime={item.ends_at}
                provenance="confirmed"
                tags={item.support_tags}
              />
            ))}
          </BoardColumn>

          <BoardColumn
            title="Flexible"
            count={state.flexible_items.length}
            emptyLabel="No flexible items."
          >
            {state.flexible_items.map((item) => (
              <BoardCard
                key={item.id}
                title={item.title}
                time={item.starts_at}
                endTime={item.ends_at}
                provenance={provenanceFor(item)}
                tags={item.support_tags}
              />
            ))}
          </BoardColumn>

          <BoardColumn
            title="Move to Tomorrow"
            count={state.move_to_tomorrow.length}
            emptyLabel="Nothing queued for tomorrow."
            description="Tap to select items, then move them in one go."
          >
            {state.move_to_tomorrow.map((item) => (
              <BoardCard
                key={item.id}
                title={item.title}
                time={item.starts_at}
                endTime={item.ends_at}
                provenance={provenanceFor(item)}
                tags={item.support_tags}
                selectable
                selected={selectedTimelineIds.has(item.id)}
                onSelect={(next) => toggleSelected(item.id, next)}
                selectLabel={`Select ${item.title} to move to tomorrow`}
              />
            ))}
          </BoardColumn>
        </div>
      </div>

      {singlePreviewMode && boardPreview ? (
        <div className="mt-6">
          <PreviewStep
            preview={boardPreview}
            selectedIds={boardSelectedIds}
            onSelectedChange={setBoardSelectedIds}
            onCancel={() => {
              setBoardPreview(null);
              setBoardSelectedIds(new Set());
              setSinglePreviewMode(false);
            }}
            onApply={() => {
              setApplyResult(null);
              setBoardError(null);
              setConfirmOpen(true);
            }}
          />
        </div>
      ) : (
        <StickyApplyBar
          count={selectedTimelineIds.size}
          isPending={previewMutation.isPending || applyMutation.isPending}
          onApply={startMoveTomorrow}
          onClear={() => setSelectedTimelineIds(new Set())}
        />
      )}

      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={handleDialogOpenChange}
        actions={includedActions}
        isPending={applyMutation.isPending}
        result={applyResult}
        errorMessage={boardError}
        onConfirm={handleConfirm}
      />
    </div>
  );
}
