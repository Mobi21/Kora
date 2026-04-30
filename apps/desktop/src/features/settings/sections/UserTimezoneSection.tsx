import { useMemo } from 'react';
import { SectionHeader } from '../components/SectionHeader';
import { MonoField } from '../components/MonoField';
import { ReadOnlyBanner } from '../components/ReadOnlyBanner';

const WHY = 'Edit via ~/.kora/settings.toml then restart Kora.';

interface UserTimezoneSectionProps {
  highlightFields?: ReadonlySet<string>;
}

export function UserTimezoneSection({
  highlightFields,
}: UserTimezoneSectionProps): JSX.Element {
  const hl = (k: string) => highlightFields?.has(`timezone.${k}`);

  const detected = useMemo(() => {
    try {
      return Intl.DateTimeFormat().resolvedOptions().timeZone || '—';
    } catch {
      return '—';
    }
  }, []);

  const offset = useMemo(() => {
    try {
      const m = -new Date().getTimezoneOffset();
      const hh = String(Math.floor(Math.abs(m) / 60)).padStart(2, '0');
      const mm = String(Math.abs(m) % 60).padStart(2, '0');
      return `${m >= 0 ? '+' : '-'}${hh}:${mm}`;
    } catch {
      return '—';
    }
  }, []);

  return (
    <section className="flex flex-col gap-6">
      <SectionHeader
        id="timezone"
        eyebrow="18"
        title="User timezone"
        description="Storage is always UTC. This controls how times render in the UI."
      />
      <ReadOnlyBanner />

      <div className="grid grid-cols-2 gap-4">
        <MonoField
          label="IANA timezone"
          value={detected}
          fallback="Not detected."
          whyTooltip={WHY}
          highlight={hl('user_tz')}
        />
        <MonoField
          label="Current offset"
          value={offset}
          whyTooltip={WHY}
          highlight={hl('offset')}
        />
      </div>
    </section>
  );
}
