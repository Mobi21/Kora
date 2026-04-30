import type { EventInput } from '@fullcalendar/core';
import type { MedicationDayView } from '@/lib/api/types';

/**
 * Convert a MedicationDayView into a list of FullCalendar background events
 * representing the dose windows. These are rendered as 2px sage horizontal
 * bands on the time grid via the `kora-medication-band` CSS class.
 *
 * Returns [] if no medication data, no doses, or no windows.
 */
export function buildMedicationBands(med: MedicationDayView | null | undefined): EventInput[] {
  if (!med?.enabled || !med.doses?.length) return [];
  const out: EventInput[] = [];
  for (const dose of med.doses) {
    if (!dose.window_start || !dose.window_end) continue;
    out.push({
      id: `med-band-${dose.id}`,
      start: dose.window_start,
      end: dose.window_end,
      display: 'background',
      classNames: ['kora-medication-band'],
      extendedProps: {
        koraMedicationDose: dose,
      },
    });
  }
  return out;
}

/**
 * Helper to get medication ISO date strings overlapping the visible range.
 * For now we fetch only the start date because the daemon's medication
 * endpoint is single-date scoped; week/month rendering will fetch start day.
 */
export function medicationDateForRange(startISO: string): string {
  const d = new Date(startISO);
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}
