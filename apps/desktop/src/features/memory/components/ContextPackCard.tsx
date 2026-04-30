import type { ContextPackSummary } from '@/lib/api/types';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { ExternalLink } from 'lucide-react';
import { cn } from '@/lib/utils';
import { formatRelativeOr } from '../utils/format';

interface ContextPackCardProps {
  pack: ContextPackSummary;
  onOpen: (pack: ContextPackSummary) => void;
}

export function ContextPackCard({ pack, onOpen }: ContextPackCardProps): JSX.Element {
  const canOpen = !!pack.artifact_path;
  const button = (
    <Button
      size="sm"
      variant={canOpen ? 'subtle' : 'ghost'}
      onClick={() => canOpen && onOpen(pack)}
      disabled={!canOpen}
      aria-label={`Open ${pack.title}`}
      className="gap-1.5"
    >
      <ExternalLink className="h-3.5 w-3.5" strokeWidth={1.5} aria-hidden />
      Open
    </Button>
  );

  return (
    <Card
      className={cn(
        'flex h-full flex-col gap-3 p-[var(--pad)]',
        'transition-colors duration-[var(--motion-fast)] ease-[var(--ease-out)]',
        'hover:border-[var(--border-strong)]',
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <h3
          className={cn(
            'font-narrative text-[var(--fs-md)] tracking-[var(--track-tight)] text-[var(--fg)]',
            'line-clamp-2',
          )}
          title={pack.title}
        >
          {pack.title}
        </h3>
      </div>
      <div className="flex items-center gap-2">
        <span
          className={cn(
            'inline-flex items-center rounded-[var(--r-pill)] border border-[var(--border)]',
            'px-2 py-0.5 text-[var(--fs-2xs)] uppercase tracking-[var(--track-label)]',
            'text-[var(--fg-muted)]',
          )}
        >
          {pack.pack_type}
        </span>
      </div>
      <div className="mt-auto flex items-center justify-between gap-2 pt-1">
        <span className="font-mono num-tabular text-[var(--fs-2xs)] text-[var(--fg-subtle)]">
          {formatRelativeOr(pack.created_at, '—')}
        </span>
        {canOpen ? (
          button
        ) : (
          <Tooltip>
            <TooltipTrigger asChild>
              <span tabIndex={0} aria-label="No artifact path">
                {button}
              </span>
            </TooltipTrigger>
            <TooltipContent>No artifact path</TooltipContent>
          </Tooltip>
        )}
      </div>
    </Card>
  );
}
