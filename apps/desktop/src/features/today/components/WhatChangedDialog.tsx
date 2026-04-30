import { useNavigate } from 'react-router-dom';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { cn } from '@/lib/utils';

interface WhatChangedDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  date: string;
  /** Optional override; defaults to a calm canonical 6 reasons. */
  options?: readonly string[];
}

const DEFAULT_OPTIONS: readonly string[] = [
  "I'm behind",
  'Too tired',
  'Event changed',
  'Skipped something',
  'Need to move things',
  'Need a smaller version',
];

export function WhatChangedDialog({
  open,
  onOpenChange,
  date,
  options = DEFAULT_OPTIONS,
}: WhatChangedDialogProps): JSX.Element {
  const navigate = useNavigate();

  const handlePick = (reason: string): void => {
    onOpenChange(false);
    const params = new URLSearchParams({ date, reason });
    navigate(`/repair?${params.toString()}`);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>What changed?</DialogTitle>
          <DialogDescription>
            Pick what's most true. Kora will reshape today around it.
          </DialogDescription>
        </DialogHeader>
        <div className="grid grid-cols-1 gap-2 pt-3 sm:grid-cols-2">
          {options.map((label) => (
            <button
              key={label}
              type="button"
              onClick={() => handlePick(label)}
              className={cn(
                'group flex items-center justify-between gap-3',
                'rounded-[var(--r-2)] border border-[var(--border)] bg-[var(--surface-2)]',
                'px-3 py-2.5 text-left text-[var(--fs-base)] text-[var(--fg)]',
                'transition-colors duration-[var(--motion-fast)] ease-[var(--ease-out)]',
                'hover:border-[var(--border-strong)] hover:bg-[var(--surface-3)]',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]',
              )}
            >
              <span>{label}</span>
              <span
                aria-hidden
                className={cn(
                  'font-mono text-[var(--fs-xs)] text-[var(--fg-subtle)]',
                  'transition-transform duration-[var(--motion-fast)] group-hover:translate-x-0.5',
                )}
              >
                →
              </span>
            </button>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  );
}
