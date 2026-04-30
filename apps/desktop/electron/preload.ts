import { contextBridge, ipcRenderer } from 'electron';

export interface KoraConnection {
  host: string;
  port: number;
  token: string;
  state?: string | null;
  pid?: number | null;
}

export interface KoraDaemonStatus {
  running: boolean;
  state: string | null;
  pid: number | null;
  port: number | null;
  ownedByApp: boolean;
}

export interface KoraNotifyPayload {
  title?: string;
  body?: string;
}

export interface KoraProbeResult {
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

export interface KoraOpenDirectoryOptions {
  title?: string;
  defaultPath?: string;
  buttonLabel?: string;
}

const api = {
  getConnection: (): Promise<KoraConnection> => ipcRenderer.invoke('kora:get-connection'),
  openExternal: (url: string): Promise<{ ok: true }> =>
    ipcRenderer.invoke('kora:open-external', url),
  notify: (payload: KoraNotifyPayload): Promise<{ ok: true }> =>
    ipcRenderer.invoke('kora:notify', payload),
  openDirectoryDialog: (
    options?: KoraOpenDirectoryOptions,
  ): Promise<string | null> =>
    ipcRenderer.invoke('kora:open-directory-dialog', options ?? {}),
  daemon: {
    probe: (): Promise<KoraProbeResult> => ipcRenderer.invoke('kora:daemon-probe'),
    start: (): Promise<KoraDaemonStatus> => ipcRenderer.invoke('kora:daemon-start'),
    stop: (): Promise<{ ok: boolean }> => ipcRenderer.invoke('kora:daemon-stop'),
    logs: (lines?: number): Promise<string[]> => ipcRenderer.invoke('kora:daemon-logs', lines),
  },
} as const;

contextBridge.exposeInMainWorld('kora', api);

export type KoraBridge = typeof api;
