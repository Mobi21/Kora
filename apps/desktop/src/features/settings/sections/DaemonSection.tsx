import type { DesktopStatusView, InspectSetupReport } from '@/lib/api/types';
import { Lock } from 'lucide-react';
import { Pill } from '@/components/ui/pill';
import { Skeleton } from '@/components/ui/skeleton';
import { SectionHeader } from '../components/SectionHeader';
import { MonoField } from '../components/MonoField';
import { ReadOnlyBanner } from '../components/ReadOnlyBanner';

const WHY = 'Edit via ~/.kora/settings.toml then restart Kora.';

interface DaemonSectionProps {
  setup: InspectSetupReport | null;
  status: DesktopStatusView | null;
  loading?: boolean;
  highlightFields?: ReadonlySet<string>;
}

export function DaemonSection({
  setup,
  status,
  loading,
  highlightFields,
}: DaemonSectionProps): JSX.Element {
  const hl = (k: string) => highlightFields?.has(`daemon.${k}`);
  const host = setup?.daemon.host ?? status?.host ?? '127.0.0.1';
  const port = setup?.daemon.port ?? status?.port;

  return (
    <section className="flex flex-col gap-6">
      <SectionHeader
        id="daemon"
        eyebrow="08"
        title="Daemon"
        description="Bind address and the cadence at which Kora wakes up to do background work."
      />
      <ReadOnlyBanner />

      {loading ? (
        <div className="flex flex-col gap-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-12 w-full" />
          ))}
        </div>
      ) : (
        <div className="flex flex-col gap-4">
          <MonoField
            label="Host"
            value={host}
            hint="Pinned to localhost. Kora never binds to public interfaces."
            whyTooltip={WHY}
            restartRequired
            highlight={hl('host')}
            trailing={
              <Pill status={host === '127.0.0.1' ? 'ok' : 'warn'} label="local-only">
                <Lock className="h-3 w-3" strokeWidth={1.5} aria-hidden />
                local
              </Pill>
            }
          />
          <MonoField
            label="Port"
            value={port ? String(port) : '—'}
            whyTooltip={WHY}
            restartRequired
            highlight={hl('port')}
          />
          <div className="grid grid-cols-2 gap-4">
            <MonoField
              label="Idle check interval"
              value="300s"
              whyTooltip={WHY}
              restartRequired
              highlight={hl('idle_check_interval')}
            />
            <MonoField
              label="Background interval"
              value="60s"
              whyTooltip={WHY}
              restartRequired
              highlight={hl('background_safe_interval')}
            />
          </div>
          <MonoField
            label="Daemon ownership"
            value={
              typeof window !== 'undefined' && window.kora
                ? 'managed by desktop app'
                : 'external (CLI)'
            }
            hint="Whether this app started the daemon process or is attaching to one."
            whyTooltip={WHY}
            highlight={hl('ownership')}
          />
        </div>
      )}
    </section>
  );
}
