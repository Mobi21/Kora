import type { DesktopSettings } from '@/lib/api/types';

/**
 * Compute a shallow diff between a baseline and a draft of `DesktopSettings`.
 *
 * Returns only the keys whose values differ. For nested record/array fields
 * (`calendar_layers`, `today_module_order`) we deep-compare so unchanged
 * collections don't ship in the patch payload.
 */
export function diffSettings(
  baseline: DesktopSettings,
  draft: DesktopSettings,
): Partial<DesktopSettings> {
  const patch: Partial<DesktopSettings> = {};
  const keys = Object.keys(draft) as (keyof DesktopSettings)[];
  for (const key of keys) {
    if (!isEqual(baseline[key], draft[key])) {
      // narrow widening: copy through as the same key.
      (patch as Record<string, unknown>)[key as string] = draft[key];
    }
  }
  return patch;
}

/**
 * Whether a single key on the draft differs from its baseline value.
 * Used by field components to render the "changed" left-rule indicator.
 */
export function isFieldChanged<K extends keyof DesktopSettings>(
  baseline: DesktopSettings | null | undefined,
  draft: DesktopSettings | null | undefined,
  key: K,
): boolean {
  if (!baseline || !draft) return false;
  return !isEqual(baseline[key], draft[key]);
}

export function hasAnyChanges(
  baseline: DesktopSettings | null | undefined,
  draft: DesktopSettings | null | undefined,
  keys: (keyof DesktopSettings)[],
): boolean {
  if (!baseline || !draft) return false;
  return keys.some((k) => !isEqual(baseline[k], draft[k]));
}

function isEqual(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (a == null || b == null) return false;
  if (typeof a !== 'object' || typeof b !== 'object') return false;
  if (Array.isArray(a) || Array.isArray(b)) {
    if (!Array.isArray(a) || !Array.isArray(b)) return false;
    if (a.length !== b.length) return false;
    return a.every((v, i) => isEqual(v, b[i]));
  }
  const ar = a as Record<string, unknown>;
  const br = b as Record<string, unknown>;
  const aKeys = Object.keys(ar);
  const bKeys = Object.keys(br);
  if (aKeys.length !== bKeys.length) return false;
  return aKeys.every((k) => isEqual(ar[k], br[k]));
}
