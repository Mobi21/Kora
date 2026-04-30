import { SectionHeader } from '../components/SectionHeader';
import { MonoField } from '../components/MonoField';
import { ReadOnlyBanner } from '../components/ReadOnlyBanner';

const WHY = 'Edit via ~/.kora/settings.toml then restart Kora.';

interface OrchestrationSectionProps {
  highlightFields?: ReadonlySet<string>;
}

export function OrchestrationSection({
  highlightFields,
}: OrchestrationSectionProps): JSX.Element {
  const hl = (k: string) => highlightFields?.has(`orchestration.${k}`);

  return (
    <section className="flex flex-col gap-6">
      <SectionHeader
        id="orchestration"
        eyebrow="12"
        title="Orchestration"
        description="Background trigger evaluator and tick rate."
      />
      <ReadOnlyBanner />

      <div className="grid grid-cols-2 gap-4">
        <MonoField
          label="Trigger evaluator"
          value="enabled"
          whyTooltip={WHY}
          restartRequired
          highlight={hl('trigger_evaluator_enabled')}
        />
        <MonoField
          label="Tick interval"
          value="5.0s"
          whyTooltip={WHY}
          restartRequired
          highlight={hl('trigger_tick_interval_seconds')}
        />
      </div>
    </section>
  );
}
