import { Globe } from 'lucide-react';
import type { IntegrationsView } from '@/lib/api/types';
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

interface BrowserSectionProps {
  integrations: IntegrationsView | null;
  loading?: boolean;
  highlightFields?: ReadonlySet<string>;
}

export function BrowserSection({
  integrations,
  loading,
  highlightFields,
}: BrowserSectionProps): JSX.Element {
  const hl = (k: string) => highlightFields?.has(`browser.${k}`);
  const browserIntegration =
    integrations?.integrations.find((i) => i.kind === 'browser') ?? null;

  const enabled = browserIntegration?.enabled ?? false;
  const meta = browserIntegration?.metadata ?? {};
  const binaryPath =
    typeof meta['binary_path'] === 'string' ? (meta['binary_path'] as string) : '';
  const profile =
    typeof meta['default_profile'] === 'string'
      ? (meta['default_profile'] as string)
      : '';
  const clipTarget =
    typeof meta['clip_target'] === 'string'
      ? (meta['clip_target'] as string)
      : 'vault';

  return (
    <section className="flex flex-col gap-6">
      <SectionHeader
        id="browser"
        eyebrow="15"
        title="Browser"
        description="Agent-browser integration — disabled until you opt in."
        trailing={
          browserIntegration ? (
            <Pill
              status={HEALTH_TO_PILL[browserIntegration.health] ?? 'unknown'}
              label={browserIntegration.health}
            />
          ) : undefined
        }
      />
      <ReadOnlyBanner />

      {loading ? (
        <div className="flex flex-col gap-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-12 w-full" />
          ))}
        </div>
      ) : !browserIntegration ? (
        <div className="flex items-center gap-3 rounded-[var(--r-2)] border border-[var(--border)] bg-[var(--surface-1)] px-3 py-4 text-[var(--fs-sm)] text-[var(--fg-muted)]">
          <Globe className="h-4 w-4 text-[var(--fg-subtle)]" strokeWidth={1.5} />
          Browser integration is not registered with the daemon.
        </div>
      ) : (
        <div className="flex flex-col gap-4">
          <MonoField
            label="Enabled"
            value={enabled ? 'true' : 'false'}
            whyTooltip={WHY}
            restartRequired
            highlight={hl('enabled')}
          />
          <MonoField
            label="Binary path"
            value={binaryPath || '—'}
            fallback="Auto-detect from PATH."
            reveal
            whyTooltip={WHY}
            restartRequired
            highlight={hl('binary_path')}
          />
          <MonoField
            label="Default profile"
            value={profile || '—'}
            fallback="Use the browser's default profile."
            whyTooltip={WHY}
            restartRequired
            highlight={hl('default_profile')}
          />
          <MonoField
            label="Clip target"
            value={clipTarget}
            hint='Where memory clips land: "vault" | "memory" | "both" | "none".'
            whyTooltip={WHY}
            restartRequired
            highlight={hl('clip_target')}
          />
          <div className="grid grid-cols-2 gap-4">
            <MonoField
              label="Max session"
              value="3600s"
              whyTooltip={WHY}
              restartRequired
              highlight={hl('max_session_duration_seconds')}
            />
            <MonoField
              label="Command timeout"
              value="30s"
              whyTooltip={WHY}
              restartRequired
              highlight={hl('command_timeout_seconds')}
            />
          </div>
        </div>
      )}
    </section>
  );
}
