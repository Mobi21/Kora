import { SectionHeader } from '../components/SectionHeader';
import { MonoField } from '../components/MonoField';
import { ReadOnlyBanner } from '../components/ReadOnlyBanner';

const WHY = 'Edit via ~/.kora/settings.toml then restart Kora.';

interface AutonomousSectionProps {
  highlightFields?: ReadonlySet<string>;
}

export function AutonomousSection({
  highlightFields,
}: AutonomousSectionProps): JSX.Element {
  const hl = (k: string) => highlightFields?.has(`autonomous.${k}`);

  return (
    <section className="flex flex-col gap-6">
      <SectionHeader
        id="autonomous"
        eyebrow="11"
        title="Autonomous"
        description="Long-running execution budgets, checkpoints, and decision timeouts."
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
          label="Daily cost limit"
          value="$5.00"
          whyTooltip={WHY}
          restartRequired
          highlight={hl('daily_cost_limit')}
        />
        <MonoField
          label="Per-session cost"
          value="$1.00"
          whyTooltip={WHY}
          restartRequired
          highlight={hl('per_session_cost_limit')}
        />
        <MonoField
          label="Max session"
          value="4 hours"
          whyTooltip={WHY}
          restartRequired
          highlight={hl('max_session_hours')}
        />
        <MonoField
          label="Checkpoint interval"
          value="30 min"
          whyTooltip={WHY}
          restartRequired
          highlight={hl('checkpoint_interval_minutes')}
        />
        <MonoField
          label="Auto-continue"
          value="30s"
          whyTooltip={WHY}
          restartRequired
          highlight={hl('auto_continue_seconds')}
        />
        <MonoField
          label="Decision timeout"
          value="10 min"
          whyTooltip={WHY}
          restartRequired
          highlight={hl('decision_timeout_minutes')}
        />
        <MonoField
          label="Concurrent tasks"
          value="1"
          whyTooltip={WHY}
          restartRequired
          highlight={hl('max_concurrent_tasks')}
        />
        <MonoField
          label="Request limit / hour"
          value="—"
          fallback="No cap configured."
          whyTooltip={WHY}
          restartRequired
          highlight={hl('request_limit_per_hour')}
        />
        <MonoField
          label="Token warning threshold"
          value="85%"
          whyTooltip={WHY}
          restartRequired
          highlight={hl('token_warning_threshold')}
        />
        <MonoField
          label="Cost warning threshold"
          value="80%"
          whyTooltip={WHY}
          restartRequired
          highlight={hl('cost_warning_threshold')}
        />
        <MonoField
          label="Overlap similarity"
          value="0.60"
          whyTooltip={WHY}
          restartRequired
          highlight={hl('overlap_similarity_threshold')}
        />
      </div>
    </section>
  );
}
