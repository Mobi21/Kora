import { Search, X } from 'lucide-react';
import { useEffect, useMemo, useRef, type KeyboardEvent } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { cn } from '@/lib/utils';

export interface RailItem {
  id: string;
  label: string;
  /** Optional fuzzy keywords to make the search more forgiving. */
  keywords?: ReadonlyArray<string>;
  writable?: boolean;
}

interface SearchableRailProps {
  items: ReadonlyArray<RailItem>;
  activeId: string;
  query: string;
  onQueryChange: (value: string) => void;
  onSelect: (id: string) => void;
  onResetSection?: () => void;
  matchedFieldCounts?: Readonly<Record<string, number>>;
}

/**
 * Left rail for the settings screen. Vertical nav, anchored items,
 * client-side fuzzy search, ESC clears, "Reset to defaults" trailing slot.
 */
export function SearchableRail({
  items,
  activeId,
  query,
  onQueryChange,
  onSelect,
  onResetSection,
  matchedFieldCounts,
}: SearchableRailProps): JSX.Element {
  const inputRef = useRef<HTMLInputElement>(null);

  const visibleItems = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return items;
    return items.filter((item) => {
      const haystack = [item.label, ...(item.keywords ?? [])]
        .join(' ')
        .toLowerCase();
      return fuzzyMatch(haystack, q);
    });
  }, [items, query]);

  function onSearchKeyDown(event: KeyboardEvent<HTMLInputElement>): void {
    if (event.key === 'Escape') {
      event.preventDefault();
      if (query) {
        onQueryChange('');
      } else {
        inputRef.current?.blur();
      }
    }
  }

  // When the active section is hidden by filter, clear the filter so the
  // user doesn't lose orientation.
  useEffect(() => {
    if (!query) return;
    if (!visibleItems.some((i) => i.id === activeId) && visibleItems[0]) {
      onSelect(visibleItems[0].id);
    }
  }, [query, visibleItems, activeId, onSelect]);

  return (
    <aside
      aria-label="Settings sections"
      className="flex h-full w-[220px] shrink-0 flex-col gap-3 border-r border-[var(--border)] bg-[var(--bg)] py-6 pl-6 pr-4"
    >
      <div className="relative">
        <Search
          aria-hidden
          className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[var(--fg-subtle)]"
          strokeWidth={1.5}
        />
        <Input
          ref={inputRef}
          type="search"
          value={query}
          onChange={(e) => onQueryChange(e.target.value)}
          onKeyDown={onSearchKeyDown}
          placeholder="Search settings"
          aria-label="Search settings"
          className="h-8 pl-8 pr-7 text-[var(--fs-sm)]"
        />
        {query && (
          <button
            type="button"
            onClick={() => onQueryChange('')}
            aria-label="Clear search"
            className="absolute right-1 top-1/2 inline-flex h-6 w-6 -translate-y-1/2 items-center justify-center rounded-[var(--r-1)] text-[var(--fg-subtle)] hover:text-[var(--fg-muted)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
          >
            <X className="h-3 w-3" strokeWidth={1.5} />
          </button>
        )}
      </div>

      <nav aria-label="Sections" className="-mx-1 flex flex-1 flex-col overflow-y-auto py-1">
        {visibleItems.length === 0 && (
          <p className="px-2 py-3 text-[var(--fs-xs)] text-[var(--fg-subtle)]">
            No matches.
          </p>
        )}
        {visibleItems.map((item) => {
          const isActive = item.id === activeId;
          const fieldHits = matchedFieldCounts?.[item.id];
          return (
            <a
              key={item.id}
              href={`#${item.id}`}
              onClick={(e) => {
                e.preventDefault();
                onSelect(item.id);
              }}
              aria-current={isActive ? 'page' : undefined}
              className={cn(
                'group relative flex items-center gap-2 rounded-[var(--r-1)]',
                'border-l-[3px] px-2.5 py-1.5 text-[var(--fs-sm)]',
                'transition-colors duration-[var(--motion-fast)] ease-[var(--ease-out)]',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]',
                isActive
                  ? 'border-l-[var(--accent)] bg-[var(--surface-1)] text-[var(--fg)]'
                  : 'border-l-transparent text-[var(--fg-muted)] hover:bg-[var(--surface-1)] hover:text-[var(--fg)]',
              )}
            >
              <span className="truncate">{item.label}</span>
              {!item.writable && (
                <span
                  aria-hidden
                  title="Read-only"
                  className="ml-auto inline-block h-1 w-1 shrink-0 rounded-full bg-[var(--fg-subtle)]"
                />
              )}
              {fieldHits && fieldHits > 0 && (
                <span
                  className="ml-auto inline-flex h-4 min-w-[1rem] items-center justify-center rounded-[var(--r-pill)] bg-[var(--accent-soft)] px-1 text-[10px] font-medium text-[var(--fg)]"
                  aria-label={`${fieldHits} matching fields`}
                >
                  {fieldHits}
                </span>
              )}
            </a>
          );
        })}
      </nav>

      {onResetSection && (
        <div className="border-t border-[var(--border)] pt-3">
          <Button
            variant="ghost"
            size="sm"
            onClick={onResetSection}
            className="w-full justify-start text-[var(--fs-xs)] text-[var(--fg-muted)]"
            aria-label="Reset current section to defaults"
          >
            Reset to defaults
          </Button>
        </div>
      )}
    </aside>
  );
}

/**
 * Tiny char-by-char subsequence match. Cheap and good enough for ~20
 * sections, which is the entire surface area.
 */
function fuzzyMatch(haystack: string, needle: string): boolean {
  if (needle.length === 0) return true;
  if (haystack.includes(needle)) return true;
  let i = 0;
  for (let j = 0; j < haystack.length && i < needle.length; j += 1) {
    if (haystack[j] === needle[i]) i += 1;
  }
  return i === needle.length;
}
