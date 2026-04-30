import { useConnectionStore } from '@/lib/api/connection';
import { Pill, type PillStatus } from '@/components/ui/pill';
import type { RuntimeState } from '@/lib/api/types';
import { isDemoMode } from '@/lib/demo/mode';

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

export function RuntimeIndicator(): JSX.Element {
  const connStatus = useConnectionStore((s) => s.status);
  const connection = useConnectionStore((s) => s.connection);

  if (isDemoMode()) {
    return <Pill status="unknown" label="Demo snapshot" />;
  }

  if (connStatus !== 'ready') {
    const fallback: PillStatus = connStatus === 'error' ? 'degraded' : 'unknown';
    const label = connStatus === 'error' ? 'No daemon' : 'Connecting…';
    return <Pill status={fallback} label={label} />;
  }
  const rawState = connection?.state;
  const state: RuntimeState =
    rawState && rawState in RUNTIME_TO_PILL
      ? (rawState as RuntimeState)
      : 'connected';
  return <Pill status={RUNTIME_TO_PILL[state]} label={RUNTIME_LABEL[state]} />;
}
