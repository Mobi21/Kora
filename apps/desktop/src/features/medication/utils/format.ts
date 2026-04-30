import type { MedicationDose } from '@/lib/api/types';
import type { PillStatus } from '@/components/ui/pill';
import { isoDate } from '@/lib/dates';

export function shiftIsoDate(iso: string, days: number): string {
  const [y, m, d] = iso.split('-').map(Number);
  const dt = new Date(Date.UTC(y, (m ?? 1) - 1, d ?? 1));
  dt.setUTCDate(dt.getUTCDate() + days);
  return dt.toISOString().slice(0, 10);
}

export function todayIso(): string {
  return isoDate(new Date());
}

export function dayOffsetFromToday(iso: string): number {
  const a = new Date(`${todayIso()}T00:00:00Z`).getTime();
  const b = new Date(`${iso}T00:00:00Z`).getTime();
  return Math.round((b - a) / 86_400_000);
}

function pad2(n: number): string {
  return n.toString().padStart(2, '0');
}

function timeOnly(iso: string): string {
  const d = new Date(iso);
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
}

export function doseTimeLabel(dose: MedicationDose): string {
  if (dose.window_start && dose.window_end) {
    return `${timeOnly(dose.window_start)}–${timeOnly(dose.window_end)}`;
  }
  if (dose.scheduled_at) return timeOnly(dose.scheduled_at);
  return '—:—';
}

export function statusToPillStatus(s: MedicationDose['status']): PillStatus {
  switch (s) {
    case 'taken':
      return 'ok';
    case 'pending':
      return 'unknown';
    case 'skipped':
      return 'warn';
    case 'missed':
      return 'degraded';
    case 'unknown':
      return 'unknown';
  }
}

export function statusLabel(s: MedicationDose['status']): string {
  switch (s) {
    case 'taken':
      return 'Taken';
    case 'pending':
      return 'Pending';
    case 'skipped':
      return 'Skipped';
    case 'missed':
      return 'Missed';
    case 'unknown':
      return 'Unknown';
  }
}

export function summarizeDay(doses: MedicationDose[]): {
  taken: number;
  skipped: number;
  pending: number;
  missed: number;
} {
  let taken = 0;
  let skipped = 0;
  let pending = 0;
  let missed = 0;
  for (const d of doses) {
    if (d.status === 'taken') taken++;
    else if (d.status === 'skipped') skipped++;
    else if (d.status === 'missed') missed++;
    else pending++;
  }
  return { taken, skipped, pending, missed };
}

export function formatTimeOnly(iso: string): string {
  return timeOnly(iso);
}

export function formatDayLabel(iso: string): string {
  const offset = dayOffsetFromToday(iso);
  if (offset === 0) return 'Today';
  if (offset === -1) return 'Yesterday';
  if (offset === 1) return 'Tomorrow';
  const d = new Date(`${iso}T00:00:00`);
  return new Intl.DateTimeFormat(undefined, {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
  }).format(d);
}
