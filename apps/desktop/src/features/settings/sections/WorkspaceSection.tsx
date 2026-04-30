import type { IntegrationsView } from '@/lib/api/types';
import { Briefcase } from 'lucide-react';
import { Pill, type PillStatus } from '@/components/ui/pill';
import { Skeleton } from '@/components/ui/skeleton';
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

interface WorkspaceSectionProps {
  integrations: IntegrationsView | null;
  loading?: boolean;
  highlightFields?: ReadonlySet<string>;
}

export function WorkspaceSection({
  integrations,
  loading,
  highlightFields,
}: WorkspaceSectionProps): JSX.Element {
  const hl = (k: string) => highlightFields?.has(`workspace.${k}`);
  const workspace =
    integrations?.integrations.find((i) => i.kind === 'workspace') ?? null;
  const meta = workspace?.metadata ?? {};
  const account = typeof meta['account'] === 'string' ? (meta['account'] as string) : '';
  const googleEmail =
    typeof meta['google_email'] === 'string' ? (meta['google_email'] as string) : '';
  const readOnly =
    typeof meta['read_only'] === 'boolean' ? (meta['read_only'] as boolean) : null;
  const defaultCalendar =
    typeof meta['default_calendar_id'] === 'string'
      ? (meta['default_calendar_id'] as string)
      : '';
  const mcpServerName =
    typeof meta['mcp_server_name'] === 'string'
      ? (meta['mcp_server_name'] as string)
      : '';
  const toolMap = (meta['tool_map'] ?? null) as Record<string, unknown> | null;
  const toolMapCount = toolMap ? Object.keys(toolMap).length : 0;

  return (
    <section className="flex flex-col gap-6">
      <SectionHeader
        id="workspace"
        eyebrow="17"
        title="Workspace"
        description="Google Workspace + calendar wiring used by the workspace capability."
        trailing={
          workspace ? (
            <Pill
              status={HEALTH_TO_PILL[workspace.health] ?? 'unknown'}
              label={workspace.health}
            />
          ) : undefined
        }
      />
      <ReadOnlyBanner />

      {loading ? (
        <div className="flex flex-col gap-3">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-12 w-full" />
          ))}
        </div>
      ) : !workspace ? (
        <div className="flex items-center gap-3 rounded-[var(--r-2)] border border-[var(--border)] bg-[var(--surface-1)] px-3 py-4 text-[var(--fs-sm)] text-[var(--fg-muted)]">
          <Briefcase className="h-4 w-4 text-[var(--fg-subtle)]" strokeWidth={1.5} />
          Workspace integration is not registered with the daemon.
        </div>
      ) : (
        <div className="flex flex-col gap-4">
          <MonoField
            label="Account"
            value={account || '—'}
            fallback="Not configured."
            whyTooltip={WHY}
            restartRequired
            highlight={hl('account')}
          />
          <MonoField
            label="Google email"
            value={googleEmail || '—'}
            fallback="Not configured."
            whyTooltip={WHY}
            restartRequired
            highlight={hl('google_email')}
          />
          <div className="grid grid-cols-2 gap-4">
            <MonoField
              label="Read-only"
              value={readOnly == null ? '—' : readOnly ? 'true' : 'false'}
              fallback="Not exposed by daemon yet."
              whyTooltip={WHY}
              restartRequired
              highlight={hl('read_only')}
            />
            <MonoField
              label="Default calendar"
              value={defaultCalendar || '—'}
              fallback="No primary calendar set."
              whyTooltip={WHY}
              restartRequired
              highlight={hl('default_calendar_id')}
            />
          </div>
          <MonoField
            label="MCP server name"
            value={mcpServerName || '—'}
            fallback="Not configured."
            whyTooltip={WHY}
            restartRequired
            highlight={hl('mcp_server_name')}
          />
          <MonoField
            label="Tool map"
            value={toolMapCount > 0 ? `${toolMapCount} entr${toolMapCount === 1 ? 'y' : 'ies'}` : '—'}
            fallback="No tool aliases configured."
            whyTooltip={WHY}
            restartRequired
            highlight={hl('tool_map')}
          />
        </div>
      )}
    </section>
  );
}
