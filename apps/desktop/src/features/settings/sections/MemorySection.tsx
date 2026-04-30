import { Database, RefreshCcw } from 'lucide-react';
import type { InspectSetupReport } from '@/lib/api/types';
import { Button } from '@/components/ui/button';
import { Pill } from '@/components/ui/pill';
import { Skeleton } from '@/components/ui/skeleton';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { SectionHeader } from '../components/SectionHeader';
import { MonoField } from '../components/MonoField';
import { ReadOnlyBanner } from '../components/ReadOnlyBanner';

const WHY_TOOLTIP = 'Edit via ~/.kora/settings.toml then restart Kora.';

interface MemorySectionProps {
  setup: InspectSetupReport | null;
  loading?: boolean;
  highlightFields?: ReadonlySet<string>;
}

export function MemorySection({ setup, loading, highlightFields }: MemorySectionProps): JSX.Element {
  const hl = (k: string) => highlightFields?.has(`memory.${k}`);
  const projectionPresent = setup?.projection_db.exists ?? false;

  return (
    <section className="flex flex-col gap-6">
      <SectionHeader
        id="memory"
        eyebrow="05"
        title="Memory"
        description="Filesystem-canonical store, embedding model, and the projection DB derived from it."
        trailing={
          <Tooltip>
            <TooltipTrigger asChild>
              <span>
                <Button
                  variant="outline"
                  size="sm"
                  disabled
                  aria-label="Rebuild memory projection"
                  className="opacity-60"
                >
                  <RefreshCcw className="h-3.5 w-3.5" strokeWidth={1.5} />
                  Rebuild projection
                </Button>
              </span>
            </TooltipTrigger>
            <TooltipContent>
              No daemon endpoint yet. Restart the daemon to rebuild.
            </TooltipContent>
          </Tooltip>
        }
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
            label="Memory root"
            value={setup.memory.path}
            reveal
            whyTooltip={WHY_TOOLTIP}
            restartRequired
            highlight={hl('kora_memory_path')}
          />
          <div className="grid grid-cols-2 gap-4">
            <MonoField
              label="Embedding model"
              value={setup.memory.embedding_model}
              whyTooltip={WHY_TOOLTIP}
              restartRequired
              highlight={hl('embedding_model')}
            />
            <MonoField
              label="Embedding dims"
              value={String(setup.memory.embedding_dims)}
              whyTooltip={WHY_TOOLTIP}
              restartRequired
              highlight={hl('embedding_dims')}
            />
          </div>
          <MonoField
            label="Projection DB"
            value={setup.projection_db.path}
            reveal
            whyTooltip={WHY_TOOLTIP}
            restartRequired
            highlight={hl('projection_db')}
            trailing={
              <Pill
                status={projectionPresent ? 'ok' : 'warn'}
                label={projectionPresent ? 'present' : 'will rebuild'}
              />
            }
          />
          <div className="grid grid-cols-2 gap-4">
            <MonoField
              label="Hybrid weights"
              value="0.7 vector / 0.3 FTS"
              hint="Defaults. See settings.toml to override."
              whyTooltip={WHY_TOOLTIP}
              restartRequired
              highlight={hl('hybrid_weights')}
            />
            <MonoField
              label="Dedup threshold"
              value="0.50"
              whyTooltip={WHY_TOOLTIP}
              restartRequired
              highlight={hl('dedup_threshold')}
            />
          </div>
          <MonoField
            label="Signal scanner patterns (max)"
            value="—"
            fallback="Not exposed by daemon yet."
            hint="Default is 50; visible in settings.toml under [memory]."
            whyTooltip={WHY_TOOLTIP}
            restartRequired
            highlight={hl('signal_scanner')}
            trailing={
              <Database
                className="h-3.5 w-3.5 text-[var(--fg-subtle)]"
                strokeWidth={1.5}
                aria-hidden
              />
            }
          />
        </div>
      )}
    </section>
  );
}
