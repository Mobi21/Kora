import { useState } from 'react';
import { Pause, Play, RotateCcw, Square } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import type { RoutineRunView } from '@/lib/api/types';

type Action = 'pause' | 'resume' | 'cancel' | 'reset';

interface RoutineActionsBarProps {
  run: RoutineRunView;
  busy: boolean;
  onAction: (action: Action) => void;
}

export function RoutineActionsBar({
  run,
  busy,
  onAction,
}: RoutineActionsBarProps): JSX.Element {
  const [confirmCancelOpen, setConfirmCancelOpen] = useState<boolean>(false);

  const isActive = run.status === 'active';
  const isPaused = run.status === 'paused';
  const canCancel = run.status === 'active' || run.status === 'paused' || run.status === 'pending';

  return (
    <div className="flex items-center gap-1">
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            variant="ghost"
            size="icon"
            className="h-8 w-8"
            disabled={busy || !isActive}
            aria-label={`Pause ${run.name}`}
            onClick={() => onAction('pause')}
          >
            <Pause className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
          </Button>
        </TooltipTrigger>
        <TooltipContent>Pause</TooltipContent>
      </Tooltip>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            variant="ghost"
            size="icon"
            className="h-8 w-8"
            disabled={busy || !isPaused}
            aria-label={`Resume ${run.name}`}
            onClick={() => onAction('resume')}
          >
            <Play className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
          </Button>
        </TooltipTrigger>
        <TooltipContent>Resume</TooltipContent>
      </Tooltip>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            variant="ghost"
            size="icon"
            className="h-8 w-8"
            disabled={busy}
            aria-label={`Reset ${run.name}`}
            onClick={() => onAction('reset')}
          >
            <RotateCcw className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
          </Button>
        </TooltipTrigger>
        <TooltipContent>Reset</TooltipContent>
      </Tooltip>
      <Popover open={confirmCancelOpen} onOpenChange={setConfirmCancelOpen}>
        <Tooltip>
          <TooltipTrigger asChild>
            <PopoverTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="h-8 w-8 text-[var(--fg-muted)] hover:text-[var(--danger)]"
                disabled={busy || !canCancel}
                aria-label={`Cancel ${run.name}`}
              >
                <Square className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
              </Button>
            </PopoverTrigger>
          </TooltipTrigger>
          <TooltipContent>Cancel run</TooltipContent>
        </Tooltip>
        <PopoverContent align="end" className="w-64 space-y-3">
          <div className="space-y-1">
            <p className="font-narrative text-[var(--fs-md)] text-[var(--fg)]">
              Cancel this run?
            </p>
            <p className="text-[var(--fs-xs)] text-[var(--fg-muted)]">
              You can start it again later. Progress will be cleared.
            </p>
          </div>
          <div className="flex items-center justify-end gap-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setConfirmCancelOpen(false)}
            >
              Keep
            </Button>
            <Button
              variant="danger"
              size="sm"
              onClick={() => {
                setConfirmCancelOpen(false);
                onAction('cancel');
              }}
            >
              Cancel run
            </Button>
          </div>
        </PopoverContent>
      </Popover>
    </div>
  );
}
