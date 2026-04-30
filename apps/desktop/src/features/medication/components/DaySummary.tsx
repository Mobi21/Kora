import { Pill } from '@/components/ui/pill';
import { formatRelative } from '@/lib/dates';
import type { MedicationDose } from '@/lib/api/types';
import { summarizeDay } from '../utils/format';

interface DaySummaryProps {
  doses: MedicationDose[];
  lastTakenAt: string | null;
  healthSignals: string[];
}

interface StatProps {
  label: string;
  value: number;
  status: 'ok' | 'warn' | 'unknown';
}

function Stat({ label, value, status }: StatProps): JSX.Element {
  return (
    <div className="flex items-center gap-2">
      <Pill status={status} label={label}>
        <span className="font-mono num-tabular">{value}</span>
        <span className="text-[var(--fg-muted)]">{label}</span>
      </Pill>
    </div>
  );
}

export function DaySummary({
  doses,
  lastTakenAt,
  healthSignals,
}: DaySummaryProps): JSX.Element {
  const counts = summarizeDay(doses);
  return (
    <section
      className="space-y-3 rounded-[var(--r-2)] border border-[var(--border)] bg-[var(--surface-1)] p-4"
      aria-labelledby="medication-day-summary"
    >
      <h2
        id="medication-day-summary"
        className="font-narrative text-[var(--fs-lg)] tracking-[var(--track-tight)] text-[var(--fg)]"
      >
        Day so far
      </h2>
      <div className="flex flex-wrap items-center gap-2">
        <Stat label="Taken" value={counts.taken} status="ok" />
        <Stat label="Skipped" value={counts.skipped + counts.missed} status="warn" />
        <Stat label="Pending" value={counts.pending} status="unknown" />
      </div>
      {lastTakenAt && (
        <p className="text-[var(--fs-sm)] text-[var(--fg-muted)]">
          Last taken{' '}
          <span className="font-mono num-tabular text-[var(--fg)]">
            {formatRelative(lastTakenAt)}
          </span>
          .
        </p>
      )}
      {healthSignals.length > 0 && (
        <ul className="space-y-1 pt-1 text-[var(--fs-sm)] italic text-[var(--fg-muted)]">
          {healthSignals.map((s, i) => (
            <li key={`${i}-${s}`}>· {s}</li>
          ))}
        </ul>
      )}
    </section>
  );
}
