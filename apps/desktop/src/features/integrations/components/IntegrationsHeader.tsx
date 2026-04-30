import { useEffect, useState } from 'react';
import { RefreshCcw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';

interface IntegrationsHeaderProps {
  generatedAt: string | null;
  isFetching: boolean;
  onRefresh: () => void;
}

export function IntegrationsHeader({
  generatedAt,
  isFetching,
  onRefresh,
}: IntegrationsHeaderProps): JSX.Element {
  const [relative, setRelative] = useState(() => formatChecked(generatedAt));

  useEffect(() => {
    setRelative(formatChecked(generatedAt));
    const id = window.setInterval(() => {
      setRelative(formatChecked(generatedAt));
    }, 5_000);
    return () => window.clearInterval(id);
  }, [generatedAt]);

  return (
    <header className="flex w-full flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <div className="flex min-w-0 flex-col gap-1.5">
        <h1
          className={cn(
            'font-narrative text-[var(--fs-3xl)] tracking-[var(--track-tight)]',
            'text-[var(--fg)]',
          )}
        >
          Integrations
        </h1>
        <p
          className={cn(
            'font-narrative italic text-[var(--fs-md)] text-[var(--fg-muted)]',
          )}
        >
          What Kora can reach right now.
        </p>
      </div>

      <div className="flex shrink-0 items-center gap-3">
        <span
          className="font-mono text-[var(--fs-2xs)] text-[var(--fg-subtle)] num-tabular"
          aria-live="polite"
        >
          {relative}
        </span>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              onClick={onRefresh}
              aria-label="Recheck integration health"
              disabled={isFetching}
            >
              <RefreshCcw
                className={cn('h-4 w-4', isFetching && 'animate-pulse opacity-60')}
                strokeWidth={1.5}
              />
            </Button>
          </TooltipTrigger>
          <TooltipContent>Recheck health</TooltipContent>
        </Tooltip>
      </div>
    </header>
  );
}

function formatChecked(generatedAt: string | null): string {
  if (!generatedAt) return 'never checked';
  const date = new Date(generatedAt);
  if (Number.isNaN(date.getTime())) return 'never checked';
  const elapsed = Date.now() - date.getTime();
  const seconds = Math.max(0, Math.round(elapsed / 1000));
  if (seconds < 60) return `checked ${seconds}s ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `checked ${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `checked ${hours}h ago`;
  const days = Math.round(hours / 24);
  return `checked ${days}d ago`;
}
