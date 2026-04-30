import { Server } from 'lucide-react';
import type { IntegrationsView } from '@/lib/api/types';
import { Pill, type PillStatus } from '@/components/ui/pill';
import { Skeleton } from '@/components/ui/skeleton';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { SectionHeader } from '../components/SectionHeader';
import { MonoField } from '../components/MonoField';
import { ReadOnlyBanner } from '../components/ReadOnlyBanner';

const WHY = 'Edit via ~/.kora/settings.toml then restart Kora.';

const HEALTH_TO_PILL: Record<string, PillStatus> = {
  ok: 'ok',
  degraded: 'warn',
  unavailable: 'degraded',
  unconfigured: 'unknown',
};

interface MCPSectionProps {
  integrations: IntegrationsView | null;
  loading?: boolean;
  highlightFields?: ReadonlySet<string>;
}

export function MCPSection({
  integrations,
  loading,
  highlightFields,
}: MCPSectionProps): JSX.Element {
  const hl = (k: string) => highlightFields?.has(`mcp.${k}`);
  const mcpServers = (integrations?.integrations ?? []).filter((i) => i.kind === 'mcp');
  const mcpTools = integrations?.tools ?? [];

  return (
    <section className="flex flex-col gap-6">
      <SectionHeader
        id="mcp"
        eyebrow="13"
        title="MCP"
        description="Model Context Protocol servers Kora can spawn for tool access."
      />
      <ReadOnlyBanner />

      <MonoField
        label="Startup timeout"
        value="30s"
        whyTooltip={WHY}
        restartRequired
        highlight={hl('startup_timeout')}
      />

      <div className="flex flex-col gap-2">
        <p className="text-[var(--fs-xs)] uppercase tracking-[0.02em] text-[var(--fg-muted)]">
          Servers
        </p>
        {loading ? (
          <Skeleton className="h-20 w-full" />
        ) : mcpServers.length === 0 ? (
          <div className="flex items-center gap-3 rounded-[var(--r-2)] border border-[var(--border)] bg-[var(--surface-1)] px-3 py-4 text-[var(--fs-sm)] text-[var(--fg-muted)]">
            <Server className="h-4 w-4 text-[var(--fg-subtle)]" strokeWidth={1.5} />
            No MCP servers registered. Add one under <code className="font-mono">[mcp.servers]</code> in <code className="font-mono">settings.toml</code>.
          </div>
        ) : (
          <ul className="flex flex-col gap-2">
            {mcpServers.map((server) => {
              const tools = mcpTools.filter((t) => t.integration_id === server.id);
              const failing = tools.filter((t) => t.status === 'failing').length;
              return (
                <li
                  key={server.id}
                  className="rounded-[var(--r-2)] border border-[var(--border)] bg-[var(--surface-1)] p-3"
                >
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <span className="text-[var(--fs-sm)] font-medium text-[var(--fg)]">
                        {server.label}
                      </span>
                      <span className="font-mono text-[var(--fs-2xs)] text-[var(--fg-subtle)]">
                        {server.id}
                      </span>
                    </div>
                    <Pill
                      status={HEALTH_TO_PILL[server.health] ?? 'unknown'}
                      label={server.health}
                    />
                  </div>
                  {server.detail && (
                    <p className="mt-1 text-[var(--fs-xs)] text-[var(--fg-muted)]">
                      {server.detail}
                    </p>
                  )}
                  <dl className="mt-2 grid grid-cols-3 gap-2 text-[var(--fs-xs)]">
                    <div>
                      <dt className="text-[var(--fg-subtle)]">Enabled</dt>
                      <dd className="font-mono text-[var(--fg)]">
                        {server.enabled ? 'yes' : 'no'}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-[var(--fg-subtle)]">Tools</dt>
                      <dd className="font-mono text-[var(--fg)] num-tabular">
                        {server.tools_available}
                        {failing > 0 && (
                          <span className="ml-1 text-[var(--danger)]">
                            ({failing} failing)
                          </span>
                        )}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-[var(--fg-subtle)]">Last check</dt>
                      <dd className="font-mono text-[var(--fg)] num-tabular">
                        {server.last_check_at
                          ? new Date(server.last_check_at).toLocaleTimeString()
                          : '—'}
                      </dd>
                    </div>
                  </dl>
                  {tools.length > 0 && (
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <p className="mt-2 truncate text-[var(--fs-xs)] text-[var(--fg-muted)]">
                          tools: {tools.map((t) => t.name).join(', ')}
                        </p>
                      </TooltipTrigger>
                      <TooltipContent>
                        Discovered via MCP handshake at server start.
                      </TooltipContent>
                    </Tooltip>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </section>
  );
}
