import type { InspectSetupReport } from '@/lib/api/types';
import { Skeleton } from '@/components/ui/skeleton';
import { SectionHeader } from '../components/SectionHeader';
import { MonoField } from '../components/MonoField';
import { SecretField } from '../components/SecretField';
import { ReadOnlyBanner } from '../components/ReadOnlyBanner';

interface LLMSectionProps {
  setup: InspectSetupReport | null;
  loading?: boolean;
  highlightFields?: ReadonlySet<string>;
}

const WHY_TOOLTIP = 'Edit via ~/.kora/settings.toml then restart Kora.';

export function LLMSection({ setup, loading, highlightFields }: LLMSectionProps): JSX.Element {
  const hl = (k: string) => highlightFields?.has(`llm.${k}`);

  return (
    <section className="flex flex-col gap-6">
      <SectionHeader
        id="llm"
        eyebrow="04"
        title="LLM"
        description="The provider, model, and base URL Kora resolved at startup."
      />
      <ReadOnlyBanner />

      {loading || !setup ? (
        <div className="flex flex-col gap-3">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-12 w-full" />
          ))}
        </div>
      ) : (
        <div className="flex flex-col gap-4">
          <MonoField
            label="Provider"
            value={setup.llm.provider}
            whyTooltip={WHY_TOOLTIP}
            restartRequired
            highlight={hl('provider')}
          />
          <MonoField
            label="Model"
            value={setup.llm.model}
            whyTooltip={WHY_TOOLTIP}
            restartRequired
            highlight={hl('model')}
          />
          <MonoField
            label="Background model"
            value={setup.llm.model /* daemon doesn't expose distinct field */}
            hint="Defaults to the primary model when unset."
            whyTooltip={WHY_TOOLTIP}
            restartRequired
            highlight={hl('background_model')}
          />
          <MonoField
            label="API base"
            value={setup.llm.api_base}
            whyTooltip={WHY_TOOLTIP}
            restartRequired
            highlight={hl('api_base')}
          />
          <SecretField
            label="API key"
            present={!!setup.llm.api_base}
            hint="Stored in `~/.kora/settings.toml` or `.env`. Never sent over the wire."
            whyTooltip={WHY_TOOLTIP}
            restartRequired
            highlight={hl('api_key')}
          />
          <div className="grid grid-cols-2 gap-4">
            <MonoField
              label="Timeout"
              value={`${setup.llm.timeout}s`}
              whyTooltip={WHY_TOOLTIP}
              restartRequired
            />
            <MonoField
              label="Max tokens"
              value={String(setup.llm.max_tokens)}
              whyTooltip={WHY_TOOLTIP}
              restartRequired
            />
          </div>
        </div>
      )}
    </section>
  );
}
