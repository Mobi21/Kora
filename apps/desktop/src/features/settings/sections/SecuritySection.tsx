import type { InspectSetupReport } from '@/lib/api/types';
import { Skeleton } from '@/components/ui/skeleton';
import { Pill } from '@/components/ui/pill';
import { SectionHeader } from '../components/SectionHeader';
import { MonoField } from '../components/MonoField';
import { SecretField } from '../components/SecretField';
import { ReadOnlyBanner } from '../components/ReadOnlyBanner';

const WHY = 'Edit via ~/.kora/settings.toml then restart Kora.';

interface SecuritySectionProps {
  setup: InspectSetupReport | null;
  loading?: boolean;
  highlightFields?: ReadonlySet<string>;
}

export function SecuritySection({
  setup,
  loading,
  highlightFields,
}: SecuritySectionProps): JSX.Element {
  const hl = (k: string) => highlightFields?.has(`security.${k}`);

  return (
    <section className="flex flex-col gap-6">
      <SectionHeader
        id="security"
        eyebrow="14"
        title="Security"
        description="Local-only auth, CORS allow-list, and prompt-injection scanning."
      />
      <ReadOnlyBanner />

      {loading || !setup ? (
        <div className="flex flex-col gap-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-12 w-full" />
          ))}
        </div>
      ) : (
        <div className="flex flex-col gap-4">
          <MonoField
            label="API token path"
            value={setup.security.api_token_path}
            reveal
            whyTooltip={WHY}
            restartRequired
            highlight={hl('api_token_path')}
          />
          <SecretField
            label="API token"
            present={setup.security.token_file_exists}
            hint="The desktop app reads this file at startup. Never sent to remote services."
            whyTooltip={WHY}
            restartRequired
            highlight={hl('api_token')}
          />
          <div className="grid grid-cols-2 gap-4">
            <MonoField
              label="Auth mode"
              value={setup.security.auth_mode}
              hint='"prompt" requires user approval; "trust_all" auto-grants.'
              whyTooltip={WHY}
              restartRequired
              highlight={hl('auth_mode')}
              trailing={
                <Pill
                  status={setup.security.auth_mode === 'prompt' ? 'ok' : 'warn'}
                  label={setup.security.auth_mode}
                />
              }
            />
            <MonoField
              label="Injection scan"
              value={setup.security.injection_scan_enabled ? 'enabled' : 'disabled'}
              whyTooltip={WHY}
              restartRequired
              highlight={hl('injection_scan_enabled')}
            />
          </div>
          <MonoField
            label="CORS origins"
            value={
              setup.security.cors_origins?.length
                ? setup.security.cors_origins.join(', ')
                : '—'
            }
            fallback="No origins configured."
            whyTooltip={WHY}
            restartRequired
            highlight={hl('cors_origins')}
          />
        </div>
      )}
    </section>
  );
}
