import { ChevronDown } from 'lucide-react';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { cn } from '@/lib/utils';
import { PRESETS, type PresetId } from '../utils/layers';

export interface PresetMenuProps {
  preset: PresetId;
  onChange: (preset: PresetId) => void;
}

export function PresetMenu({ preset, onChange }: PresetMenuProps): JSX.Element {
  const active = PRESETS.find((p) => p.id === preset) ?? PRESETS[1];
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        className={cn(
          'inline-flex w-full items-center justify-between gap-2 rounded-[var(--r-2)]',
          'border border-[var(--border)] bg-[var(--surface-1)] px-3 py-2',
          'text-[var(--fs-sm)] text-[var(--fg)]',
          'hover:bg-[var(--surface-2)] hover:border-[var(--border-strong)]',
          'transition-colors duration-[var(--motion-fast)] ease-[var(--ease-out)]',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]',
          'focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--bg)]',
        )}
        aria-label="Layer presets"
      >
        <span className="flex flex-col items-start">
          <span className="text-[var(--fs-2xs)] uppercase tracking-[0.02em] text-[var(--fg-muted)]">
            Layer preset
          </span>
          <span className="text-[var(--fg)]">{active.label}</span>
        </span>
        <ChevronDown className="h-4 w-4 text-[var(--fg-muted)]" strokeWidth={1.5} />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="min-w-[14rem]">
        <DropdownMenuLabel>Presets</DropdownMenuLabel>
        <DropdownMenuSeparator />
        {PRESETS.map((p) => (
          <DropdownMenuItem
            key={p.id}
            onSelect={() => onChange(p.id)}
            aria-current={p.id === preset}
            className={cn(
              'flex flex-col items-start gap-0.5',
              p.id === preset && 'bg-[var(--surface-2)]',
            )}
          >
            <span className="text-[var(--fs-sm)] text-[var(--fg)]">{p.label}</span>
            <span className="text-[var(--fs-2xs)] text-[var(--fg-muted)]">
              {p.description}
            </span>
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
