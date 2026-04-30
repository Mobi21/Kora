import { HelpCircle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Kbd } from '@/components/ui/kbd';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';

const SHORTCUTS: Array<{ keys: string[]; label: string }> = [
  { keys: ['D'], label: 'Day view' },
  { keys: ['W'], label: 'Week view' },
  { keys: ['M'], label: 'Month view' },
  { keys: ['A'], label: 'Agenda view' },
  { keys: ['T'], label: 'Today' },
  { keys: ['←'], label: 'Previous period' },
  { keys: ['→'], label: 'Next period' },
  { keys: ['?'], label: 'Show shortcuts' },
];

export interface ShortcutsPopoverProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function ShortcutsPopover({
  open,
  onOpenChange,
}: ShortcutsPopoverProps): JSX.Element {
  return (
    <Popover open={open} onOpenChange={onOpenChange}>
      <PopoverTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          aria-label="Show keyboard shortcuts"
          className="h-7 w-7"
        >
          <HelpCircle className="h-3.5 w-3.5" strokeWidth={1.5} />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-64">
        <div className="flex flex-col gap-2">
          <span className="text-[var(--fs-2xs)] uppercase tracking-[0.02em] text-[var(--fg-muted)]">
            Calendar shortcuts
          </span>
          <ul className="flex flex-col gap-1">
            {SHORTCUTS.map((s) => (
              <li
                key={s.label}
                className="flex items-center justify-between gap-3"
              >
                <span className="text-[var(--fs-sm)] text-[var(--fg)]">
                  {s.label}
                </span>
                <span className="flex items-center gap-1">
                  {s.keys.map((k) => (
                    <Kbd key={k}>{k}</Kbd>
                  ))}
                </span>
              </li>
            ))}
          </ul>
        </div>
      </PopoverContent>
    </Popover>
  );
}
