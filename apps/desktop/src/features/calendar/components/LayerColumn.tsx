import { useMemo } from 'react';
import { cn } from '@/lib/utils';
import type { CalendarLayerState } from '@/lib/api/types';
import { Skeleton } from '@/components/ui/skeleton';
import { Layers } from 'lucide-react';
import { LayerToggleRow } from './LayerToggleRow';
import { PresetMenu } from './PresetMenu';
import {
  applyPreset,
  type LayerStateMap,
  type PresetId,
} from '../utils/layers';

export interface LayerColumnProps {
  layers: CalendarLayerState[] | null;
  loading: boolean;
  preset: PresetId;
  enabled: LayerStateMap;
  onPresetChange: (preset: PresetId) => void;
  onLayerToggle: (id: string, enabled: boolean) => void;
}

export function LayerColumn({
  layers,
  loading,
  preset,
  enabled,
  onPresetChange,
  onLayerToggle,
}: LayerColumnProps): JSX.Element {
  const resolved = useMemo(() => {
    if (!layers) return enabled;
    if (preset === 'custom') return enabled;
    return applyPreset(preset, layers, enabled);
  }, [layers, preset, enabled]);

  return (
    <aside
      aria-label="Calendar layers"
      className={cn(
        'flex w-56 shrink-0 flex-col gap-3 border-r border-[var(--border)]',
        'bg-[var(--bg)] px-3 py-4',
      )}
    >
      <div className="flex items-center gap-2 px-1">
        <Layers className="h-4 w-4 text-[var(--fg-muted)]" strokeWidth={1.5} />
        <span className="text-[var(--fs-2xs)] uppercase tracking-[0.02em] text-[var(--fg-muted)]">
          Layers
        </span>
      </div>

      <PresetMenu preset={preset} onChange={onPresetChange} />

      <div className="mt-1 flex flex-col gap-0.5">
        {loading && !layers
          ? Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="flex items-center gap-3 px-2 py-1.5">
                <Skeleton className="h-5 w-9" />
                <Skeleton className="h-3 flex-1" />
                <Skeleton className="h-3 w-3 rounded-full" />
              </div>
            ))
          : (layers ?? []).map((layer) => (
              <LayerToggleRow
                key={layer.id}
                layer={layer}
                enabled={resolved[layer.id] ?? layer.enabled}
                onToggle={onLayerToggle}
              />
            ))}
        {!loading && layers && layers.length === 0 && (
          <p className="px-2 py-1 text-[var(--fs-xs)] text-[var(--fg-muted)]">
            No layers configured.
          </p>
        )}
      </div>
    </aside>
  );
}
