import { SectionHeader } from '../components/SectionHeader';
import { MonoField } from '../components/MonoField';
import { ReadOnlyBanner } from '../components/ReadOnlyBanner';

const WHY = 'Edit via ~/.kora/settings.toml then restart Kora.';

interface NotificationsSectionProps {
  highlightFields?: ReadonlySet<string>;
}

export function NotificationsSection({
  highlightFields,
}: NotificationsSectionProps): JSX.Element {
  const hl = (k: string) => highlightFields?.has(`notifications.${k}`);

  return (
    <section className="flex flex-col gap-6">
      <SectionHeader
        id="notifications"
        eyebrow="09"
        title="Notifications"
        description="Proactive engagement and ADHD-aware pacing — cooldowns, DND, hyperfocus respect."
      />
      <ReadOnlyBanner />

      <div className="grid grid-cols-2 gap-4">
        <MonoField
          label="Enabled"
          value="true"
          whyTooltip={WHY}
          restartRequired
          highlight={hl('enabled')}
        />
        <MonoField
          label="Cooldown"
          value="15 min"
          whyTooltip={WHY}
          restartRequired
          highlight={hl('cooldown_minutes')}
        />
        <MonoField
          label="Respect DND"
          value="true"
          whyTooltip={WHY}
          restartRequired
          highlight={hl('respect_dnd')}
        />
        <MonoField
          label="Re-engagement"
          value="4 hours"
          whyTooltip={WHY}
          restartRequired
          highlight={hl('re_engagement_hours')}
        />
        <MonoField
          label="Hyperfocus threshold"
          value="3 turns"
          hint="Soften pings when sustained focus is detected."
          whyTooltip={WHY}
          restartRequired
          highlight={hl('hyperfocus_threshold_turns')}
        />
        <MonoField
          label="Max per hour"
          value="4"
          whyTooltip={WHY}
          restartRequired
          highlight={hl('max_per_hour')}
        />
        <MonoField
          label="DND start"
          value="—"
          fallback="Not set in settings.toml."
          whyTooltip={WHY}
          restartRequired
          highlight={hl('dnd_start')}
        />
        <MonoField
          label="DND end"
          value="—"
          fallback="Not set in settings.toml."
          whyTooltip={WHY}
          restartRequired
          highlight={hl('dnd_end')}
        />
      </div>
    </section>
  );
}
