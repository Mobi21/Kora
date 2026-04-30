import type { ReactNode } from 'react';
import { ExternalLink, Sparkles, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { ProvenanceDot } from '@/components/ui/provenance-dot';
import { cn } from '@/lib/utils';
import { formatTime } from '@/lib/dates';
import type { CalendarEventView } from '@/lib/api/types';
import { classifyProvenance, pickProvenance } from '../utils/provenance';

export interface ShowMeWhyPanelProps {
  open: boolean;
  event: CalendarEventView | null;
  generatedAt: string | null;
  onClose: () => void;
}

export function ShowMeWhyPanel({
  open,
  event,
  generatedAt,
  onClose,
}: ShowMeWhyPanelProps): JSX.Element | null {
  if (!open) return null;
  return (
    <aside
      role="complementary"
      aria-label="Event provenance"
      className={cn(
        'flex h-full w-[360px] shrink-0 flex-col border-l border-[var(--border)]',
        'bg-[var(--surface-2)]',
      )}
    >
      <header
        className={cn(
          'flex items-center justify-between gap-2 border-b border-[var(--border)]',
          'px-4 py-3',
        )}
      >
        <div className="flex items-center gap-2 text-[var(--fg-muted)]">
          <Sparkles className="h-3.5 w-3.5" strokeWidth={1.5} />
          <span className="text-[var(--fs-2xs)] uppercase tracking-[0.02em]">
            Show me why
          </span>
        </div>
        <Button
          variant="ghost"
          size="icon"
          aria-label="Close provenance panel"
          onClick={onClose}
          className="h-7 w-7"
        >
          <X className="h-4 w-4" strokeWidth={1.5} />
        </Button>
      </header>

      <div className="flex-1 overflow-y-auto px-4 py-4">
        {!event ? (
          <EmptyView />
        ) : (
          <EventDetail event={event} generatedAt={generatedAt} />
        )}
      </div>
    </aside>
  );
}

function EmptyView(): JSX.Element {
  return (
    <div className="flex h-full flex-col items-start justify-start gap-2">
      <p
        className={cn(
          'font-narrative text-[var(--fs-lg)] tracking-[var(--track-tight)]',
          'text-[var(--fg)]',
        )}
      >
        Click an event to see its provenance.
      </p>
      <p className="text-[var(--fs-sm)] text-[var(--fg-muted)]">
        Kora shows where the event came from, what backs it, and when it last
        synced — so you can trust what's on the grid.
      </p>
    </div>
  );
}

interface EventDetailProps {
  event: CalendarEventView;
  generatedAt: string | null;
}

function EventDetail({ event, generatedAt }: EventDetailProps): JSX.Element {
  const dots = classifyProvenance(event.provenance);
  const primary = pickProvenance(event.provenance);
  const meta = event.metadata ?? {};
  const calId = pickStringMeta(meta, [
    'calendar_entry_id',
    'calendar_id',
    'calendarEntryId',
  ]);
  const reminderId = pickStringMeta(meta, ['reminder_id', 'reminderId']);
  const itemId = pickStringMeta(meta, ['item_id', 'itemId']);
  const lastSync = pickStringMeta(meta, [
    'last_sync_at',
    'last_synced_at',
    'lastSyncAt',
  ]);
  const sourceLabel = event.source || 'unknown';

  return (
    <div className="flex flex-col gap-4">
      <div>
        <div className="mb-1 flex items-center gap-1.5">
          {dots.map((k) => (
            <ProvenanceDot key={k} kind={k} size={8} />
          ))}
        </div>
        <h2
          className={cn(
            'font-narrative text-[var(--fs-xl)] tracking-[var(--track-tight)]',
            'text-[var(--fg)]',
          )}
        >
          {event.title || 'Untitled event'}
        </h2>
        <p className="font-mono num-tabular text-[var(--fs-xs)] text-[var(--fg-muted)]">
          {event.all_day
            ? 'All day'
            : `${formatTime(event.starts_at)}${event.ends_at ? ` – ${formatTime(event.ends_at)}` : ''}`}
        </p>
      </div>

      <DetailSection label="Source">
        <DetailRow label="Provenance" value={primary} />
        <DetailRow label="Source" value={sourceLabel} />
        <DetailRow label="Status" value={event.status || 'n/a'} />
        <DetailRow label="Kind" value={event.kind || 'n/a'} />
      </DetailSection>

      <DetailSection label="Backing rows">
        <DetailRow label="Event id" value={event.id} mono />
        {calId && <DetailRow label="Calendar entry" value={calId} mono />}
        {reminderId && <DetailRow label="Reminder" value={reminderId} mono />}
        {itemId && <DetailRow label="Item" value={itemId} mono />}
      </DetailSection>

      <DetailSection label="Layers">
        <div className="flex flex-wrap gap-1.5">
          {(event.layer_ids?.length ?? 0) === 0 ? (
            <span className="text-[var(--fs-xs)] text-[var(--fg-muted)]">
              No layer tags.
            </span>
          ) : (
            event.layer_ids.map((id) => (
              <span
                key={id}
                className={cn(
                  'rounded-[var(--r-pill)] border border-[var(--border)]',
                  'bg-[var(--surface-1)] px-2 py-0.5',
                  'text-[var(--fs-2xs)] text-[var(--fg-muted)]',
                )}
              >
                {id}
              </span>
            ))
          )}
        </div>
      </DetailSection>

      <DetailSection label="Sync">
        <DetailRow
          label="Last sync"
          value={lastSync ?? generatedAt ?? '—'}
          mono
        />
      </DetailSection>

      <Button
        variant="outline"
        size="sm"
        disabled
        aria-label="Open in source (not yet available)"
        className="gap-2 self-start"
      >
        <ExternalLink className="h-3.5 w-3.5" strokeWidth={1.5} />
        Open in source
      </Button>
    </div>
  );
}

function DetailSection({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}): JSX.Element {
  return (
    <section className="flex flex-col gap-1.5">
      <span className="text-[var(--fs-2xs)] uppercase tracking-[0.02em] text-[var(--fg-muted)]">
        {label}
      </span>
      <div className="flex flex-col gap-1">{children}</div>
    </section>
  );
}

function DetailRow({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
}): JSX.Element {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <span className="text-[var(--fs-xs)] text-[var(--fg-muted)]">{label}</span>
      <span
        className={cn(
          'min-w-0 flex-1 truncate text-right text-[var(--fs-xs)] text-[var(--fg)]',
          mono && 'font-mono num-tabular',
        )}
        title={value}
      >
        {value}
      </span>
    </div>
  );
}

function pickStringMeta(
  meta: Record<string, unknown>,
  keys: string[],
): string | null {
  for (const k of keys) {
    const v = meta[k];
    if (typeof v === 'string' && v.length > 0) return v;
  }
  return null;
}
