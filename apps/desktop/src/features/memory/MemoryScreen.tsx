import { useCallback, useMemo, useState, type ReactNode } from 'react';
import { Brain } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { EmptyState } from '@/components/ui/empty-state';
import { Skeleton } from '@/components/ui/skeleton';
import { cn } from '@/lib/utils';
import type {
  ContextPackSummary,
  FutureBridgeSummary,
  VaultMemoryItem,
} from '@/lib/api/types';
import {
  useDebouncedValue,
  useVaultContextQuery,
  useVaultSearchQuery,
} from './queries';
import { MemoryHeader } from './components/MemoryHeader';
import { MemorySearchBar, type CertaintyFilter } from './components/MemorySearchBar';
import { MemorySection } from './components/MemorySection';
import { ContextPackCard } from './components/ContextPackCard';
import { FutureBridgeChip } from './components/FutureBridgeChip';
import { VaultHealthBanner } from './components/VaultHealthBanner';
import { CorrectionDialog } from './components/CorrectionDialog';
import type { RowOperation } from './components/RowActions';

const CONFIRMED_HOVER: RowOperation[] = ['correct', 'merge', 'mark_stale', 'delete'];
const CORRECTIONS_HOVER: RowOperation[] = ['correct', 'merge', 'delete'];

export function MemoryScreen(): JSX.Element {
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<CertaintyFilter>('all');
  const debounced = useDebouncedValue(query.trim(), 250);

  const isSearching = debounced.length > 0;
  const ctx = useVaultContextQuery();
  const search = useVaultSearchQuery(debounced);

  const [dialog, setDialog] = useState<{
    memory: VaultMemoryItem | null;
    operation: RowOperation;
    open: boolean;
  }>({ memory: null, operation: 'correct', open: false });

  const handleAction = useCallback((op: RowOperation, memory: VaultMemoryItem) => {
    setDialog({ memory, operation: op, open: true });
  }, []);

  const closeDialog = useCallback(
    (open: boolean) => setDialog((d) => ({ ...d, open })),
    [],
  );

  const vault = (search.data?.vault ?? ctx.data?.vault) ?? null;

  const handleOpenArtifact = useCallback((path: string | null) => {
    if (!path) return;
    if (typeof window !== 'undefined' && window.kora?.openExternal) {
      void window.kora.openExternal(path);
    }
  }, []);

  const handleOpenPack = useCallback(
    (pack: ContextPackSummary) => handleOpenArtifact(pack.artifact_path),
    [handleOpenArtifact],
  );
  const handleOpenBridge = useCallback(
    (bridge: FutureBridgeSummary) => handleOpenArtifact(bridge.artifact_path),
    [handleOpenArtifact],
  );

  const filterMatches = useCallback(
    (items: VaultMemoryItem[]) => {
      if (filter === 'all') return items;
      return items.filter((m) => m.certainty === filter);
    },
    [filter],
  );

  // Section data derived from search or context.
  const sections = useMemo(() => {
    if (isSearching && search.data) {
      const results = filterMatches(search.data.results);
      return {
        kind: 'search' as const,
        confirmed: results.filter((m) => m.certainty === 'confirmed'),
        guesses: results.filter((m) => m.certainty === 'guess'),
        stale: results.filter((m) => m.certainty === 'stale' || m.certainty === 'unknown'),
        corrections: results.filter((m) => m.certainty === 'correction'),
        all: results,
      };
    }
    const c = ctx.data;
    if (!c) {
      return {
        kind: 'context' as const,
        confirmed: [],
        guesses: [],
        stale: [],
        corrections: [],
        all: [],
      };
    }
    const recent = filterMatches(c.recent_memories);
    return {
      kind: 'context' as const,
      confirmed: recent.filter((m) => m.certainty === 'confirmed'),
      guesses: recent.filter((m) => m.certainty === 'guess'),
      stale: filterMatches(c.uncertain_or_stale),
      corrections: filterMatches(c.corrections),
      all: recent,
    };
  }, [isSearching, search.data, ctx.data, filterMatches]);

  const contextPacks = ctx.data?.context_packs ?? [];
  const futureBridges = ctx.data?.future_bridges ?? [];

  const ctxLoading = ctx.isLoading && !ctx.data;
  const searchLoading = isSearching && search.isLoading && !search.data;
  const sectionLoading = isSearching ? searchLoading : ctxLoading;

  // Catastrophic-error empty state: both fail and we have no data at all.
  const totalFail =
    !ctx.data && ctx.isError && (!isSearching || (search.isError && !search.data));

  if (totalFail) {
    return (
      <ScreenScaffold>
        <EmptyState
          icon={Brain}
          title="Memory didn't load."
          description={ctx.error?.message ?? 'The vault context could not be fetched.'}
          action={
            <Button onClick={() => ctx.refetch()} aria-label="Retry loading memory">
              Retry
            </Button>
          }
        />
      </ScreenScaffold>
    );
  }

  const sectionError = (isError: boolean, msg: string | undefined, retry: () => void) =>
    isError ? { message: msg ?? 'Section failed to load.', onRetry: retry } : null;

  return (
    <>
      <ScreenScaffold>
        <div className="flex flex-col gap-6">
          <MemoryHeader vault={vault} />

          <MemorySearchBar
            value={query}
            onChange={setQuery}
            filter={filter}
            onFilterChange={setFilter}
          />

          {vault && vault.health !== 'ok' && <VaultHealthBanner vault={vault} />}

          <div className="flex flex-col gap-8">
            <MemorySection
              title="Confirmed"
              subtitle="Things you've validated."
              memories={sections.confirmed}
              hoverActions={CONFIRMED_HOVER}
              onAction={handleAction}
              emptyText="Nothing confirmed yet."
              loading={sectionLoading}
              error={sectionError(
                isSearching ? search.isError : ctx.isError,
                isSearching ? search.error?.message : ctx.error?.message,
                () => (isSearching ? search.refetch() : ctx.refetch()),
              )}
            />

            <MemorySection
              title="Guesses"
              subtitle="Things Kora is inferring. Correct or confirm them."
              memories={sections.guesses}
              primaryActions={[
                { op: 'confirm', variant: 'primary' },
                { op: 'correct', variant: 'subtle' },
              ]}
              hoverActions={['mark_stale', 'delete']}
              onAction={handleAction}
              emptyText="No guesses to confirm."
              loading={sectionLoading}
              error={sectionError(
                isSearching ? search.isError : ctx.isError,
                isSearching ? search.error?.message : ctx.error?.message,
                () => (isSearching ? search.refetch() : ctx.refetch()),
              )}
            />

            <MemorySection
              title="Stale or uncertain"
              subtitle="These haven't been refreshed in a while."
              memories={sections.stale}
              primaryActions={[
                { op: 'correct', variant: 'subtle', label: 'Refresh' },
                { op: 'confirm', variant: 'ghost', label: 'Mark fresh' },
              ]}
              hoverActions={['delete']}
              onAction={handleAction}
              emptyText="Nothing stale right now."
              loading={sectionLoading}
              error={sectionError(
                isSearching ? search.isError : ctx.isError,
                isSearching ? search.error?.message : ctx.error?.message,
                () => (isSearching ? search.refetch() : ctx.refetch()),
              )}
            />

            <MemorySection
              title="Corrections"
              subtitle="Recent edits to what Kora believed."
              memories={sections.corrections}
              hoverActions={CORRECTIONS_HOVER}
              onAction={handleAction}
              emptyText="No corrections logged yet."
              loading={sectionLoading}
              error={sectionError(
                isSearching ? search.isError : ctx.isError,
                isSearching ? search.error?.message : ctx.error?.message,
                () => (isSearching ? search.refetch() : ctx.refetch()),
              )}
            />

            {/* Context packs and future bridges are only available from the
                context endpoint; they're hidden while searching. */}
            {!isSearching && (
              <>
                <ContextPacksSection
                  packs={contextPacks}
                  loading={ctxLoading}
                  onOpen={handleOpenPack}
                />
                <FutureBridgesSection
                  bridges={futureBridges}
                  loading={ctxLoading}
                  onOpen={handleOpenBridge}
                />
              </>
            )}
          </div>
        </div>

        <CorrectionDialog
          open={dialog.open}
          onOpenChange={closeDialog}
          memory={dialog.memory}
          operation={dialog.operation}
        />
      </ScreenScaffold>
    </>
  );
}

function ScreenScaffold({ children }: { children: ReactNode }): JSX.Element {
  return (
    <div className="flex h-full w-full justify-center overflow-y-auto">
      <div
        className={cn('w-full px-6 py-8')}
        style={{ maxWidth: 'var(--ws-memory)' }}
      >
        {children}
      </div>
    </div>
  );
}

interface ContextPacksSectionProps {
  packs: ContextPackSummary[];
  loading: boolean;
  onOpen: (pack: ContextPackSummary) => void;
}

function ContextPacksSection({
  packs,
  loading,
  onOpen,
}: ContextPacksSectionProps): JSX.Element {
  return (
    <section className="flex flex-col gap-3" aria-label="Context packs">
      <header className="space-y-0.5">
        <h2 className="font-narrative text-[var(--fs-2xl)] tracking-[var(--track-tight)] text-[var(--fg)]">
          Context packs
        </h2>
        <p className="text-[var(--fs-sm)] text-[var(--fg-muted)]">
          Saved context Kora hands tools and agents.
        </p>
      </header>
      {loading ? (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 3 }, (_, i) => (
            <div
              key={i}
              className="flex h-[120px] flex-col gap-2 rounded-[var(--r-2)] border border-[var(--border)] bg-[var(--surface-1)] p-[var(--pad)]"
            >
              <Skeleton className="h-4 w-2/3" />
              <Skeleton className="h-3 w-1/3" />
              <Skeleton className="mt-auto h-7 w-20" />
            </div>
          ))}
        </div>
      ) : packs.length === 0 ? (
        <EmptySection text="No context packs yet." />
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {packs.map((pack) => (
            <ContextPackCard key={pack.id} pack={pack} onOpen={onOpen} />
          ))}
        </div>
      )}
    </section>
  );
}

interface FutureBridgesSectionProps {
  bridges: FutureBridgeSummary[];
  loading: boolean;
  onOpen: (bridge: FutureBridgeSummary) => void;
}

function FutureBridgesSection({
  bridges,
  loading,
  onOpen,
}: FutureBridgesSectionProps): JSX.Element {
  return (
    <section className="flex flex-col gap-3" aria-label="Future bridges">
      <header className="space-y-0.5">
        <h2 className="font-narrative text-[var(--fs-2xl)] tracking-[var(--track-tight)] text-[var(--fg)]">
          Future bridges
        </h2>
        <p className="text-[var(--fs-sm)] text-[var(--fg-muted)]">
          Notes Kora will surface when the moment is closer.
        </p>
      </header>
      {loading ? (
        <div className="flex gap-3 overflow-x-auto pb-2">
          {Array.from({ length: 3 }, (_, i) => (
            <div
              key={i}
              className="flex w-72 shrink-0 flex-col gap-2 rounded-[var(--r-2)] border border-[var(--border)] bg-[var(--surface-1)] p-3"
            >
              <Skeleton className="h-3 w-full" />
              <Skeleton className="h-3 w-5/6" />
              <Skeleton className="mt-2 h-3 w-1/3" />
            </div>
          ))}
        </div>
      ) : bridges.length === 0 ? (
        <EmptySection text="No future bridges yet." />
      ) : (
        <div className="flex gap-3 overflow-x-auto pb-2">
          {bridges.map((b) => (
            <FutureBridgeChip key={b.id} bridge={b} onOpen={onOpen} />
          ))}
        </div>
      )}
    </section>
  );
}

function EmptySection({ text }: { text: string }): JSX.Element {
  return (
    <div className="rounded-[var(--r-2)] border border-[var(--border)] bg-[var(--surface-1)] px-4 py-8 text-center">
      <p className="font-narrative italic text-[var(--fs-sm)] text-[var(--fg-muted)]">{text}</p>
    </div>
  );
}

export default MemoryScreen;
