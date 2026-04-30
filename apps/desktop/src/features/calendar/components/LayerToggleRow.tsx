import { Switch } from '@/components/ui/switch';
import { cn } from '@/lib/utils';
import type { CalendarLayerState } from '@/lib/api/types';

export interface LayerToggleRowProps {
  layer: CalendarLayerState;
  enabled: boolean;
  onToggle: (id: string, enabled: boolean) => void;
}

export function LayerToggleRow({
  layer,
  enabled,
  onToggle,
}: LayerToggleRowProps): JSX.Element {
  return (
    <label
      className={cn(
        'group flex cursor-pointer items-center gap-3 rounded-[var(--r-1)] px-2 py-1.5',
        'transition-colors duration-[var(--motion-fast)] ease-[var(--ease-out)]',
        'hover:bg-[var(--surface-2)]',
      )}
      title={layer.description}
    >
      <Switch
        checked={enabled}
        onCheckedChange={(v) => onToggle(layer.id, v)}
        aria-label={`Toggle ${layer.label} layer`}
      />
      <span
        className={cn(
          'flex-1 truncate text-[var(--fs-sm)]',
          enabled ? 'text-[var(--fg)]' : 'text-[var(--fg-muted)]',
        )}
      >
        {layer.label}
      </span>
      <span
        aria-hidden
        className="h-3 w-3 shrink-0 rounded-full"
        style={{ background: layer.color || 'var(--border-strong)' }}
      />
    </label>
  );
}
