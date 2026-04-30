import { useMutation, useQuery, type UseMutationResult, type UseQueryResult } from '@tanstack/react-query';

export const FIRST_RUN_FLAG_KEY = 'kora.firstRunCompleted';

/** Mirror of the main-process `ProbeResult` shape. Kept in sync with
 *  electron/daemon/probe.ts and electron/preload.ts. */
export interface ProbeResult {
  found: boolean;
  running: boolean;
  cliAvailable: boolean;
  version?: string;
  cliPath?: string;
  host?: string;
  port?: number;
  pid?: number;
  state?: string;
  message?: string;
}

export interface ProbeViewModel extends ProbeResult {
  /** Convenience flag: probe answered AND daemon is healthy. */
  ready: boolean;
}

const NOT_AVAILABLE: ProbeViewModel = {
  found: false,
  running: false,
  cliAvailable: false,
  ready: false,
  message: 'Desktop bridge unavailable',
};

async function runDevProbe(): Promise<ProbeViewModel> {
  const res = await fetch('/__kora_dev/probe', {
    cache: 'no-store',
    headers: { Accept: 'application/json' },
  });
  if (!res.ok) {
    return NOT_AVAILABLE;
  }
  const result = (await res.json()) as ProbeResult;
  return { ...result, ready: result.running };
}

async function runProbe(): Promise<ProbeViewModel> {
  if (typeof window !== 'undefined' && window.kora?.daemon?.probe) {
    const result = await window.kora.daemon.probe();
    return { ...result, ready: result.running };
  }
  if (import.meta.env.DEV) {
    return await runDevProbe();
  }
  return NOT_AVAILABLE;
}

/**
 * Probe the daemon + CLI. Always resolves; never throws.
 * Mounted lazily by the wizard so we don't probe on every render of the app.
 */
export function useFirstRunProbeQuery(opts?: { enabled?: boolean }): UseQueryResult<ProbeViewModel> {
  return useQuery({
    queryKey: ['first-run', 'probe'],
    queryFn: runProbe,
    enabled: opts?.enabled ?? true,
    staleTime: 0,
    gcTime: 0,
    retry: false,
  });
}

export function useOpenDirectoryDialogMutation(): UseMutationResult<string | null, Error, { defaultPath?: string; title?: string } | void> {
  return useMutation({
    mutationFn: async (input) => {
      if (typeof window === 'undefined' || !window.kora?.openDirectoryDialog) {
        throw new Error('Directory picker unavailable in this environment');
      }
      const opts = (input ?? undefined) as { defaultPath?: string; title?: string } | undefined;
      return await window.kora.openDirectoryDialog(opts);
    },
  });
}

export function readFirstRunCompleted(): boolean {
  try {
    return window.localStorage.getItem(FIRST_RUN_FLAG_KEY) === 'true';
  } catch {
    return false;
  }
}

export function markFirstRunCompleted(): void {
  try {
    window.localStorage.setItem(FIRST_RUN_FLAG_KEY, 'true');
  } catch {
    /* ignore — private mode etc. */
  }
}

export function clearFirstRunCompleted(): void {
  try {
    window.localStorage.removeItem(FIRST_RUN_FLAG_KEY);
  } catch {
    /* ignore */
  }
}

/**
 * One-shot health probe used by the App gate. Times out at 3s; returns true
 * if the daemon answers /health within the budget, false otherwise.
 */
export async function pingDaemonOnce(timeoutMs = 3000): Promise<boolean> {
  try {
    const probe = typeof window !== 'undefined' && window.kora?.daemon?.probe
      ? window.kora.daemon.probe()
      : import.meta.env.DEV
        ? runDevProbe()
        : Promise.resolve({ found: false, running: false, cliAvailable: false });
    const result = await Promise.race<ProbeResult>([
      probe,
      new Promise<ProbeResult>((resolve) =>
        setTimeout(() => resolve({ found: false, running: false, cliAvailable: false, message: 'timeout' }), timeoutMs),
      ),
    ]);
    return result.running;
  } catch {
    return false;
  }
}
