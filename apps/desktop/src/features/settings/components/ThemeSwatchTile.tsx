import { Check } from 'lucide-react';
import { cn } from '@/lib/utils';
import type { ThemeFamily } from '@/lib/theme/types';

const THEME_LABELS: Record<ThemeFamily, string> = {
  'warm-neutral': 'Warm Neutral',
  'quiet-dark': 'Quiet Dark',
  'low-stimulation': 'Low Stimulation',
  'high-contrast': 'High Contrast',
  'soft-color': 'Soft Color',
  'compact-focus': 'Compact Focus',
};

const THEME_BLURBS: Record<ThemeFamily, string> = {
  'warm-neutral': 'Default. Calm warm beige.',
  'quiet-dark': 'Low-saturation dark.',
  'low-stimulation': 'Muted accents, gentle.',
  'high-contrast': 'High AA contrast.',
  'soft-color': 'Slightly more saturated.',
  'compact-focus': 'Tight, dense.',
};

interface ThemeSwatchTileProps {
  theme: ThemeFamily;
  selected: boolean;
  onSelect: (theme: ThemeFamily) => void;
}

/**
 * Mini preview of a theme. The wrapper sets `data-theme` so the CSS
 * variables resolve in isolation, regardless of the active document
 * theme. Renders three swatches: bg, surface-1, accent.
 */
export function ThemeSwatchTile({
  theme,
  selected,
  onSelect,
}: ThemeSwatchTileProps): JSX.Element {
  return (
    <button
      type="button"
      data-theme={theme}
      onClick={() => onSelect(theme)}
      aria-pressed={selected}
      aria-label={`Select theme: ${THEME_LABELS[theme]}`}
      className={cn(
        'group relative flex flex-col gap-1.5 rounded-[var(--r-2)]',
        'border bg-[var(--bg)] p-2 text-left transition-colors duration-[var(--motion-fast)]',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--bg)]',
        selected
          ? 'border-[var(--accent)] shadow-[0_0_0_1px_var(--accent)]'
          : 'border-[var(--border)] hover:border-[var(--border-strong)]',
      )}
      style={{ width: 96, height: 64 }}
    >
      <div className="flex h-3 w-full gap-1">
        <span
          className="h-full w-1/3 rounded-[var(--r-1)]"
          style={{ background: 'var(--surface-1)' }}
          aria-hidden
        />
        <span
          className="h-full w-1/3 rounded-[var(--r-1)]"
          style={{ background: 'var(--surface-2)' }}
          aria-hidden
        />
        <span
          className="h-full w-1/3 rounded-[var(--r-1)]"
          style={{ background: 'var(--accent)' }}
          aria-hidden
        />
      </div>
      <div className="flex-1">
        <p className="text-[var(--fs-2xs)] font-medium leading-tight text-[var(--fg)]">
          {THEME_LABELS[theme]}
        </p>
        <p className="text-[10px] leading-tight text-[var(--fg-muted)] line-clamp-1">
          {THEME_BLURBS[theme]}
        </p>
      </div>
      {selected && (
        <span
          aria-hidden
          className="absolute right-1.5 top-1.5 inline-flex h-3.5 w-3.5 items-center justify-center rounded-full bg-[var(--accent)] text-[var(--accent-fg)]"
        >
          <Check className="h-2.5 w-2.5" strokeWidth={2} />
        </span>
      )}
    </button>
  );
}
