import { useMemo } from 'react';
import type { DesktopSettings } from '@/lib/api/types';
import { THEME_FAMILIES, type ThemeFamily, type DensityMode, type MotionMode } from '@/lib/theme/types';
import { useTheme } from '@/lib/theme/provider';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { SectionHeader } from '../components/SectionHeader';
import { SwitchField } from '../components/SwitchField';
import { SelectField } from '../components/SelectField';
import { StickySaveBar } from '../components/StickySaveBar';
import { ThemeSwatchTile } from '../components/ThemeSwatchTile';
import { hasAnyChanges, isFieldChanged } from '../utils/diff';
import type { SettingsValidationIssue } from '@/lib/api/types';
import { groupIssuesByPath } from '../utils/validate';

const KEYS: (keyof DesktopSettings)[] = [
  'theme_family',
  'accent_color',
  'density',
  'motion',
  'support_mode_visuals',
  'command_bar_behavior',
];

const DENSITY_OPTIONS: ReadonlyArray<{ value: DensityMode; label: string; description: string }> = [
  { value: 'cozy', label: 'Cozy', description: 'Generous padding, easier to scan.' },
  { value: 'balanced', label: 'Balanced', description: 'Default. Good for most.' },
  { value: 'compact', label: 'Compact', description: 'Tighter rows, more on screen.' },
];

const MOTION_OPTIONS: ReadonlyArray<{ value: MotionMode; label: string; description: string }> = [
  { value: 'normal', label: 'Normal', description: 'Default transitions.' },
  { value: 'reduced', label: 'Reduced', description: 'Subdued; respects OS preference.' },
  { value: 'none', label: 'None', description: 'No animations or transitions.' },
];

const COMMAND_BAR_OPTIONS = [
  {
    value: 'screen-aware',
    label: 'Screen-aware',
    description: 'Surface actions tied to the current screen.',
  },
  {
    value: 'always-global',
    label: 'Always global',
    description: 'Show every command, every time.',
  },
  {
    value: 'minimal',
    label: 'Minimal',
    description: 'Hide unless explicitly opened.',
  },
] as const;

const ACCENT_OPTIONS = [
  { value: 'sage', label: 'Sage' },
  { value: 'terracotta', label: 'Terracotta' },
  { value: 'slate', label: 'Slate' },
  { value: 'plum', label: 'Plum' },
  { value: 'amber', label: 'Amber' },
] as const;

interface ThemeAndDisplaySectionProps {
  baseline: DesktopSettings;
  draft: DesktopSettings;
  onPatch: (patch: Partial<DesktopSettings>) => void;
  onSave: () => void;
  onDiscard: () => void;
  saving: boolean;
  saveError: string | null;
  issues: SettingsValidationIssue[];
  highlightFields?: ReadonlySet<string>;
}

/**
 * Tile-style switcher used by Density and Motion. Wraps three buttons in a
 * shared visual treatment so the controls feel like one widget.
 */
function TileSwitcher<V extends string>({
  label,
  value,
  options,
  onChange,
  changed,
  highlight,
}: {
  label: string;
  value: V;
  options: ReadonlyArray<{ value: V; label: string; description: string }>;
  onChange: (next: V) => void;
  changed: boolean;
  highlight?: boolean;
}): JSX.Element {
  return (
    <div
      className={cn(
        'flex flex-col gap-1.5 border-l-[3px] py-1 pl-3 -ml-3',
        changed ? 'border-l-[var(--accent)]' : 'border-l-transparent',
      )}
    >
      <p
        className={cn(
          'text-[var(--fs-xs)] uppercase tracking-[0.02em] text-[var(--fg-muted)]',
          highlight && 'rounded-[var(--r-1)] bg-[var(--accent-soft)] px-1 self-start',
        )}
      >
        {label}
      </p>
      <div role="radiogroup" aria-label={label} className="grid grid-cols-3 gap-2">
        {options.map((opt) => {
          const selected = opt.value === value;
          return (
            <button
              key={opt.value}
              type="button"
              role="radio"
              aria-checked={selected}
              onClick={() => onChange(opt.value)}
              className={cn(
                'flex h-full flex-col items-start gap-1 rounded-[var(--r-2)] border p-3 text-left',
                'transition-colors duration-[var(--motion-fast)] ease-[var(--ease-out)]',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--bg)]',
                selected
                  ? 'border-[var(--accent)] bg-[var(--accent-soft)] text-[var(--fg)] shadow-[0_0_0_1px_var(--accent)]'
                  : 'border-[var(--border)] bg-[var(--surface-1)] text-[var(--fg)] hover:border-[var(--border-strong)]',
              )}
            >
              <span className="text-[var(--fs-sm)] font-medium">{opt.label}</span>
              <span className="text-[var(--fs-xs)] text-[var(--fg-muted)]">
                {opt.description}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

export function ThemeAndDisplaySection({
  baseline,
  draft,
  onPatch,
  onSave,
  onDiscard,
  saving,
  saveError,
  issues,
  highlightFields,
}: ThemeAndDisplaySectionProps): JSX.Element {
  const { setTheme, setDensity, setMotion } = useTheme();
  const grouped = useMemo(() => groupIssuesByPath(issues), [issues]);
  const hl = (key: keyof DesktopSettings) =>
    highlightFields?.has(`theme.${key}`) ?? false;

  function applyAndPatch(patch: Partial<DesktopSettings>) {
    if (patch.theme_family) setTheme(patch.theme_family as ThemeFamily);
    if (patch.density) setDensity(patch.density as DensityMode);
    if (patch.motion) setMotion(patch.motion as MotionMode);
    onPatch(patch);
  }

  return (
    <section className="flex flex-col gap-6">
      <SectionHeader
        id="theme"
        eyebrow="01"
        title="Theme & Display"
        description="How Kora looks and feels. Changes apply immediately so you can settle in."
      />

      <div
        className={cn(
          'flex flex-col gap-1.5 border-l-[3px] py-1 pl-3 -ml-3',
          isFieldChanged(baseline, draft, 'theme_family')
            ? 'border-l-[var(--accent)]'
            : 'border-l-transparent',
        )}
      >
        <p
          className={cn(
            'text-[var(--fs-xs)] uppercase tracking-[0.02em] text-[var(--fg-muted)]',
            hl('theme_family') && 'rounded-[var(--r-1)] bg-[var(--accent-soft)] px-1 self-start',
          )}
        >
          Theme family
        </p>
        <div role="radiogroup" aria-label="Theme family" className="grid grid-cols-3 gap-2">
          {THEME_FAMILIES.map((theme) => (
            <ThemeSwatchTile
              key={theme}
              theme={theme}
              selected={draft.theme_family === theme}
              onSelect={(t) => applyAndPatch({ theme_family: t })}
            />
          ))}
        </div>
      </div>

      <SelectField
        label="Accent color"
        value={draft.accent_color}
        options={ACCENT_OPTIONS}
        onChange={(v) => onPatch({ accent_color: v })}
        changed={isFieldChanged(baseline, draft, 'accent_color')}
        highlight={hl('accent_color')}
        hint="Used for primary actions, focus rings, and provenance dots."
      />

      <TileSwitcher
        label="Density"
        value={draft.density as DensityMode}
        options={DENSITY_OPTIONS}
        onChange={(v) => applyAndPatch({ density: v })}
        changed={isFieldChanged(baseline, draft, 'density')}
        highlight={hl('density')}
      />

      <TileSwitcher
        label="Motion"
        value={draft.motion as MotionMode}
        options={MOTION_OPTIONS}
        onChange={(v) => applyAndPatch({ motion: v })}
        changed={isFieldChanged(baseline, draft, 'motion')}
        highlight={hl('motion')}
      />

      <SwitchField
        label="Support mode visuals"
        description="Show ADHD/autism support cues like reality-state badges and load bands."
        value={draft.support_mode_visuals}
        onChange={(v) => onPatch({ support_mode_visuals: v })}
        changed={isFieldChanged(baseline, draft, 'support_mode_visuals')}
        highlight={hl('support_mode_visuals')}
      />

      <SelectField
        label="Command bar behavior"
        value={draft.command_bar_behavior}
        options={COMMAND_BAR_OPTIONS}
        onChange={(v) => onPatch({ command_bar_behavior: v })}
        changed={isFieldChanged(baseline, draft, 'command_bar_behavior')}
        highlight={hl('command_bar_behavior')}
      />

      {grouped['*']?.[0] && (
        <p className="text-[var(--fs-xs)] text-[var(--danger)]">
          {grouped['*'][0].message}
        </p>
      )}

      <div className="flex justify-end">
        <Button
          variant="ghost"
          size="sm"
          onClick={onDiscard}
          disabled={!hasAnyChanges(baseline, draft, KEYS)}
          aria-label="Reset Theme & Display fields to saved values"
        >
          Reset section
        </Button>
      </div>

      <StickySaveBar
        visible={hasAnyChanges(baseline, draft, KEYS)}
        saving={saving}
        error={saveError}
        onSave={onSave}
        onDiscard={onDiscard}
      />
    </section>
  );
}
