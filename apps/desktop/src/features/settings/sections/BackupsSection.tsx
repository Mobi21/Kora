import type { InspectSetupReport } from '@/lib/api/types';
import { Skeleton } from '@/components/ui/skeleton';
import { SectionHeader } from '../components/SectionHeader';
import { MonoField } from '../components/MonoField';

interface BackupsSectionProps {
  setup: InspectSetupReport | null;
  loading?: boolean;
  highlightFields?: ReadonlySet<string>;
}

export function BackupsSection({
  setup,
  loading,
  highlightFields,
}: BackupsSectionProps): JSX.Element {
  const hl = (k: string) => highlightFields?.has(`backups.${k}`);

  // ~/.kora/settings.toml — not exposed by inspect, but we can hint at it.
  const settingsToml = '~/.kora/settings.toml';

  return (
    <section className="flex flex-col gap-6">
      <SectionHeader
        id="backups"
        eyebrow="19"
        title="Backups & data"
        description="The directories Kora actually reads from. Reveal in Finder to inspect."
      />

      {loading || !setup ? (
        <div className="flex flex-col gap-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-12 w-full" />
          ))}
        </div>
      ) : (
        <div className="flex flex-col gap-4">
          <MonoField
            label="Data directory"
            value={setup.data_dir}
            hint="Operational DB, lockfile, API token, daemon logs."
            reveal
            highlight={hl('data_dir')}
          />
          <MonoField
            label="Memory root"
            value={setup.memory.path}
            hint="Filesystem-canonical memory store."
            reveal
            highlight={hl('memory_root')}
          />
          <MonoField
            label="Settings file"
            value={settingsToml}
            hint="The TOML you edit when desktop fields are read-only."
            reveal
            highlight={hl('settings_toml')}
          />
        </div>
      )}
    </section>
  );
}
