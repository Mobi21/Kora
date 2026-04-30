import type { CalendarEventView, CalendarLayerState } from '@/lib/api/types';

export type PresetId = 'calm' | 'planning' | 'full' | 'low_stim' | 'custom';

export interface PresetDef {
  id: PresetId;
  label: string;
  description: string;
  /** Returns true if the layer should be enabled under this preset. */
  applies: (layerId: string) => boolean;
}

const KEY_BASE = 'kora.calendar.layers';

function ids(...patterns: string[]): (id: string) => boolean {
  return (id) => patterns.some((p) => id.toLowerCase() === p || id.toLowerCase().includes(p));
}

export const PRESETS: PresetDef[] = [
  {
    id: 'calm',
    label: 'Calm',
    description: 'Events and repair only.',
    applies: ids('event', 'repair'),
  },
  {
    id: 'planning',
    label: 'Planning',
    description: 'Everything except provenance and load overlays.',
    applies: (id) => !ids('provenance', 'load')(id),
  },
  {
    id: 'full',
    label: 'Full Context',
    description: 'Every layer, including provenance and load overlay.',
    applies: () => true,
  },
  {
    id: 'low_stim',
    label: 'Low Stimulation',
    description: 'Events and medication only.',
    applies: ids('event', 'medic'),
  },
  {
    id: 'custom',
    label: 'Custom',
    description: 'Your last manual selection.',
    applies: () => true,
  },
];

export interface LayerStateMap {
  [layerId: string]: boolean;
}

export function applyPreset(
  presetId: PresetId,
  layers: CalendarLayerState[],
  custom: LayerStateMap,
): LayerStateMap {
  if (presetId === 'custom') {
    const out: LayerStateMap = {};
    for (const l of layers) out[l.id] = custom[l.id] ?? l.enabled;
    return out;
  }
  const def = PRESETS.find((p) => p.id === presetId);
  if (!def) {
    const out: LayerStateMap = {};
    for (const l of layers) out[l.id] = l.enabled;
    return out;
  }
  const out: LayerStateMap = {};
  for (const l of layers) out[l.id] = def.applies(l.id);
  return out;
}

export function readPersisted(): {
  preset: PresetId;
  custom: LayerStateMap;
} {
  if (typeof window === 'undefined') return { preset: 'planning', custom: {} };
  try {
    const raw = window.localStorage.getItem(KEY_BASE);
    if (!raw) return { preset: 'planning', custom: {} };
    const parsed = JSON.parse(raw) as { preset?: PresetId; custom?: LayerStateMap };
    return {
      preset: parsed.preset ?? 'planning',
      custom: parsed.custom ?? {},
    };
  } catch {
    return { preset: 'planning', custom: {} };
  }
}

export function writePersisted(preset: PresetId, custom: LayerStateMap): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(
      KEY_BASE,
      JSON.stringify({ preset, custom }),
    );
  } catch {
    /* swallow storage quota / privacy errors */
  }
}

/** True when the event has any of the enabled layer ids attached. */
export function eventVisible(
  ev: CalendarEventView,
  enabled: LayerStateMap,
): boolean {
  if (!ev.layer_ids || ev.layer_ids.length === 0) {
    // No layer tags → treat as base "events" layer.
    const eventLayerEnabled = Object.entries(enabled).find(([id]) =>
      id.toLowerCase().includes('event'),
    );
    return eventLayerEnabled ? eventLayerEnabled[1] : true;
  }
  return ev.layer_ids.some((id) => enabled[id]);
}

const VIEW_KEY = 'kora.calendar.view';

export function readView(): 'day' | 'week' | 'month' | 'agenda' | null {
  if (typeof window === 'undefined') return null;
  try {
    const v = window.localStorage.getItem(VIEW_KEY);
    if (v === 'day' || v === 'week' || v === 'month' || v === 'agenda') return v;
    return null;
  } catch {
    return null;
  }
}

export function writeView(v: 'day' | 'week' | 'month' | 'agenda'): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(VIEW_KEY, v);
  } catch {
    /* swallow */
  }
}
