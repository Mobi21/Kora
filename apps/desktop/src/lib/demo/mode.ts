import type { Connection } from '@/lib/api/connection';

export const DEMO_LABEL =
  'Demo mode · sanitized acceptance snapshot · not connected to your local daemon';

const appBase = import.meta.env.BASE_URL === '/'
  ? ''
  : import.meta.env.BASE_URL.replace(/\/$/, '');

export const DEMO_SNAPSHOT_PATH = `${appBase}/demo/acceptance_demo_snapshot.json`;

export const DEMO_CONNECTION: Connection = {
  host: 'static-demo',
  port: 0,
  token: 'acceptance-demo',
  state: 'demo',
  pid: null,
};

export function isDemoMode(): boolean {
  if (typeof window === 'undefined') return false;
  const params = new URLSearchParams(window.location.search);
  if (params.get('demo') === 'off') {
    window.sessionStorage.removeItem('kora.demoMode');
    return false;
  }
  if (params.get('demo') === 'acceptance' || params.get('demo') === 'snapshot') {
    window.sessionStorage.setItem('kora.demoMode', 'acceptance');
    return true;
  }
  return (
    window.sessionStorage.getItem('kora.demoMode') === 'acceptance' ||
    import.meta.env.VITE_KORA_DEMO === 'acceptance'
  );
}
