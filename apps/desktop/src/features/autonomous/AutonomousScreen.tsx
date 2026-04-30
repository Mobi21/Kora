import { useQueryClient } from '@tanstack/react-query';
import { Compass, RefreshCw, Settings as SettingsIcon } from 'lucide-react';
import { Link } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { EmptyState } from '@/components/ui/empty-state';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import type {
  AutonomousPlanView,
  AutonomousView,
  HealthState,
} from '@/lib/api/types';
import { cn } from '@/lib/utils';
import { AutonomousHeader } from './components/AutonomousHeader';
import { DecisionStrip } from './components/DecisionStrip';
import { PlanCard } from './components/PlanCard';
import { AUTONOMOUS_QUERY_KEY, useAutonomous, type PlanBucket } from './queries';

const WORKSPACE_MAX_WIDTH = 'var(--ws-autonomous)';

export function AutonomousScreen(): JSX.Element {
  const query = useAutonomous();
  const queryClient = useQueryClient();
  const refetch = (): void => {
    void queryClient.invalidateQueries({ queryKey: AUTONOMOUS_QUERY_KEY });
  };

  return (
    <div className="flex h-full w-full justify-center overflow-y-auto px-6 py-8">
      <div
        className="flex w-full flex-col gap-6"
        style={{ maxWidth: WORKSPACE_MAX_WIDTH }}
      >
        {query.isPending && !query.data ? (
          <LoadingState />
        ) : query.isError && !query.data ? (
          <ErrorState message={query.error?.message ?? 'Could not load autonomous plans.'} onRetry={refetch} />
        ) : query.data ? (
          <Loaded view={query.data} onRetry={refetch} />
        ) : null}
      </div>
    </div>
  );
}

interface LoadedProps {
  view: AutonomousView;
  onRetry: () => void;
}

function Loaded({ view, onRetry }: LoadedProps): JSX.Element {
  if (!view.enabled) {
    return (
      <>
        <AutonomousHeader enabled={false} />
        <Card className="px-6 py-12">
          <EmptyState
            icon={Compass}
            title="Autonomous work is off."
            description="Enable it in Settings to let Kora run plans on your behalf."
            action={
              <Button asChild>
                <Link to="/settings#autonomous">
                  <SettingsIcon className="h-4 w-4" strokeWidth={1.6} />
                  Open Settings
                </Link>
              </Button>
            }
          />
        </Card>
      </>
    );
  }

  return (
    <>
      <AutonomousHeader enabled={view.enabled} />
      {view.health !== 'ok' && (
        <UnhealthyBanner
          health={view.health}
          message={view.message}
          onRetry={onRetry}
        />
      )}

      <section aria-label="Open decisions" className="flex flex-col gap-3">
        <h2 className="text-label">Open decisions</h2>
        <DecisionStrip decisions={view.open_decisions} />
      </section>

      <PlansTabs view={view} />
    </>
  );
}

interface PlansTabsProps {
  view: AutonomousView;
}

const TABS: ReadonlyArray<{ id: PlanBucket; label: string }> = [
  { id: 'active', label: 'Active' },
  { id: 'queued', label: 'Queued' },
  { id: 'completed', label: 'Recently completed' },
];

function PlansTabs({ view }: PlansTabsProps): JSX.Element {
  const counts: Record<PlanBucket, number> = {
    active: view.active.length,
    queued: view.queued.length,
    completed: view.recently_completed.length,
  };
  const plans: Record<PlanBucket, readonly AutonomousPlanView[]> = {
    active: view.active,
    queued: view.queued,
    completed: view.recently_completed,
  };

  // Default to the first non-empty tab so the user lands on something useful.
  const defaultTab: PlanBucket =
    counts.active > 0
      ? 'active'
      : counts.queued > 0
        ? 'queued'
        : counts.completed > 0
          ? 'completed'
          : 'active';

  return (
    <Tabs defaultValue={defaultTab} className="flex flex-col gap-4">
      <TabsList aria-label="Plan buckets">
        {TABS.map((tab) => (
          <TabsTrigger key={tab.id} value={tab.id}>
            <span>{tab.label}</span>
            <span className="ml-2 font-mono num-tabular text-[var(--fs-xs)] text-[var(--fg-subtle)]">
              {counts[tab.id]}
            </span>
          </TabsTrigger>
        ))}
      </TabsList>

      {TABS.map((tab) => (
        <TabsContent key={tab.id} value={tab.id} className="mt-1">
          {plans[tab.id].length === 0 ? (
            <EmptyTab bucket={tab.id} />
          ) : (
            <div
              className="flex flex-col"
              style={{ gap: 'var(--space-y-card)' }}
            >
              {plans[tab.id].map((plan, index) => (
                <PlanCard
                  key={`${tab.id}-${plan.id}-${index}`}
                  plan={plan}
                  bucket={tab.id}
                />
              ))}
            </div>
          )}
        </TabsContent>
      ))}
    </Tabs>
  );
}

interface EmptyTabProps {
  bucket: PlanBucket;
}

const EMPTY_HEAD: Record<PlanBucket, string> = {
  active: 'No active plans.',
  queued: 'Queue is empty.',
  completed: 'Nothing completed recently.',
};

const EMPTY_BODY: Record<PlanBucket, string> = {
  active:
    "Kora isn't running anything in the background right now. Plans you start will land here.",
  queued: 'Plans waiting on capacity or a deadline will appear here before they begin.',
  completed: 'Recently finished plans will rest here for a short while before settling into history.',
};

function EmptyTab({ bucket }: EmptyTabProps): JSX.Element {
  return (
    <Card className="flex flex-col gap-2 px-6 py-10 text-center">
      <p
        className="font-narrative italic text-[var(--fg)]"
        style={{ fontSize: '1.0625rem' }}
      >
        {EMPTY_HEAD[bucket]}
      </p>
      <p className="text-[var(--fs-sm)] text-[var(--fg-muted)]">
        {EMPTY_BODY[bucket]}
      </p>
    </Card>
  );
}

interface UnhealthyBannerProps {
  health: HealthState;
  message: string | null;
  onRetry: () => void;
}

function UnhealthyBanner({ health, message, onRetry }: UnhealthyBannerProps): JSX.Element {
  return (
    <Card
      role="status"
      className={cn(
        'flex flex-col gap-2 border-l-4 px-4 py-3 sm:flex-row sm:items-center sm:justify-between',
      )}
      style={{ borderLeftColor: 'var(--ok)' }}
    >
      <div className="flex flex-col gap-1">
        <p
          className="font-narrative tracking-[var(--track-tight)] text-[var(--fg)]"
          style={{ fontSize: '1.0625rem', lineHeight: 1.35 }}
        >
          {message ?? 'Autonomous subsystem is degraded.'}
        </p>
        <span className="font-mono num-tabular text-[var(--fs-2xs)] uppercase tracking-[var(--track-label)] text-[var(--fg-subtle)]">
          health: {health}
        </span>
      </div>
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={onRetry}
        aria-label="Retry loading autonomous data"
        className="gap-1.5"
      >
        <RefreshCw className="h-3.5 w-3.5" strokeWidth={1.6} />
        Retry
      </Button>
    </Card>
  );
}

interface ErrorStateProps {
  message: string;
  onRetry: () => void;
}

function ErrorState({ message, onRetry }: ErrorStateProps): JSX.Element {
  return (
    <>
      <AutonomousHeader enabled={false} />
      <Card className="flex flex-col gap-3 px-6 py-10">
        <EmptyState
          icon={Compass}
          title="Couldn't reach the autonomous service."
          description={message}
          action={
            <Button onClick={onRetry} aria-label="Retry">
              <RefreshCw className="h-4 w-4" strokeWidth={1.6} />
              Retry
            </Button>
          }
        />
      </Card>
    </>
  );
}

function LoadingState(): JSX.Element {
  return (
    <>
      <header className="flex items-start justify-between gap-6">
        <div className="flex flex-col gap-2">
          <Skeleton className="h-8 w-48" />
          <Skeleton className="h-4 w-72" />
        </div>
        <Skeleton className="h-6 w-40" />
      </header>
      <section className="flex flex-col gap-3">
        <Skeleton className="h-3 w-32" />
        <Skeleton className="h-20 w-full" />
      </section>
      <section className="flex flex-col gap-3">
        <Skeleton className="h-9 w-72" />
        <PlanCardSkeleton />
        <PlanCardSkeleton />
      </section>
    </>
  );
}

function PlanCardSkeleton(): JSX.Element {
  return (
    <Card className="relative overflow-hidden p-5">
      <span
        aria-hidden
        className="absolute inset-y-0 left-0 w-1 bg-[var(--surface-3)]"
      />
      <div className="flex flex-col gap-4 pl-2">
        <div className="flex items-start justify-between gap-3">
          <div className="flex flex-col gap-2">
            <Skeleton className="h-5 w-56" />
            <Skeleton className="h-4 w-80" />
          </div>
          <Skeleton className="h-6 w-24" />
        </div>
        <div className="flex items-center gap-3">
          <Skeleton className="h-1 flex-1 rounded-full" />
          <Skeleton className="h-3 w-12" />
        </div>
        <div className="flex items-center gap-2">
          <Skeleton className="h-4 w-4 rounded-full" />
          <Skeleton className="h-4 w-4 rounded-full" />
          <Skeleton className="h-4 w-4 rounded-full" />
          <Skeleton className="h-4 w-4 rounded-full" />
        </div>
      </div>
    </Card>
  );
}

export default AutonomousScreen;
