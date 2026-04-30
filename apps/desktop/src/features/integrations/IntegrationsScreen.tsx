import { useMemo } from 'react';
import { AlertTriangle, RefreshCw } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Divider } from '@/components/ui/divider';
import { EmptyState } from '@/components/ui/empty-state';
import { Skeleton } from '@/components/ui/skeleton';
import { cn } from '@/lib/utils';
import type {
  IntegrationsView,
  IntegrationStatusView,
  IntegrationToolView,
} from '@/lib/api/types';
import { IntegrationsHeader } from './components/IntegrationsHeader';
import { IntegrationSection } from './components/IntegrationSection';
import { IntegrationCard } from './components/IntegrationCard';
import { MCPRow } from './components/MCPRow';
import { ToolListPanel } from './components/ToolListPanel';
import {
  KIND_LABEL,
  KIND_ORDER,
  groupIntegrations,
  toolsByIntegration,
  useIntegrationsQuery,
  useRecheckIntegrations,
  type IntegrationKind,
} from './queries';

export function IntegrationsScreen(): JSX.Element {
  const query = useIntegrationsQuery();
  const recheck = useRecheckIntegrations();

  const handleRefresh = () => {
    recheck();
    void query.refetch();
  };

  return (
    <div className="flex h-full w-full justify-center overflow-y-auto">
      <div
        className="flex w-full flex-col gap-8 px-6 pb-16 pt-10 sm:px-8 lg:px-10"
        style={{ maxWidth: '960px' }}
      >
        <IntegrationsHeader
          generatedAt={query.data?.generated_at ?? null}
          isFetching={query.isFetching}
          onRefresh={handleRefresh}
        />

        {query.isPending ? (
          <IntegrationsSkeleton />
        ) : query.isError ? (
          <FullErrorState onRetry={handleRefresh} />
        ) : query.data ? (
          <IntegrationsContent view={query.data} onRecheck={handleRefresh} />
        ) : (
          <IntegrationsSkeleton />
        )}
      </div>
    </div>
  );
}

interface IntegrationsContentProps {
  view: IntegrationsView;
  onRecheck: () => void;
}

function IntegrationsContent({
  view,
  onRecheck,
}: IntegrationsContentProps): JSX.Element {
  const grouped = useMemo(() => groupIntegrations(view), [view]);
  const toolsByIntId = useMemo(() => toolsByIntegration(view), [view]);

  return (
    <>
      <div className="flex flex-col gap-8">
        {KIND_ORDER.map((kind) => (
          <SectionForKind
            key={kind}
            kind={kind}
            integrations={grouped[kind]}
            toolsByIntId={toolsByIntId}
            onRecheck={onRecheck}
          />
        ))}
      </div>

      <Divider />

      <ToolListPanel integrations={view.integrations} tools={view.tools} />
    </>
  );
}

interface SectionForKindProps {
  kind: IntegrationKind;
  integrations: IntegrationStatusView[];
  toolsByIntId: Map<string, IntegrationToolView[]>;
  onRecheck: () => void;
}

function SectionForKind({
  kind,
  integrations,
  toolsByIntId,
  onRecheck,
}: SectionForKindProps): JSX.Element {
  const empty = integrations.length === 0;
  const navigate = useNavigate();

  if (kind === 'mcp') {
    return (
      <IntegrationSection
        title={KIND_LABEL.mcp}
        count={integrations.length}
        isEmpty={empty}
        empty="No MCP servers configured."
        trailing={
          empty ? (
            <Button
              variant="outline"
              size="sm"
              onClick={() => navigate('/settings#mcp')}
              aria-label="Open MCP settings"
            >
              Open Settings
            </Button>
          ) : undefined
        }
      >
        <Card className="flex flex-col p-0">
          {integrations.map((integration, index) => (
            <div key={integration.id}>
              {index > 0 && <Divider className="mx-3" />}
              <MCPRow
                integration={integration}
                tools={toolsByIntId.get(integration.id) ?? []}
                onRecheck={onRecheck}
              />
            </div>
          ))}
        </Card>
      </IntegrationSection>
    );
  }

  const unconfiguredCopy = unconfiguredCopyForKind(kind);

  return (
    <IntegrationSection title={KIND_LABEL[kind]} isEmpty={empty}>
      <div className="flex flex-col gap-3">
        {integrations.map((integration) => (
          <IntegrationCard
            key={integration.id}
            integration={integration}
            onRecheck={onRecheck}
            unconfiguredCopy={unconfiguredCopy}
          />
        ))}
      </div>
    </IntegrationSection>
  );
}

function unconfiguredCopyForKind(kind: IntegrationKind): string | undefined {
  switch (kind) {
    case 'vault':
      return 'Connect your Obsidian vault to enable canonical memory.';
    case 'workspace':
      return 'Connect a workspace to let Kora reach calendar and email.';
    case 'browser':
      return 'Configure a browser binary to let Kora navigate the web.';
    case 'claude_code':
      return 'Point Kora at your Claude Code CLI to enable delegation.';
    default:
      return undefined;
  }
}

function FullErrorState({ onRetry }: { onRetry: () => void }): JSX.Element {
  return (
    <div className="flex flex-1 items-center justify-center pt-16">
      <EmptyState
        icon={AlertTriangle}
        title="Integrations didn't load."
        description="Kora couldn't reach the integrations service. We'll keep trying."
        action={
          <Button onClick={onRetry} aria-label="Retry loading integrations">
            <RefreshCw className="h-4 w-4" strokeWidth={1.5} aria-hidden />
            Try again
          </Button>
        }
      />
    </div>
  );
}

function IntegrationsSkeleton(): JSX.Element {
  return (
    <div aria-busy aria-live="polite" className="flex flex-col gap-8">
      {KIND_ORDER.map((kind) => (
        <section key={kind} className="flex flex-col gap-3">
          <div className="flex items-center justify-between">
            <Skeleton className="h-6 w-32" />
            <Skeleton className="h-3 w-16" />
          </div>
          {kind === 'mcp' ? (
            <Card className="flex flex-col gap-3 p-3">
              {Array.from({ length: 3 }).map((_, i) => (
                <SkeletonRow key={i} />
              ))}
            </Card>
          ) : (
            <SkeletonCard />
          )}
        </section>
      ))}
      <SkeletonToolList />
    </div>
  );
}

function SkeletonCard(): JSX.Element {
  return (
    <Card className="flex items-center gap-3 p-[var(--pad)]">
      <Skeleton className="h-8 w-8 rounded-[var(--r-2)]" />
      <div className="flex flex-1 flex-col gap-1.5">
        <Skeleton className="h-4 w-40" />
        <Skeleton className="h-3 w-64" />
      </div>
      <Skeleton className="h-5 w-20 rounded-[var(--r-pill)]" />
    </Card>
  );
}

function SkeletonRow(): JSX.Element {
  return (
    <div className="flex items-center gap-3 py-1">
      <Skeleton className="h-3 w-1 self-stretch rounded-[var(--r-pill)]" />
      <div className="flex flex-1 flex-col gap-1.5">
        <Skeleton className="h-4 w-48" />
        <Skeleton className="h-3 w-72" />
      </div>
      <Skeleton className="h-5 w-12 rounded-[var(--r-pill)]" />
      <Skeleton className="h-5 w-20 rounded-[var(--r-pill)]" />
    </div>
  );
}

function SkeletonToolList(): JSX.Element {
  return (
    <section className="flex flex-col gap-3">
      <Skeleton className="h-6 w-24" />
      <Skeleton className="h-9 w-full" />
      <div
        className={cn(
          'flex flex-col rounded-[var(--r-2)] border border-[var(--border)]',
          'bg-[var(--surface-1)] divide-y divide-[var(--border)]',
        )}
      >
        {Array.from({ length: 4 }).map((_, i) => (
          <div
            key={i}
            className="grid grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)_auto] items-center gap-3 px-3 py-2"
          >
            <Skeleton className="h-3 w-20" />
            <Skeleton className="h-3 w-40" />
            <Skeleton className="h-5 w-16 rounded-[var(--r-pill)]" />
          </div>
        ))}
      </div>
    </section>
  );
}

export default IntegrationsScreen;
