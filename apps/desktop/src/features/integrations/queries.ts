import { useMemo } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { createApiClient, type ApiClient } from '@/lib/api/client';
import { useConnection } from '@/lib/api/connection';
import type {
  IntegrationsView,
  IntegrationStatusView,
  IntegrationToolView,
} from '@/lib/api/types';

// Local query-key namespace for the Integrations screen so an optimistic
// recheck doesn't churn other screens that may also read integrations.
export const integrationsKeys = {
  view: () => ['integrations', 'view'] as const,
} as const;

function useApi(): ApiClient | null {
  const conn = useConnection();
  return useMemo(() => (conn ? createApiClient(conn) : null), [conn]);
}

export function useIntegrationsQuery() {
  const api = useApi();
  return useQuery<IntegrationsView, Error>({
    queryKey: integrationsKeys.view(),
    queryFn: () => api!.integrations(),
    enabled: !!api,
    staleTime: 15_000,
    refetchInterval: 60_000,
    retry: 1,
  });
}

// Returns a stable callback that invalidates the integrations query and
// also the shared `kora` integrations key so other surfaces (e.g. command
// palette or runtime indicator) get fresh data on a recheck.
export function useRecheckIntegrations() {
  const qc = useQueryClient();
  return () => {
    qc.invalidateQueries({ queryKey: integrationsKeys.view() });
    qc.invalidateQueries({ queryKey: ['kora', 'integrations'] });
  };
}

export type IntegrationKind = IntegrationStatusView['kind'];

export const KIND_ORDER: IntegrationKind[] = [
  'workspace',
  'vault',
  'browser',
  'claude_code',
  'mcp',
];

export const KIND_LABEL: Record<IntegrationKind, string> = {
  workspace: 'Workspace',
  vault: 'Vault',
  browser: 'Browser',
  claude_code: 'Claude Code',
  mcp: 'MCP servers',
};

// Group integrations by kind, preserving server order within each kind.
export function groupIntegrations(
  view: IntegrationsView | undefined,
): Record<IntegrationKind, IntegrationStatusView[]> {
  const empty: Record<IntegrationKind, IntegrationStatusView[]> = {
    workspace: [],
    vault: [],
    browser: [],
    claude_code: [],
    mcp: [],
  };
  if (!view) return empty;
  for (const integration of view.integrations) {
    empty[integration.kind].push(integration);
  }
  return empty;
}

// Group tools by integration id for fast lookup when rendering a server's
// disclosure row or the cross-server tools panel.
export function toolsByIntegration(
  view: IntegrationsView | undefined,
): Map<string, IntegrationToolView[]> {
  const map = new Map<string, IntegrationToolView[]>();
  if (!view) return map;
  for (const tool of view.tools) {
    const list = map.get(tool.integration_id);
    if (list) {
      list.push(tool);
    } else {
      map.set(tool.integration_id, [tool]);
    }
  }
  return map;
}
