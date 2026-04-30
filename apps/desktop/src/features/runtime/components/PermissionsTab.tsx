import { useMemo } from 'react';
import { KeyRound, RefreshCcw, ShieldOff } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { EmptyState } from '@/components/ui/empty-state';
import { Pill, type PillStatus } from '@/components/ui/pill';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';
import { formatTime } from '@/lib/dates';
import { usePermissions } from '@/lib/api/queries';
import type { PermissionGrant } from '@/lib/api/types';

interface CapabilityGroup {
  id: string;
  label: string;
  match: (tool: string) => boolean;
}

const GROUPS: CapabilityGroup[] = [
  {
    id: 'filesystem',
    label: 'Filesystem',
    match: (t) => t.includes('filesystem') || t.includes('workspace_file'),
  },
  {
    id: 'workspace',
    label: 'Workspace',
    match: (t) => t.startsWith('workspace') || t.includes('shell') || t.includes('process'),
  },
  {
    id: 'browser',
    label: 'Browser',
    match: (t) => t.includes('browser') || t.includes('web_fetch'),
  },
  {
    id: 'mcp',
    label: 'MCP',
    match: (t) => t.startsWith('mcp') || t.includes('mcp_'),
  },
  {
    id: 'vault',
    label: 'Vault',
    match: (t) => t.includes('vault') || t.includes('memory'),
  },
];

const DECISION_TO_PILL: Record<string, PillStatus> = {
  allow: 'ok',
  granted: 'ok',
  deny: 'degraded',
  denied: 'degraded',
  prompt: 'warn',
  pending: 'warn',
};

function decisionPill(decision: string): {
  status: PillStatus;
  label: string;
} {
  const normalised = decision.toLowerCase();
  return {
    status: DECISION_TO_PILL[normalised] ?? 'unknown',
    label: decision,
  };
}

function groupGrants(grants: PermissionGrant[]) {
  const buckets = new Map<string, PermissionGrant[]>();
  const other: PermissionGrant[] = [];
  for (const grant of grants) {
    const tool = (grant.tool_name ?? '').toLowerCase();
    const group = GROUPS.find((g) => g.match(tool));
    if (!group) {
      other.push(grant);
      continue;
    }
    const list = buckets.get(group.id) ?? [];
    list.push(grant);
    buckets.set(group.id, list);
  }
  return { buckets, other };
}

function GrantSkeleton(): JSX.Element {
  return (
    <div className="flex items-center justify-between gap-4 rounded-[var(--r-2)] border border-[var(--border)] bg-[var(--surface-1)] px-4 py-3">
      <div className="flex-1 space-y-1.5">
        <Skeleton className="h-3 w-48" />
        <Skeleton className="h-3 w-32" />
      </div>
      <div className="flex items-center gap-2">
        <Skeleton className="h-5 w-16" />
        <Skeleton className="h-7 w-16" />
      </div>
    </div>
  );
}

function GrantRow({ grant }: { grant: PermissionGrant }): JSX.Element {
  const decision = decisionPill(grant.decision);
  const granted = grant.granted_at ? formatTime(grant.granted_at) : '—';
  return (
    <div className="flex items-center justify-between gap-4 rounded-[var(--r-2)] border border-[var(--border)] bg-[var(--surface-1)] px-4 py-3">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="font-mono text-[var(--fs-sm)] text-[var(--fg)]">
            {grant.tool_name}
          </span>
          <span className="font-mono text-[var(--fs-2xs)] uppercase tracking-[var(--track-label)] text-[var(--fg-subtle)]">
            risk: {grant.risk_level || 'unknown'}
          </span>
        </div>
        <div className="mt-1 flex items-center gap-3 text-[var(--fs-xs)] text-[var(--fg-muted)]">
          <span
            className="truncate font-mono"
            title={grant.scope}
          >
            {grant.scope || '—'}
          </span>
          <span aria-hidden>·</span>
          <span className="font-mono num-tabular" title={grant.granted_at ?? undefined}>
            {granted}
          </span>
        </div>
        {grant.reason && (
          <p className="mt-1 text-[var(--fs-xs)] text-[var(--fg-muted)]">
            {grant.reason}
          </p>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <Pill status={decision.status} label={decision.label} />
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="sm"
              disabled
              aria-label="Revoke grant (not yet supported)"
            >
              <ShieldOff className="h-3.5 w-3.5" strokeWidth={1.5} />
              Revoke
            </Button>
          </TooltipTrigger>
          <TooltipContent>Not yet supported by daemon</TooltipContent>
        </Tooltip>
      </div>
    </div>
  );
}

function GroupSection({
  label,
  grants,
}: {
  label: string;
  grants: PermissionGrant[];
}): JSX.Element {
  return (
    <section className="space-y-2">
      <header className="flex items-baseline justify-between gap-3">
        <h3 className="text-[var(--fs-2xs)] uppercase tracking-[var(--track-label)] text-[var(--fg-muted)]">
          {label}
        </h3>
        <span className="font-mono text-[var(--fs-2xs)] text-[var(--fg-subtle)] num-tabular">
          {grants.length}
        </span>
      </header>
      <div className="space-y-2">
        {grants.map((grant, idx) => (
          <GrantRow
            key={`${grant.tool_name}-${grant.granted_at}-${idx}`}
            grant={grant}
          />
        ))}
      </div>
    </section>
  );
}

export function PermissionsTab(): JSX.Element {
  const permissions = usePermissions();
  const grants = useMemo(
    () => permissions.data?.grants ?? [],
    [permissions.data?.grants],
  );
  const grouped = useMemo(() => groupGrants(grants), [grants]);

  return (
    <section className="space-y-6">
      <header className="flex items-start justify-between gap-3">
        <div className="space-y-1">
          <h2 className="font-narrative text-[var(--fs-2xl)] tracking-[var(--track-tight)] text-[var(--fg)]">
            Permissions
          </h2>
          <p className="text-[var(--fs-sm)] text-[var(--fg-muted)]">
            Tool-call grants the daemon has recorded. Most recent first.
          </p>
        </div>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => permissions.refetch()}
          disabled={permissions.isFetching}
          aria-label="Refresh permissions"
        >
          <RefreshCcw
            className={cn('h-3.5 w-3.5', permissions.isFetching && 'opacity-60')}
            strokeWidth={1.5}
          />
          Refresh
        </Button>
      </header>

      {permissions.isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 5 }).map((_, i) => (
            <GrantSkeleton key={i} />
          ))}
        </div>
      ) : permissions.isError ? (
        <EmptyState
          icon={KeyRound}
          title="Permissions aren't available"
          description="The permissions endpoint failed. Try refreshing — if it keeps failing, the daemon may not yet expose grants."
          action={
            <Button onClick={() => permissions.refetch()}>
              <RefreshCcw className="h-4 w-4" strokeWidth={1.5} />
              Refresh
            </Button>
          }
        />
      ) : grants.length === 0 ? (
        <EmptyState
          icon={KeyRound}
          title="No grants yet"
          description="Once Kora needs to use a tool, the decision will be recorded here for review."
        />
      ) : (
        <div className="space-y-6">
          {GROUPS.map((group) => {
            const list = grouped.buckets.get(group.id);
            if (!list || list.length === 0) return null;
            return <GroupSection key={group.id} label={group.label} grants={list} />;
          })}
          {grouped.other.length > 0 && (
            <GroupSection label="Other" grants={grouped.other} />
          )}
        </div>
      )}
    </section>
  );
}
