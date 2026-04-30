import { Search, X } from 'lucide-react';
import { Input } from '@/components/ui/input';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { cn } from '@/lib/utils';

export type CertaintyFilter = 'all' | 'confirmed' | 'guess' | 'correction' | 'stale';

const FILTERS: { id: CertaintyFilter; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'confirmed', label: 'Confirmed' },
  { id: 'guess', label: 'Guess' },
  { id: 'correction', label: 'Correction' },
  { id: 'stale', label: 'Stale' },
];

interface MemorySearchBarProps {
  value: string;
  onChange: (value: string) => void;
  filter: CertaintyFilter;
  onFilterChange: (filter: CertaintyFilter) => void;
}

export function MemorySearchBar({
  value,
  onChange,
  filter,
  onFilterChange,
}: MemorySearchBarProps): JSX.Element {
  return (
    <div
      className={cn(
        'sticky top-0 z-10 -mx-2 flex flex-wrap items-center gap-3 px-2 py-3',
        'bg-[color-mix(in_oklch,var(--bg)_92%,transparent)] backdrop-blur',
      )}
    >
      <label className="relative flex min-w-[260px] flex-1 items-center">
        <Search
          aria-hidden
          className="pointer-events-none absolute left-3 h-4 w-4 text-[var(--fg-subtle)]"
          strokeWidth={1.5}
        />
        <Input
          type="search"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="Search what Kora knows…"
          aria-label="Search memory"
          className={cn(
            'h-10 pl-9 pr-9 text-[var(--fs-base)]',
            'placeholder:italic placeholder:[font-family:var(--font-narrative)]',
            'placeholder:text-[var(--fg-subtle)]',
          )}
        />
        {value.length > 0 && (
          <button
            type="button"
            onClick={() => onChange('')}
            aria-label="Clear search"
            className={cn(
              'absolute right-2 inline-flex h-6 w-6 items-center justify-center rounded-[var(--r-1)]',
              'text-[var(--fg-muted)] hover:bg-[var(--surface-2)] hover:text-[var(--fg)]',
              'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]',
            )}
          >
            <X className="h-3.5 w-3.5" strokeWidth={1.5} />
          </button>
        )}
      </label>
      <Tabs
        value={filter}
        onValueChange={(v) => onFilterChange(v as CertaintyFilter)}
        aria-label="Filter by certainty"
      >
        <TabsList className="h-9 border-b-0">
          {FILTERS.map((f) => (
            <TabsTrigger key={f.id} value={f.id} className="px-3">
              {f.label}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>
    </div>
  );
}

export const CERTAINTY_FILTERS = FILTERS;
