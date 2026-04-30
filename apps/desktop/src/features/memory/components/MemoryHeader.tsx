import type { VaultState } from '@/lib/api/types';
import { Pill, type PillStatus } from '@/components/ui/pill';
import { cn } from '@/lib/utils';
import { truncateMiddle } from '../utils/format';

const VAULT_PILL: Record<VaultState['health'], { status: PillStatus; label: string }> = {
  ok: { status: 'ok', label: 'ok' },
  unconfigured: { status: 'unknown', label: 'unconfigured' },
  missing: { status: 'degraded', label: 'missing' },
  degraded: { status: 'warn', label: 'degraded' },
};

interface MemoryHeaderProps {
  vault: VaultState | null;
}

export function MemoryHeader({ vault }: MemoryHeaderProps): JSX.Element {
  const pill = vault ? VAULT_PILL[vault.health] : { status: 'unknown' as PillStatus, label: '—' };
  const path = vault?.path ?? vault?.memory_root ?? '';

  return (
    <header className="flex flex-wrap items-end justify-between gap-3">
      <div className="space-y-1">
        <h1
          className={cn(
            'font-narrative text-[var(--fs-3xl)] tracking-[var(--track-tight)] text-[var(--fg)]',
          )}
        >
          Memory
        </h1>
        <p className="font-narrative text-[var(--fs-md)] italic text-[var(--fg-muted)]">
          What Kora believes about you, and how to correct it.
        </p>
      </div>
      <div className="flex flex-col items-end gap-1.5">
        <Pill status={pill.status} label={`Vault ${pill.label}`}>
          {pill.label}
        </Pill>
        {path && (
          <span
            title={path}
            className="font-mono num-tabular text-[var(--fs-2xs)] text-[var(--fg-subtle)]"
          >
            {truncateMiddle(path, 56)}
          </span>
        )}
      </div>
    </header>
  );
}
