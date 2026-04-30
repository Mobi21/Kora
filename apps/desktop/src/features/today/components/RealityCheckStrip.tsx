import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { WhatChangedDialog } from './WhatChangedDialog';

interface RealityCheckStripProps {
  date: string;
  repairAvailable: boolean;
  className?: string;
}

export function RealityCheckStrip({
  date,
  repairAvailable,
  className,
}: RealityCheckStripProps): JSX.Element {
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();

  if (!repairAvailable) {
    return (
      <div
        className={cn(
          'flex w-full items-center gap-3 px-1 py-2',
          'text-[var(--fs-sm)] text-[var(--fg-muted)]',
          className,
        )}
      >
        <span
          aria-hidden
          className="inline-block h-1.5 w-1.5 rounded-full bg-[var(--ok)]"
        />
        <span className="font-narrative italic">
          Today's plan looks intact. Nothing flagged for repair.
        </span>
      </div>
    );
  }

  return (
    <>
      <div
        className={cn(
          'relative flex w-full flex-col gap-3 sm:flex-row sm:items-center sm:justify-between',
          'overflow-hidden rounded-[var(--r-2)] border border-[var(--border)]',
          'bg-[var(--surface-2)] px-[var(--pad)] py-3',
          className,
        )}
      >
        <span
          aria-hidden
          className="absolute left-0 top-0 h-full w-1"
          style={{ background: 'var(--ok)' }}
        />
        <div className="flex min-w-0 flex-col gap-1 pl-2">
          <p className="font-narrative text-[var(--fs-md)] leading-tight text-[var(--fg)]">
            Something off today?
          </p>
          <p className="text-[var(--fs-sm)] text-[var(--fg-muted)]">
            A reality check takes a minute and reshapes only what changed.
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2 pl-2 sm:pl-0">
          <Button size="sm" onClick={() => setOpen(true)}>
            What changed?
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => navigate('/repair')}
            aria-label="Open the Repair screen"
          >
            Open Repair
          </Button>
        </div>
      </div>
      <WhatChangedDialog open={open} onOpenChange={setOpen} date={date} />
    </>
  );
}
