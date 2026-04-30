import { SectionHeader } from '../components/SectionHeader';
import { MonoField } from '../components/MonoField';
import { ReadOnlyBanner } from '../components/ReadOnlyBanner';

const WHY = 'Edit via ~/.kora/settings.toml then restart Kora.';

interface QualitySectionProps {
  highlightFields?: ReadonlySet<string>;
}

export function QualitySection({ highlightFields }: QualitySectionProps): JSX.Element {
  const hl = (k: string) => highlightFields?.has(`quality.${k}`);

  return (
    <section className="flex flex-col gap-6">
      <SectionHeader
        id="quality"
        eyebrow="07"
        title="Quality"
        description="Confidence thresholds and regression-detection windows used by the quality gates."
      />
      <ReadOnlyBanner />

      <div className="grid grid-cols-2 gap-4">
        <MonoField
          label="Confidence threshold"
          value="0.60"
          whyTooltip={WHY}
          restartRequired
          highlight={hl('confidence_threshold')}
        />
        <MonoField
          label="Regression window"
          value="7 days"
          whyTooltip={WHY}
          restartRequired
          highlight={hl('regression_window_days')}
        />
        <MonoField
          label="Regression threshold"
          value="0.15"
          whyTooltip={WHY}
          restartRequired
          highlight={hl('regression_threshold')}
        />
        <MonoField
          label="LLM judge sampling"
          value="10%"
          whyTooltip={WHY}
          restartRequired
          highlight={hl('llm_judge_sampling')}
        />
      </div>
    </section>
  );
}
