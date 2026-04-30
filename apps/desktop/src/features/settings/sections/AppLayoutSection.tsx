import { ArrowDown, ArrowUp } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import type { DesktopSettings } from '@/lib/api/types';
import { cn } from '@/lib/utils';
import { SectionHeader } from '../components/SectionHeader';
import { SwitchField } from '../components/SwitchField';
import { SelectField } from '../components/SelectField';
import { NumberField } from '../components/NumberField';
import { StickySaveBar } from '../components/StickySaveBar';
import { hasAnyChanges, isFieldChanged } from '../utils/diff';
import type { SettingsValidationIssue } from '@/lib/api/types';
import { groupIssuesByPath, firstErrorMessage } from '../utils/validate';

const KEYS: (keyof DesktopSettings)[] = [
  'chat_panel_default_open',
  'chat_panel_width',
  'today_module_order',
  'calendar_default_view',
  'timeline_position',
];

const CALENDAR_VIEWS = [
  { value: 'day', label: 'Day' },
  { value: 'week', label: 'Week' },
  { value: 'month', label: 'Month' },
  { value: 'agenda', label: 'Agenda' },
] as const;

const TIMELINE_POSITION = [
  { value: 'left', label: 'Left rail' },
  { value: 'right', label: 'Right rail' },
] as const;

const MODULE_LABELS: Record<string, string> = {
  now: 'Now',
  next: 'Up next',
  later: 'Later',
  timeline: 'Timeline',
  reality_check: 'Reality check',
  load: 'Load indicator',
};

interface AppLayoutSectionProps {
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

export function AppLayoutSection({
  baseline,
  draft,
  onPatch,
  onSave,
  onDiscard,
  saving,
  saveError,
  issues,
  highlightFields,
}: AppLayoutSectionProps): JSX.Element {
  const grouped = groupIssuesByPath(issues);
  const hl = (key: keyof DesktopSettings) =>
    highlightFields?.has(`layout.${key}`) ?? false;

  function moveModule(idx: number, delta: -1 | 1): void {
    const next = [...draft.today_module_order];
    const target = idx + delta;
    if (target < 0 || target >= next.length) return;
    const [item] = next.splice(idx, 1);
    next.splice(target, 0, item);
    onPatch({ today_module_order: next });
  }

  return (
    <section className="flex flex-col gap-6">
      <SectionHeader
        id="layout"
        eyebrow="02"
        title="App layout"
        description="Where the chat panel sits, what shows on Today, and how big the rails are."
      />

      <SwitchField
        label="Chat panel default open"
        description="Decide whether the side chat is open when you start a session."
        value={draft.chat_panel_default_open}
        onChange={(v) => onPatch({ chat_panel_default_open: v })}
        changed={isFieldChanged(baseline, draft, 'chat_panel_default_open')}
        highlight={hl('chat_panel_default_open')}
      />

      <NumberField
        label="Chat panel width"
        value={draft.chat_panel_width}
        onChange={(v) => onPatch({ chat_panel_width: v })}
        step={20}
        min={280}
        max={640}
        unit="px"
        hint="Recommended 320–520."
        changed={isFieldChanged(baseline, draft, 'chat_panel_width')}
        error={firstErrorMessage(grouped['chat_panel_width'])}
        highlight={hl('chat_panel_width')}
      />

      <div
        className={cn(
          'flex flex-col gap-1.5 border-l-[3px] py-1 pl-3 -ml-3',
          isFieldChanged(baseline, draft, 'today_module_order')
            ? 'border-l-[var(--accent)]'
            : 'border-l-transparent',
        )}
      >
        <p
          className={cn(
            'text-[var(--fs-xs)] uppercase tracking-[0.02em] text-[var(--fg-muted)]',
            hl('today_module_order') &&
              'self-start rounded-[var(--r-1)] bg-[var(--accent-soft)] px-1',
          )}
        >
          Today module order
        </p>
        <p className="text-[var(--fs-xs)] text-[var(--fg-muted)]">
          The order modules appear on the Today screen, top to bottom.
        </p>
        <ul className="flex flex-col gap-1.5" aria-label="Today module order">
          {draft.today_module_order.map((mod, idx) => (
            <li
              key={mod}
              className="flex items-center gap-2 rounded-[var(--r-2)] border border-[var(--border)] bg-[var(--surface-1)] px-2 py-1.5"
            >
              <span className="font-mono text-[var(--fs-2xs)] text-[var(--fg-subtle)] num-tabular w-4 text-right">
                {idx + 1}
              </span>
              <Badge provenance="local">{MODULE_LABELS[mod] ?? mod}</Badge>
              <span className="ml-auto flex items-center gap-1">
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-7 w-7"
                  onClick={() => moveModule(idx, -1)}
                  disabled={idx === 0}
                  aria-label={`Move ${MODULE_LABELS[mod] ?? mod} up`}
                >
                  <ArrowUp className="h-3.5 w-3.5" strokeWidth={1.5} />
                </Button>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-7 w-7"
                  onClick={() => moveModule(idx, 1)}
                  disabled={idx === draft.today_module_order.length - 1}
                  aria-label={`Move ${MODULE_LABELS[mod] ?? mod} down`}
                >
                  <ArrowDown className="h-3.5 w-3.5" strokeWidth={1.5} />
                </Button>
              </span>
            </li>
          ))}
        </ul>
      </div>

      <SelectField
        label="Calendar default view"
        value={draft.calendar_default_view}
        options={CALENDAR_VIEWS}
        onChange={(v) => onPatch({ calendar_default_view: v })}
        changed={isFieldChanged(baseline, draft, 'calendar_default_view')}
        highlight={hl('calendar_default_view')}
      />

      <SelectField
        label="Timeline position"
        value={draft.timeline_position}
        options={TIMELINE_POSITION}
        onChange={(v) => onPatch({ timeline_position: v })}
        changed={isFieldChanged(baseline, draft, 'timeline_position')}
        highlight={hl('timeline_position')}
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
          aria-label="Reset App layout fields"
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
