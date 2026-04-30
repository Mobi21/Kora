import { SectionHeader } from '../components/SectionHeader';
import { MonoField } from '../components/MonoField';
import { ReadOnlyBanner } from '../components/ReadOnlyBanner';

const WHY = 'Edit via ~/.kora/settings.toml then restart Kora.';

interface AgentsSectionProps {
  highlightFields?: ReadonlySet<string>;
}

export function AgentsSection({ highlightFields }: AgentsSectionProps): JSX.Element {
  const hl = (k: string) => highlightFields?.has(`agents.${k}`);

  return (
    <section className="flex flex-col gap-6">
      <SectionHeader
        id="agents"
        eyebrow="06"
        title="Agents"
        description="Worker harness limits — iteration budget, timeouts, and reviewer sampling."
      />
      <ReadOnlyBanner />

      <div className="grid grid-cols-2 gap-4">
        <MonoField
          label="Iteration budget"
          value="150"
          hint="Hard ceiling on planner/executor turns."
          whyTooltip={WHY}
          restartRequired
          highlight={hl('iteration_budget')}
        />
        <MonoField
          label="Default timeout"
          value="300s"
          whyTooltip={WHY}
          restartRequired
          highlight={hl('default_timeout')}
        />
        <MonoField
          label="Loop detection threshold"
          value="3"
          hint="Repeated identical actions before short-circuiting."
          whyTooltip={WHY}
          restartRequired
          highlight={hl('loop_detection_threshold')}
        />
        <MonoField
          label="Reviewer sampling"
          value="10%"
          whyTooltip={WHY}
          restartRequired
          highlight={hl('reviewer_sampling_rate')}
        />
        <MonoField
          label="Thinking for planner"
          value="enabled"
          whyTooltip={WHY}
          restartRequired
          highlight={hl('thinking_for_planner')}
        />
      </div>
    </section>
  );
}
