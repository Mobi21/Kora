import { Pill, type PillStatus } from '@/components/ui/pill';
import { useConnectionStore } from '@/lib/api/connection';
import type { RuntimeState } from '@/lib/api/types';
import { LogsDrawer } from './LogsDrawer';

const RUNTIME_TO_PILL: Record<RuntimeState, PillStatus> = {
  starting: 'warn',
  connected: 'ok',
  degraded: 'warn',
  disconnected: 'degraded',
  needs_setup: 'unknown',
};

const RUNTIME_LABEL: Record<RuntimeState, string> = {
  starting: 'Starting',
  connected: 'Connected',
  degraded: 'Degraded',
  disconnected: 'Disconnected',
  needs_setup: 'Needs setup',
};

function RuntimeStatusInline(): JSX.Element {
  const connStatus = useConnectionStore((s) => s.status);
  const connection = useConnectionStore((s) => s.connection);

  let pill: { status: PillStatus; label: string };

  if (connStatus !== 'ready') {
    pill = {
      status: connStatus === 'error' ? 'degraded' : 'unknown',
      label: connStatus === 'error' ? 'No daemon' : 'Connecting…',
    };
  } else {
    const rawState = connection?.state;
    const state: RuntimeState =
      rawState && rawState in RUNTIME_TO_PILL
        ? (rawState as RuntimeState)
        : 'connected';
    pill = { status: RUNTIME_TO_PILL[state], label: RUNTIME_LABEL[state] };
  }

  return (
    <div className="flex items-center gap-2">
      <Pill status={pill.status} label={pill.label} />
    </div>
  );
}

export function RuntimeHeader(): JSX.Element {
  return (
    <header className="flex items-start justify-between gap-4">
      <div className="space-y-1.5">
        <h1
          className="font-narrative text-[var(--fs-3xl)] tracking-[var(--track-tight)] text-[var(--fg)]"
          style={{ fontWeight: 500 }}
        >
          Runtime
        </h1>
        <p className="font-narrative text-[var(--fs-md)] text-[var(--fg-muted)]">
          Daemon, doctor, setup, and permissions for this Kora install.
        </p>
      </div>
      <div className="flex items-center gap-3 pt-1">
        <RuntimeStatusInline />
        <LogsDrawer />
      </div>
    </header>
  );
}
