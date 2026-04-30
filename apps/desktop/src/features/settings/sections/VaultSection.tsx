import type { DesktopStatusView } from '@/lib/api/types';
import { Skeleton } from '@/components/ui/skeleton';
import { Pill, type PillStatus } from '@/components/ui/pill';
import { SectionHeader } from '../components/SectionHeader';
import { MonoField } from '../components/MonoField';
import { ReadOnlyBanner } from '../components/ReadOnlyBanner';

const WHY = 'Edit via ~/.kora/settings.toml then restart Kora.';

const HEALTH_TO_PILL: Record<string, PillStatus> = {
  ok: 'ok',
  unconfigured: 'unknown',
  missing: 'degraded',
  degraded: 'warn',
};

interface VaultSectionProps {
  status: DesktopStatusView | null;
  loading?: boolean;
  highlightFields?: ReadonlySet<string>;
}

export function VaultSection({
  status,
  loading,
  highlightFields,
}: VaultSectionProps): JSX.Element {
  const hl = (k: string) => highlightFields?.has(`vault.${k}`);
  const vault = status?.vault ?? null;

  return (
    <section className="flex flex-col gap-6">
      <SectionHeader
        id="vault"
        eyebrow="16"
        title="Vault"
        description="Optional Obsidian-friendly mirror of your memory."
        trailing={
          vault ? (
            <Pill status={HEALTH_TO_PILL[vault.health] ?? 'unknown'} label={vault.health} />
          ) : undefined
        }
      />
      <ReadOnlyBanner />

      {loading || !vault ? (
        <div className="flex flex-col gap-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-12 w-full" />
          ))}
        </div>
      ) : (
        <div className="flex flex-col gap-4">
          <MonoField
            label="Enabled"
            value={vault.enabled ? 'true' : 'false'}
            whyTooltip={WHY}
            restartRequired
            highlight={hl('enabled')}
          />
          <MonoField
            label="Vault path"
            value={vault.path}
            fallback="No vault configured."
            reveal
            whyTooltip={WHY}
            restartRequired
            highlight={hl('path')}
          />
          <MonoField
            label="Memory root"
            value={vault.memory_root}
            reveal
            whyTooltip={WHY}
            highlight={hl('memory_root')}
          />
          {vault.message && (
            <p className="text-[var(--fs-xs)] text-[var(--fg-muted)]">{vault.message}</p>
          )}
        </div>
      )}
    </section>
  );
}
