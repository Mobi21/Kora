import {
  BatteryLow,
  CalendarOff,
  Hourglass,
  Minimize2,
  Move,
  X,
  type LucideIcon,
} from 'lucide-react';

export interface ChangeOption {
  id: string;
  label: string;
  icon: LucideIcon;
}

// Stable ids match the strings the daemon recognises in
// `RepairPreviewRequest.change_type`. `make_smaller` and
// `move_to_tomorrow` are first-class on the backend; the others fall
// through and produce a generic "make today smaller" preview which is
// still meaningful for the user.
export const CHANGE_TYPES: readonly ChangeOption[] = [
  { id: 'behind', label: "I'm behind", icon: Hourglass },
  { id: 'tired', label: 'Too tired', icon: BatteryLow },
  { id: 'event_changed', label: 'Event changed', icon: CalendarOff },
  { id: 'skipped', label: 'Skipped something', icon: X },
  { id: 'move_to_tomorrow', label: 'Need to move things', icon: Move },
  { id: 'make_smaller', label: 'Need a smaller version', icon: Minimize2 },
] as const;

export function getChangeOption(id: string | null | undefined): ChangeOption | null {
  if (!id) return null;
  return CHANGE_TYPES.find((c) => c.id === id) ?? null;
}

export { useRepairApply, useRepairPreview, useRepairState } from '@/lib/api/queries';
