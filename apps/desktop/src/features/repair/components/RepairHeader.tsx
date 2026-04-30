import { ChevronLeft, ChevronRight } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { formatDay } from '@/lib/dates';

type Mode = 'guided' | 'board';

interface RepairHeaderProps {
  date: string;
  onDateChange: (next: string) => void;
  mode: Mode;
  onModeChange: (next: Mode) => void;
}

function shiftIso(iso: string, days: number): string {
  const d = new Date(`${iso}T00:00:00`);
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
}

export function RepairHeader({
  date,
  onDateChange,
  mode,
  onModeChange,
}: RepairHeaderProps): JSX.Element {
  return (
    <header className="flex flex-col gap-4">
      <div className="flex flex-col gap-1.5">
        <h1
          className="font-narrative text-[var(--fs-3xl)] tracking-[var(--track-tight)] text-[var(--fg)]"
        >
          Repair
        </h1>
        <p className="font-narrative text-[var(--fs-md)] italic text-[var(--fg-muted)]">
          Make today smaller. Move what doesn&rsquo;t fit.
        </p>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div
          className="inline-flex items-center gap-1 rounded-[var(--r-2)] border border-[var(--border)] bg-[var(--surface-1)] p-1"
          role="group"
          aria-label="Date stepper"
        >
          <Button
            variant="ghost"
            size="icon"
            aria-label="Previous day"
            className="h-7 w-7"
            onClick={() => onDateChange(shiftIso(date, -1))}
          >
            <ChevronLeft className="h-4 w-4" strokeWidth={1.5} />
          </Button>
          <span
            className="px-2 text-[var(--fs-sm)] text-[var(--fg)] num-tabular"
            aria-live="polite"
          >
            {formatDay(`${date}T00:00:00`)}
          </span>
          <Button
            variant="ghost"
            size="icon"
            aria-label="Next day"
            className="h-7 w-7"
            onClick={() => onDateChange(shiftIso(date, 1))}
          >
            <ChevronRight className="h-4 w-4" strokeWidth={1.5} />
          </Button>
        </div>

        <Tabs
          value={mode}
          onValueChange={(v) => onModeChange(v as Mode)}
          aria-label="Repair mode"
        >
          <TabsList
            className="h-auto gap-0.5 rounded-[var(--r-2)] border border-[var(--border)] bg-[var(--surface-1)] p-1"
            style={{ borderBottomWidth: 1 }}
          >
            <TabsTrigger
              value="guided"
              className="h-7 rounded-[var(--r-1)] px-3 text-[var(--fs-sm)] data-[state=active]:bg-[var(--surface-3)] data-[state=active]:after:hidden after:hidden"
            >
              Guided
            </TabsTrigger>
            <TabsTrigger
              value="board"
              className="h-7 rounded-[var(--r-1)] px-3 text-[var(--fs-sm)] data-[state=active]:bg-[var(--surface-3)] data-[state=active]:after:hidden after:hidden"
            >
              Board
            </TabsTrigger>
          </TabsList>
        </Tabs>
      </div>
    </header>
  );
}
