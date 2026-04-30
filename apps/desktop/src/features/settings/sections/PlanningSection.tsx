import { SectionHeader } from '../components/SectionHeader';
import { MonoField } from '../components/MonoField';
import { ReadOnlyBanner } from '../components/ReadOnlyBanner';

const WHY = 'Edit via ~/.kora/settings.toml then restart Kora.';

interface PlanningSectionProps {
  highlightFields?: ReadonlySet<string>;
}

export function PlanningSection({
  highlightFields,
}: PlanningSectionProps): JSX.Element {
  const hl = (k: string) => highlightFields?.has(`planning.${k}`);

  return (
    <section className="flex flex-col gap-6">
      <SectionHeader
        id="planning"
        eyebrow="10"
        title="Planning cadence"
        description="When Kora nudges you to plan — daily, weekly, monthly."
      />
      <ReadOnlyBanner />

      <div className="grid grid-cols-2 gap-4">
        <MonoField
          label="Daily planning"
          value="enabled"
          whyTooltip={WHY}
          restartRequired
          highlight={hl('daily_planning_enabled')}
        />
        <MonoField
          label="Daily planning time"
          value="—"
          fallback="Defaults to first session of the day."
          whyTooltip={WHY}
          restartRequired
          highlight={hl('daily_planning_time')}
        />
        <MonoField
          label="Weekly planning"
          value="enabled · Sunday"
          whyTooltip={WHY}
          restartRequired
          highlight={hl('weekly_planning_enabled')}
        />
        <MonoField
          label="Weekly planning time"
          value="18:00"
          whyTooltip={WHY}
          restartRequired
          highlight={hl('weekly_planning_time')}
        />
        <MonoField
          label="Monthly reflection"
          value="enabled · day 1"
          whyTooltip={WHY}
          restartRequired
          highlight={hl('monthly_reflection_enabled')}
        />
      </div>
    </section>
  );
}
