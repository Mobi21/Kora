import {
  ChevronDown,
  ChevronUp,
  ExternalLink,
  Pause,
  Play,
  X,
  type LucideIcon,
} from 'lucide-react';
import { Link } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import type { AutonomousPlanView } from '@/lib/api/types';
import { cn } from '@/lib/utils';

interface PlanActionsBarProps {
  plan: AutonomousPlanView;
  historyExpanded: boolean;
  onToggleHistory: () => void;
}

/**
 * Footer action row. Pause / Resume / Cancel are *intentionally* disabled
 * with tooltips because the daemon doesn't expose those endpoints yet —
 * faking them would mislead the user into thinking they had control they
 * don't have.
 *
 * "Open" is a real link. The destination route isn't built in this phase,
 * but the link is honest about where it'll end up.
 */
export function PlanActionsBar({
  plan,
  historyExpanded,
  onToggleHistory,
}: PlanActionsBarProps): JSX.Element {
  const canResume = plan.status === 'paused';
  const pauseIcon: LucideIcon = canResume ? Play : Pause;
  const pauseLabel = canResume ? 'Resume plan' : 'Pause plan';

  return (
    <div className="flex flex-wrap items-center justify-between gap-2 pt-2">
      <div className="flex items-center gap-1">
        <DisabledIconButton
          icon={pauseIcon}
          label={pauseLabel}
          tooltip="Not yet exposed by daemon"
        />
        <DisabledIconButton
          icon={X}
          label="Cancel plan"
          tooltip="Not yet exposed by daemon"
        />
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              asChild
              variant="ghost"
              size="icon"
              aria-label={`Open plan ${plan.title}`}
            >
              <Link to={`/autonomous/${plan.id}`}>
                <ExternalLink className="h-4 w-4" strokeWidth={1.6} />
              </Link>
            </Button>
          </TooltipTrigger>
          <TooltipContent>Open plan detail</TooltipContent>
        </Tooltip>
      </div>

      <Button
        type="button"
        variant="ghost"
        size="sm"
        onClick={onToggleHistory}
        aria-expanded={historyExpanded}
        aria-controls={`plan-history-${plan.id}`}
        className="gap-1.5 text-[var(--fg-muted)] hover:text-[var(--fg)]"
      >
        {historyExpanded ? (
          <ChevronUp className="h-4 w-4" strokeWidth={1.6} />
        ) : (
          <ChevronDown className="h-4 w-4" strokeWidth={1.6} />
        )}
        <span className="text-[var(--fs-sm)]">
          {historyExpanded ? 'Hide plan history' : 'Show plan history'}
        </span>
      </Button>
    </div>
  );
}

interface DisabledIconButtonProps {
  icon: LucideIcon;
  label: string;
  tooltip: string;
}

function DisabledIconButton({
  icon: Icon,
  label,
  tooltip,
}: DisabledIconButtonProps): JSX.Element {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        {/* The wrapping span gives the tooltip a hover target while the
            button itself remains disabled and unreachable via the tab key. */}
        <span tabIndex={0} className={cn('inline-flex')}>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            disabled
            aria-disabled
            aria-label={`${label} (disabled — not yet exposed by daemon)`}
          >
            <Icon className="h-4 w-4" strokeWidth={1.6} />
          </Button>
        </span>
      </TooltipTrigger>
      <TooltipContent>{tooltip}</TooltipContent>
    </Tooltip>
  );
}
