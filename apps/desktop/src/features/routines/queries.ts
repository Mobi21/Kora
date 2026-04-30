import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useMemo } from 'react';
import { createApiClient, type ApiClient } from '@/lib/api/client';
import { useConnection } from '@/lib/api/connection';
import { queryKeys, useRoutines as useRoutinesDay } from '@/lib/api/queries';
import type {
  RoutineActionRequest,
  RoutineActionResult,
  RoutineDayView,
  RoutineRunView,
  RoutineStepView,
} from '@/lib/api/types';

function useApi(): ApiClient | null {
  const conn = useConnection();
  return useMemo(() => (conn ? createApiClient(conn) : null), [conn]);
}

interface ApplyContext {
  prev: RoutineDayView | undefined;
  cacheKey: ReturnType<typeof queryKeys.routines>;
}

function applyOptimistic(
  view: RoutineDayView,
  req: RoutineActionRequest,
): RoutineDayView {
  const findRunIdx = (runs: RoutineRunView[]): number => {
    if (req.run_id) return runs.findIndex((r) => r.id === req.run_id);
    if (req.routine_id) return runs.findIndex((r) => r.routine_id === req.routine_id);
    return -1;
  };

  const cloneRun = (run: RoutineRunView): RoutineRunView => ({
    ...run,
    steps: run.steps.map((s) => ({ ...s })),
  });

  const updateRun = (run: RoutineRunView): RoutineRunView => {
    const next = cloneRun(run);
    switch (req.action) {
      case 'complete_step': {
        const idx = req.step_index ?? next.next_step_index ?? -1;
        if (idx >= 0 && idx < next.steps.length) {
          next.steps[idx] = { ...next.steps[idx], completed: true } as RoutineStepView;
        }
        const nextIdx = next.steps.findIndex((s) => !s.completed);
        next.next_step_index = nextIdx >= 0 ? nextIdx : null;
        next.status =
          nextIdx === -1 ? 'completed' : next.status === 'pending' ? 'active' : next.status;
        return next;
      }
      case 'skip_step': {
        const idx = req.step_index ?? next.next_step_index ?? -1;
        if (idx >= 0 && idx < next.steps.length) {
          next.steps[idx] = { ...next.steps[idx], completed: true } as RoutineStepView;
        }
        const nextIdx = next.steps.findIndex((s) => !s.completed);
        next.next_step_index = nextIdx >= 0 ? nextIdx : null;
        next.status = nextIdx === -1 ? 'completed' : next.status;
        return next;
      }
      case 'pause':
        next.status = 'paused';
        return next;
      case 'resume':
        next.status = 'active';
        return next;
      case 'cancel':
        next.status = 'skipped';
        return next;
      case 'start':
        next.status = 'active';
        return next;
      default:
        return next;
    }
  };

  const runIdx = findRunIdx(view.runs);
  if (runIdx >= 0) {
    const newRuns = [...view.runs];
    newRuns[runIdx] = updateRun(newRuns[runIdx]);
    return { ...view, runs: newRuns };
  }

  const upcomingIdx = findRunIdx(view.upcoming);
  if (upcomingIdx >= 0 && req.action === 'start') {
    const promoted = updateRun(view.upcoming[upcomingIdx]);
    const newUpcoming = view.upcoming.filter((_, i) => i !== upcomingIdx);
    return { ...view, runs: [...view.runs, promoted], upcoming: newUpcoming };
  }
  return view;
}

export function useRoutinesApply(date: string) {
  const api = useApi();
  const qc = useQueryClient();
  const cacheKey = queryKeys.routines(date);

  return useMutation<RoutineActionResult, Error, RoutineActionRequest, ApplyContext>({
    mutationFn: async (req) => {
      if (!api) throw new Error('Not connected');
      return api.routinesApply(req);
    },
    onMutate: async (req) => {
      await qc.cancelQueries({ queryKey: cacheKey });
      const prev = qc.getQueryData<RoutineDayView>(cacheKey);
      if (prev) {
        qc.setQueryData<RoutineDayView>(cacheKey, applyOptimistic(prev, req));
      }
      return { prev, cacheKey };
    },
    onError: (_err, _req, ctx) => {
      if (ctx?.prev) {
        qc.setQueryData(ctx.cacheKey, ctx.prev);
      }
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: cacheKey });
    },
  });
}

export { useRoutinesDay, queryKeys };
