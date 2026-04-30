import { useState } from 'react';
import { Card } from '@/components/ui/card';
import { Pill } from '@/components/ui/pill';
import type { AutonomousPlanView } from '@/lib/api/types';
import { cn } from '@/lib/utils';
import {
  bucketRuleColor,
  statusToPill,
  type PlanBucket,
} from '../queries';
import { CheckpointRail } from './CheckpointRail';
import { DecisionCard } from './DecisionStrip';
import { PlanActionsBar } from './PlanActionsBar';
import { PlanProgress } from './PlanProgress';

interface PlanCardProps {
  plan: AutonomousPlanView;
  bucket: PlanBucket;
}

export function PlanCard({ plan, bucket }: PlanCardProps): JSX.Element {
  const [historyExpanded, setHistoryExpanded] = useState(false);
  const pill = statusToPill(plan.status);
  const ruleColor = bucketRuleColor(bucket);

  return (
    <Card
      className="relative overflow-hidden p-5"
      aria-label={`Plan ${plan.title}`}
    >
      {/* 4px provenance left rule */}
      <span
        aria-hidden
        className="absolute inset-y-0 left-0 w-1"
        style={{ background: ruleColor }}
      />

      <div className="flex flex-col gap-4 pl-2">
        <header className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
          <div className="flex min-w-0 flex-col gap-1">
            <div className="flex items-center gap-2">
              <span
                aria-hidden
                className="h-1.5 w-1.5 rounded-full"
                style={{ background: ruleColor }}
              />
              <h2
                className="font-narrative tracking-[var(--track-tight)] text-[var(--fg)]"
                style={{ fontSize: '1.125rem', lineHeight: 1.3 }}
              >
                {plan.title}
              </h2>
            </div>
            <p
              className={cn(
                'truncate text-[var(--fs-base)] text-[var(--fg-muted)]',
              )}
              title={plan.goal}
            >
              {plan.goal}
            </p>
          </div>

          <div className="flex shrink-0 items-center gap-3">
            <Pill status={pill.status} label={pill.label}>
              {pill.label}
            </Pill>
            <span
              className="font-mono num-tabular text-[var(--fs-xs)] text-[var(--fg-subtle)]"
              title={`pipeline ${plan.pipeline_id}`}
            >
              {plan.pipeline_id}
            </span>
          </div>
        </header>

        <PlanProgress
          progress={plan.progress}
          completedSteps={plan.completed_steps}
          totalSteps={plan.total_steps}
          currentStep={plan.current_step}
          lastActivityAt={plan.last_activity_at}
        />

        <div
          id={`plan-history-${plan.id}`}
          className="flex flex-col gap-2"
        >
          <CheckpointRail
            checkpoints={plan.checkpoints}
            expanded={historyExpanded}
          />
        </div>

        {plan.open_decisions.length > 0 && (
          <section
            aria-label={`Open decisions for ${plan.title}`}
            className="flex flex-col gap-2"
          >
            <span className="text-label">Decisions waiting on you</span>
            <div className="flex flex-col gap-2">
              {plan.open_decisions.map((decision) => (
                <DecisionCard
                  key={decision.id}
                  decision={decision}
                  compact
                />
              ))}
            </div>
          </section>
        )}

        <PlanActionsBar
          plan={plan}
          historyExpanded={historyExpanded}
          onToggleHistory={() => setHistoryExpanded((v) => !v)}
        />
      </div>
    </Card>
  );
}
