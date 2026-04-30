import {
  Suspense,
  lazy,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { CalendarOff, RefreshCcw } from 'lucide-react';
import type {
  CalendarApi,
  DateSelectArg,
  DayCellContentArg,
  EventClickArg,
  EventContentArg,
  EventDropArg,
  EventInput,
  EventMountArg,
} from '@fullcalendar/core';
import type { EventResizeDoneArg } from '@fullcalendar/interaction';
import { keepPreviousData } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { EmptyState } from '@/components/ui/empty-state';
import { Skeleton } from '@/components/ui/skeleton';
import { cn } from '@/lib/utils';
import {
  useCalendar,
  useMedication,
} from '@/lib/api/queries';
import type {
  CalendarEditRequest,
  CalendarEventView,
} from '@/lib/api/types';
import { CalendarToolbar } from './components/CalendarToolbar';
import { LayerColumn } from './components/LayerColumn';
import { EventChip } from './components/EventChip';
import { MonthDayContent } from './components/MonthDayContent';
import { ShowMeWhyPanel } from './components/ShowMeWhyPanel';
import { EditPreviewDialog } from './components/EditPreviewDialog';
import { ShortcutsPopover } from './components/ShortcutsPopover';
import {
  buildMedicationBands,
  medicationDateForRange,
} from './components/MedicationAnchors';
import {
  FC_VIEW,
  computeVisibleRange,
  stepCursor,
  type CalendarView,
  type VisibleRange,
} from './utils/range';
import {
  applyPreset,
  eventVisible,
  readPersisted,
  readView,
  writePersisted,
  writeView,
  type LayerStateMap,
  type PresetId,
} from './utils/layers';

/* ─────────────────────────────────────────────────────────────────────────
   Lazy FullCalendar bundle. Plugins are loaded together so we keep one
   network round-trip and only one cost-of-entry skeleton frame.
   ────────────────────────────────────────────────────────────────────── */

interface FullCalendarShellProps {
  apiRef: (api: CalendarApi | null) => void;
  initialView: string;
  initialDate: Date;
  events: EventInput[];
  eventContent: (arg: EventContentArg) => ReactNode;
  dayCellContent?: (arg: DayCellContentArg) => ReactNode;
  eventDidMount?: (arg: EventMountArg) => void;
  onEventClick: (arg: EventClickArg) => void;
  onEventDrop: (arg: EventDropArg) => void;
  onEventResize: (arg: EventResizeDoneArg) => void;
  onSelect?: (arg: DateSelectArg) => void;
  editable: boolean;
}

const FullCalendarShell = lazy(async () => {
  const [react, daygrid, timegrid, list, interaction] = await Promise.all([
    import('@fullcalendar/react'),
    import('@fullcalendar/daygrid'),
    import('@fullcalendar/timegrid'),
    import('@fullcalendar/list'),
    import('@fullcalendar/interaction'),
  ]);
  const FC = react.default;
  function Bundle(props: FullCalendarShellProps): JSX.Element {
    const innerRef = useRef<{ getApi?: () => CalendarApi } | null>(null);
    useEffect(() => {
      const api = innerRef.current?.getApi?.() ?? null;
      props.apiRef(api);
      return () => props.apiRef(null);
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);
    return (
      <FC
        ref={innerRef as never}
        plugins={[
          daygrid.default,
          timegrid.default,
          list.default,
          interaction.default,
        ]}
        headerToolbar={false}
        initialView={props.initialView}
        initialDate={props.initialDate}
        events={props.events}
        editable={props.editable}
        selectable={false}
        weekends
        nowIndicator
        allDaySlot
        slotEventOverlap={false}
        eventOverlap
        height="100%"
        expandRows
        firstDay={0}
        slotDuration="00:30:00"
        slotLabelInterval="01:00"
        slotLabelFormat={{
          hour: 'numeric',
          minute: '2-digit',
          omitZeroMinute: true,
          meridiem: 'short',
        }}
        dayHeaderFormat={{
          weekday: 'short',
          day: 'numeric',
          omitCommas: true,
        }}
        scrollTime="08:00:00"
        eventContent={props.eventContent}
        dayCellContent={props.dayCellContent}
        eventDidMount={props.eventDidMount}
        eventClick={props.onEventClick}
        eventDrop={props.onEventDrop}
        eventResize={props.onEventResize}
        select={props.onSelect}
      />
    );
  }
  return { default: Bundle };
});

/* ─────────────────────────────────────────────────────────────────────────
   Skeleton calendar — chip-shaped blocks at random plausible times.
   ────────────────────────────────────────────────────────────────────── */

function SkeletonCalendar(): JSX.Element {
  const cols = 7;
  const rowsPerCol = [4, 5, 4, 6, 5, 4, 5];
  return (
    <div className="flex h-full flex-col">
      <div className="grid grid-cols-7 border-b border-[var(--border)]">
        {Array.from({ length: cols }).map((_, i) => (
          <div key={i} className="px-2 py-2">
            <Skeleton className="h-3 w-12" />
          </div>
        ))}
      </div>
      <div className="grid flex-1 grid-cols-7 divide-x divide-[var(--border)]">
        {rowsPerCol.map((count, ci) => (
          <div key={ci} className="relative">
            {Array.from({ length: count }).map((_, ri) => {
              const top = 40 + ri * (60 + ((ci + ri) % 3) * 20);
              const height = 28 + ((ri + ci) % 4) * 12;
              return (
                <Skeleton
                  key={ri}
                  className="absolute left-2 right-2"
                  style={{ top, height }}
                />
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
}

/* ─────────────────────────────────────────────────────────────────────────
   Screen
   ────────────────────────────────────────────────────────────────────── */

export function CalendarScreen(): JSX.Element {
  // ── Persistent UI state ──────────────────────────────────────────────
  const [view, setView] = useState<CalendarView>(() => readView() ?? 'week');
  const [cursor, setCursor] = useState<Date>(() => new Date());

  const persisted = useMemo(() => readPersisted(), []);
  const [preset, setPresetState] = useState<PresetId>(persisted.preset);
  const [customLayers, setCustomLayers] = useState<LayerStateMap>(
    persisted.custom,
  );

  const [selectedEventId, setSelectedEventId] = useState<string | null>(null);
  const [showWhyOpen, setShowWhyOpen] = useState(false);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const [pendingEdit, setPendingEdit] = useState<CalendarEditRequest | null>(
    null,
  );

  const apiRef = useRef<CalendarApi | null>(null);
  const screenRef = useRef<HTMLDivElement | null>(null);

  // ── Range + data ─────────────────────────────────────────────────────
  const range: VisibleRange = useMemo(
    () => computeVisibleRange(view, cursor, 0),
    [view, cursor],
  );

  const calendarQuery = useCalendar(range.start, range.end, view, {
    placeholderData: keepPreviousData,
  });
  const medicationDate = medicationDateForRange(range.start);
  const showMedication = view === 'day' || view === 'week';
  const medicationQuery = useMedication(medicationDate, {
    enabled: showMedication,
    placeholderData: keepPreviousData,
  });

  const layers = calendarQuery.data?.layers ?? null;

  // Resolve layer enabled-state from preset + custom overrides.
  const enabledLayers: LayerStateMap = useMemo(() => {
    if (!layers) return {};
    if (preset === 'custom') {
      const out: LayerStateMap = {};
      for (const l of layers) out[l.id] = customLayers[l.id] ?? l.enabled;
      return out;
    }
    return applyPreset(preset, layers, customLayers);
  }, [layers, preset, customLayers]);

  // Events filtered by enabled layers.
  const allEvents = useMemo(
    () => calendarQuery.data?.events ?? [],
    [calendarQuery.data?.events],
  );
  const visibleEvents = useMemo(() => {
    if (!layers) return allEvents;
    return allEvents.filter((e) => eventVisible(e, enabledLayers));
  }, [allEvents, layers, enabledLayers]);

  // Selected event lookup (so panel survives across refetches).
  const selectedEvent: CalendarEventView | null = useMemo(() => {
    if (!selectedEventId) return null;
    return allEvents.find((e) => e.id === selectedEventId) ?? null;
  }, [allEvents, selectedEventId]);

  // ── Convert to FullCalendar inputs ───────────────────────────────────
  const fcEvents: EventInput[] = useMemo(() => {
    if (view === 'month') {
      // We render dots ourselves via dayCellContent. Skip chip events.
      return [];
    }
    const evs: EventInput[] = visibleEvents.map((e) => ({
      id: e.id,
      title: e.title,
      start: e.starts_at,
      end: e.ends_at ?? undefined,
      allDay: e.all_day,
      extendedProps: { koraEvent: e },
    }));
    if (showMedication) {
      evs.push(...buildMedicationBands(medicationQuery.data));
    }
    return evs;
  }, [view, visibleEvents, showMedication, medicationQuery.data]);

  // ── Render-prop callbacks ────────────────────────────────────────────
  const eventContent = useCallback(
    (arg: EventContentArg): ReactNode => {
      // Background events (medication bands) — let CSS handle visuals.
      if (arg.event.display === 'background') return null;
      const ev = arg.event.extendedProps.koraEvent as
        | CalendarEventView
        | undefined;
      if (!ev) return null;
      const compact = view === 'agenda';
      return (
        <EventChip
          event={ev}
          compact={compact}
          onView={(id) => {
            setSelectedEventId(id);
            setShowWhyOpen(true);
          }}
          onMove={(id) => {
            setPendingEdit({
              operation: 'move',
              event_id: id,
              starts_at: ev.starts_at,
              ends_at: ev.ends_at ?? null,
            });
          }}
          onCancel={(id) => {
            setPendingEdit({
              operation: 'cancel',
              event_id: id,
              starts_at: ev.starts_at,
              ends_at: ev.ends_at ?? null,
            });
          }}
        />
      );
    },
    [view],
  );

  const dayCellContent = useCallback(
    (arg: DayCellContentArg): ReactNode => {
      if (view !== 'month') return undefined;
      return (
        <MonthDayContent
          date={arg.date}
          dayNumberText={arg.dayNumberText}
          isOther={arg.isOther}
          isToday={arg.isToday}
          events={visibleEvents}
          onSelect={(id) => {
            setSelectedEventId(id);
            setShowWhyOpen(true);
          }}
        />
      );
    },
    [view, visibleEvents],
  );

  const onEventClick = useCallback((arg: EventClickArg) => {
    const ev = arg.event.extendedProps.koraEvent as CalendarEventView | undefined;
    if (!ev) return;
    setSelectedEventId(ev.id);
    setShowWhyOpen(true);
  }, []);

  const onEventDrop = useCallback((arg: EventDropArg) => {
    const ev = arg.event.extendedProps.koraEvent as CalendarEventView | undefined;
    arg.revert(); // never mutate without explicit preview/apply
    if (!ev) return;
    setPendingEdit({
      operation: 'move',
      event_id: ev.id,
      starts_at: arg.event.start?.toISOString() ?? ev.starts_at,
      ends_at:
        arg.event.end?.toISOString() ?? ev.ends_at ?? null,
    });
  }, []);

  const onEventResize = useCallback((arg: EventResizeDoneArg) => {
    const ev = arg.event.extendedProps.koraEvent as CalendarEventView | undefined;
    arg.revert();
    if (!ev) return;
    setPendingEdit({
      operation: 'resize',
      event_id: ev.id,
      starts_at: arg.event.start?.toISOString() ?? ev.starts_at,
      ends_at: arg.event.end?.toISOString() ?? ev.ends_at ?? null,
    });
  }, []);

  // ── Toolbar handlers ─────────────────────────────────────────────────
  const handlePrev = useCallback(() => {
    const next = stepCursor(view, cursor, -1);
    setCursor(next);
    apiRef.current?.gotoDate(next);
  }, [view, cursor]);

  const handleNext = useCallback(() => {
    const next = stepCursor(view, cursor, 1);
    setCursor(next);
    apiRef.current?.gotoDate(next);
  }, [view, cursor]);

  const handleToday = useCallback(() => {
    const today = new Date();
    setCursor(today);
    apiRef.current?.today();
  }, []);

  const handleViewChange = useCallback((v: CalendarView) => {
    setView(v);
    writeView(v);
    apiRef.current?.changeView(FC_VIEW[v]);
  }, []);

  // ── Layer toggle handlers ────────────────────────────────────────────
  const handlePresetChange = useCallback(
    (p: PresetId) => {
      setPresetState(p);
      // If switching to a non-custom preset, the resolved enabledLayers
      // recompute via useMemo. Persist immediately.
      writePersisted(p, customLayers);
    },
    [customLayers],
  );

  const handleLayerToggle = useCallback(
    (id: string, enabled: boolean) => {
      // Switch to "custom" preset and remember the new state.
      const newCustom: LayerStateMap = { ...customLayers };
      // Capture current resolved state as the basis for custom.
      if (layers) {
        for (const l of layers) {
          newCustom[l.id] = enabledLayers[l.id] ?? l.enabled;
        }
      }
      newCustom[id] = enabled;
      setCustomLayers(newCustom);
      setPresetState('custom');
      writePersisted('custom', newCustom);
    },
    [customLayers, enabledLayers, layers],
  );

  // ── Keyboard shortcuts ───────────────────────────────────────────────
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const target = e.target as HTMLElement | null;
      if (!target) return;
      // Ignore typing surfaces.
      const tag = target.tagName;
      if (
        tag === 'INPUT' ||
        tag === 'TEXTAREA' ||
        tag === 'SELECT' ||
        target.isContentEditable
      ) {
        return;
      }
      // Stay scoped to the calendar screen if it's mounted.
      if (
        e.metaKey ||
        e.ctrlKey ||
        e.altKey ||
        (screenRef.current && !screenRef.current.contains(target))
      ) {
        return;
      }
      const k = e.key.toLowerCase();
      if (k === 'd') handleViewChange('day');
      else if (k === 'w') handleViewChange('week');
      else if (k === 'm') handleViewChange('month');
      else if (k === 'a') handleViewChange('agenda');
      else if (k === 't') handleToday();
      else if (e.key === 'ArrowLeft') handlePrev();
      else if (e.key === 'ArrowRight') handleNext();
      else if (e.key === '?') setShortcutsOpen((v) => !v);
      else return;
      e.preventDefault();
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [handleViewChange, handleToday, handlePrev, handleNext]);

  // ── Empty / error / loaded surface ───────────────────────────────────
  const isError = calendarQuery.isError && !calendarQuery.data;
  const isInitialLoad = calendarQuery.isLoading && !calendarQuery.data;
  const isEmpty =
    !!calendarQuery.data && visibleEvents.length === 0;

  return (
    <div
      ref={screenRef}
      className="kora-calendar relative flex h-full w-full flex-col bg-[var(--bg)]"
      data-density-scope="calendar"
    >
      <CalendarToolbar
        view={view}
        range={range}
        onPrev={handlePrev}
        onNext={handleNext}
        onToday={handleToday}
        onViewChange={handleViewChange}
        onShowMeWhy={() => setShowWhyOpen((v) => !v)}
        showMeWhyActive={showWhyOpen}
      />

      <div className="flex min-h-0 flex-1">
        <LayerColumn
          layers={layers}
          loading={isInitialLoad}
          preset={preset}
          enabled={enabledLayers}
          onPresetChange={handlePresetChange}
          onLayerToggle={handleLayerToggle}
        />

        <div className="relative flex min-w-0 flex-1 flex-col">
          {isEmpty && (
            <div
              className={cn(
                'pointer-events-none absolute inset-x-0 top-2 z-10 flex justify-center',
              )}
            >
              <p
                className={cn(
                  'rounded-[var(--r-pill)] border border-[var(--border)] bg-[var(--surface-1)]',
                  'px-3 py-1 font-narrative text-[var(--fs-sm)] text-[var(--fg-muted)]',
                  'shadow-[var(--shadow-1)]',
                )}
              >
                Nothing scheduled in this range. Today is yours.
              </p>
            </div>
          )}

          {isError ? (
            <div className="flex h-full items-center justify-center">
              <EmptyState
                icon={CalendarOff}
                title="Calendar didn't load."
                description={
                  calendarQuery.error?.message ??
                  'The daemon may be offline or still warming up.'
                }
                action={
                  <Button
                    onClick={() => calendarQuery.refetch()}
                    aria-label="Retry loading calendar"
                  >
                    <RefreshCcw className="h-3.5 w-3.5" strokeWidth={1.5} />
                    Try again
                  </Button>
                }
              />
            </div>
          ) : isInitialLoad ? (
            <SkeletonCalendar />
          ) : (
            <Suspense fallback={<SkeletonCalendar />}>
              <FullCalendarShell
                apiRef={(api) => {
                  apiRef.current = api;
                }}
                initialView={FC_VIEW[view]}
                initialDate={cursor}
                events={fcEvents}
                eventContent={eventContent}
                dayCellContent={view === 'month' ? dayCellContent : undefined}
                onEventClick={onEventClick}
                onEventDrop={onEventDrop}
                onEventResize={onEventResize}
                editable={view !== 'month' && view !== 'agenda'}
              />
            </Suspense>
          )}

          {/* Floating shortcut help, bottom-right. */}
          <div className="absolute bottom-3 right-3">
            <ShortcutsPopover
              open={shortcutsOpen}
              onOpenChange={setShortcutsOpen}
            />
          </div>
        </div>

        <ShowMeWhyPanel
          open={showWhyOpen}
          event={selectedEvent}
          generatedAt={calendarQuery.data?.generated_at ?? null}
          onClose={() => setShowWhyOpen(false)}
        />
      </div>

      <EditPreviewDialog
        open={!!pendingEdit}
        request={pendingEdit}
        onClose={() => setPendingEdit(null)}
        onApplied={() => {
          calendarQuery.refetch();
        }}
      />
    </div>
  );
}

export default CalendarScreen;
