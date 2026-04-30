export type CalendarView = 'day' | 'week' | 'month' | 'agenda';

export const FC_VIEW: Record<CalendarView, string> = {
  day: 'timeGridDay',
  week: 'timeGridWeek',
  month: 'dayGridMonth',
  agenda: 'listWeek',
};

export const VIEW_FROM_FC: Record<string, CalendarView> = {
  timeGridDay: 'day',
  timeGridWeek: 'week',
  dayGridMonth: 'month',
  listWeek: 'agenda',
};

const DAY_MS = 24 * 60 * 60 * 1000;

function startOfDay(d: Date): Date {
  const r = new Date(d);
  r.setHours(0, 0, 0, 0);
  return r;
}

function startOfWeek(d: Date, firstDay: number = 0): Date {
  const r = startOfDay(d);
  const diff = (r.getDay() - firstDay + 7) % 7;
  r.setDate(r.getDate() - diff);
  return r;
}

function startOfMonth(d: Date): Date {
  const r = startOfDay(d);
  r.setDate(1);
  return r;
}

function endOfMonth(d: Date): Date {
  const r = startOfMonth(d);
  r.setMonth(r.getMonth() + 1);
  return r;
}

function addDays(d: Date, days: number): Date {
  return new Date(d.getTime() + days * DAY_MS);
}

export interface VisibleRange {
  start: string;
  end: string;
  startDate: Date;
  endDate: Date;
}

/**
 * Compute visible ISO range for the daemon's `/desktop/calendar` endpoint.
 * `firstDay` matches FullCalendar's firstDay option (default Sunday=0).
 */
export function computeVisibleRange(
  view: CalendarView,
  cursor: Date,
  firstDay: number = 0,
): VisibleRange {
  let startDate: Date;
  let endDate: Date;
  switch (view) {
    case 'day': {
      startDate = startOfDay(cursor);
      endDate = addDays(startDate, 1);
      break;
    }
    case 'week':
    case 'agenda': {
      startDate = startOfWeek(cursor, firstDay);
      endDate = addDays(startDate, 7);
      break;
    }
    case 'month': {
      const monthStart = startOfMonth(cursor);
      const monthEnd = endOfMonth(cursor);
      startDate = startOfWeek(monthStart, firstDay);
      const tail = startOfWeek(addDays(monthEnd, -1), firstDay);
      endDate = addDays(tail, 7);
      break;
    }
    default: {
      startDate = startOfDay(cursor);
      endDate = addDays(startDate, 1);
    }
  }
  return {
    start: startDate.toISOString(),
    end: endDate.toISOString(),
    startDate,
    endDate,
  };
}

/** Step the cursor forward/back by one period of the current view. */
export function stepCursor(view: CalendarView, cursor: Date, dir: 1 | -1): Date {
  const r = new Date(cursor);
  switch (view) {
    case 'day':
      r.setDate(r.getDate() + dir);
      break;
    case 'week':
    case 'agenda':
      r.setDate(r.getDate() + 7 * dir);
      break;
    case 'month':
      r.setMonth(r.getMonth() + dir);
      break;
  }
  return r;
}

/** Format the active range as "Apr 29 – May 5" (or single day for 'day'). */
export function formatRangeLabel(view: CalendarView, range: VisibleRange): string {
  const sameMonth =
    range.startDate.getMonth() === addDays(range.endDate, -1).getMonth() &&
    range.startDate.getFullYear() === addDays(range.endDate, -1).getFullYear();
  const startFmt = new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
  });
  const endFmt = new Intl.DateTimeFormat(undefined, {
    month: sameMonth ? undefined : 'short',
    day: 'numeric',
  });
  const yearFmt = new Intl.DateTimeFormat(undefined, { year: 'numeric' });

  if (view === 'day') {
    const dayFmt = new Intl.DateTimeFormat(undefined, {
      weekday: 'long',
      month: 'short',
      day: 'numeric',
    });
    return `${dayFmt.format(range.startDate)}, ${yearFmt.format(range.startDate)}`;
  }
  if (view === 'month') {
    const m = new Intl.DateTimeFormat(undefined, {
      month: 'long',
      year: 'numeric',
    });
    const mid = new Date(range.startDate.getTime() + 14 * DAY_MS);
    return m.format(mid);
  }
  const lastDay = addDays(range.endDate, -1);
  const yearSuffix =
    lastDay.getFullYear() !== new Date().getFullYear()
      ? `, ${yearFmt.format(lastDay)}`
      : '';
  return `${startFmt.format(range.startDate)} – ${endFmt.format(lastDay)}${yearSuffix}`;
}

export function isoDateLocal(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}
