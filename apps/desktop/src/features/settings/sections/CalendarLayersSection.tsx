import { useMemo } from 'react';
import { Layers } from 'lucide-react';
import { Button } from '@/components/ui/button';
import type { DesktopSettings, SettingsValidationIssue } from '@/lib/api/types';
import { SectionHeader } from '../components/SectionHeader';
import { SwitchField } from '../components/SwitchField';
import { StickySaveBar } from '../components/StickySaveBar';
import { groupIssuesByPath } from '../utils/validate';

interface CalendarLayersSectionProps {
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

const FRIENDLY_LAYER_LABELS: Record<string, string> = {
  google: 'Google Calendar',
  meds: 'Medication blocks',
  routines: 'Routines',
  reminders: 'Reminders',
  protected: 'Protected commitments',
  buffer: 'Buffer windows',
  energy: 'Energy bands',
};

function describeLayer(id: string): string {
  return (
    FRIENDLY_LAYER_LABELS[id] ??
    id.replace(/[_-]/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
  );
}

function layersEqual(a: Record<string, boolean>, b: Record<string, boolean>): boolean {
  const ak = Object.keys(a);
  const bk = Object.keys(b);
  if (ak.length !== bk.length) return false;
  return ak.every((k) => a[k] === b[k]);
}

export function CalendarLayersSection({
  baseline,
  draft,
  onPatch,
  onSave,
  onDiscard,
  saving,
  saveError,
  issues,
  highlightFields,
}: CalendarLayersSectionProps): JSX.Element {
  const grouped = groupIssuesByPath(issues);

  const layers = useMemo(
    () => draft.calendar_layers ?? {},
    [draft.calendar_layers],
  );
  const ids = useMemo(() => Object.keys(layers).sort(), [layers]);
  const dirty = !layersEqual(baseline.calendar_layers ?? {}, layers);

  function setLayer(id: string, on: boolean): void {
    onPatch({
      calendar_layers: { ...layers, [id]: on },
    });
  }

  return (
    <section className="flex flex-col gap-6">
      <SectionHeader
        id="calendar-layers"
        eyebrow="03"
        title="Calendar layers"
        description="Toggle which sources show on the Calendar. Hidden layers still load in the background."
      />

      {ids.length === 0 ? (
        <div className="flex items-center gap-3 rounded-[var(--r-2)] border border-[var(--border)] bg-[var(--surface-1)] px-3 py-4 text-[var(--fs-sm)] text-[var(--fg-muted)]">
          <Layers className="h-4 w-4 text-[var(--fg-subtle)]" strokeWidth={1.5} />
          No layers reported by the daemon yet. Connect a calendar to populate this list.
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          {ids.map((id) => {
            const enabled = !!layers[id];
            const baseEnabled = !!(baseline.calendar_layers ?? {})[id];
            return (
              <SwitchField
                key={id}
                label={describeLayer(id)}
                description={id !== describeLayer(id) ? id : undefined}
                value={enabled}
                onChange={(v) => setLayer(id, v)}
                changed={enabled !== baseEnabled}
                highlight={highlightFields?.has(`calendar_layers.${id}`)}
              />
            );
          })}
        </div>
      )}

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
          disabled={!dirty}
          aria-label="Reset calendar layers"
        >
          Reset section
        </Button>
      </div>

      <StickySaveBar
        visible={dirty}
        saving={saving}
        error={saveError}
        onSave={onSave}
        onDiscard={onDiscard}
      />
    </section>
  );
}
