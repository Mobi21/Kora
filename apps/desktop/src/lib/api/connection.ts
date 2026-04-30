import { create } from 'zustand';
import { DEMO_CONNECTION, isDemoMode } from '@/lib/demo/mode';

export interface Connection {
  host: string;
  port: number;
  token: string;
  state?: string | null;
  pid?: number | null;
}

interface ConnectionStore {
  connection: Connection | null;
  status: 'idle' | 'loading' | 'ready' | 'error';
  error: string | null;
  load: () => Promise<void>;
  set: (c: Connection) => void;
  clear: () => void;
}

async function readDevConnection(): Promise<Connection> {
  const res = await fetch('/__kora_dev/connection', {
    cache: 'no-store',
    headers: { Accept: 'application/json' },
  });
  const body = (await res.json().catch(() => null)) as { message?: string } | Connection | null;
  if (!res.ok) {
    const message = body && 'message' in body && body.message ? body.message : `Dev connection failed: ${res.status}`;
    throw new Error(message);
  }
  if (!body || !('host' in body) || !('port' in body) || !('token' in body)) {
    throw new Error('Dev connection response was missing host, port, or token');
  }
  return body;
}

async function resolveConnection(): Promise<Connection> {
  if (typeof window !== 'undefined' && window.kora) {
    return await window.kora.getConnection();
  }

  if (import.meta.env.DEV) {
    return await readDevConnection();
  }

  throw new Error('Kora bridge not available');
}

export const useConnectionStore = create<ConnectionStore>((set) => ({
  connection: null,
  status: 'idle',
  error: null,
  load: async () => {
    if (isDemoMode()) {
      set({ connection: DEMO_CONNECTION, status: 'ready', error: null });
      return;
    }
    set({ status: 'loading', error: null });
    try {
      const resolved = await resolveConnection();
      set({ connection: resolved, status: 'ready', error: null });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      set({ status: 'error', error: message });
    }
  },
  set: (connection) => set({ connection, status: 'ready', error: null }),
  clear: () => set({ connection: null, status: 'idle', error: null }),
}));

export function useConnection(): Connection | null {
  const connection = useConnectionStore((s) => s.connection);
  return isDemoMode() ? DEMO_CONNECTION : connection;
}
