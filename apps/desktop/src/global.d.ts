import type { JSX as ReactJSX } from 'react/jsx-runtime';

export interface KoraDaemonStatusBridge {
  running: boolean;
  state: string | null;
  pid: number | null;
  port: number | null;
  ownedByApp: boolean;
}

export interface KoraConnectionBridge {
  host: string;
  port: number;
  token: string;
  state?: string | null;
  pid?: number | null;
}

export interface KoraNotifyPayload {
  title?: string;
  body?: string;
}

export interface KoraProbeResultBridge {
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

export interface KoraOpenDirectoryOptionsBridge {
  title?: string;
  defaultPath?: string;
  buttonLabel?: string;
}

export interface KoraBridge {
  getConnection: () => Promise<KoraConnectionBridge>;
  openExternal: (url: string) => Promise<{ ok: true }>;
  notify: (payload: KoraNotifyPayload) => Promise<{ ok: true }>;
  openDirectoryDialog: (
    options?: KoraOpenDirectoryOptionsBridge,
  ) => Promise<string | null>;
  daemon: {
    probe: () => Promise<KoraProbeResultBridge>;
    start: () => Promise<KoraDaemonStatusBridge>;
    stop: () => Promise<{ ok: boolean }>;
    logs: (lines?: number) => Promise<string[]>;
  };
}

declare global {
  interface Window {
    kora?: KoraBridge;
  }

  // React 19 removed the global JSX namespace. Re-expose it so existing
  // `: JSX.Element` return-type annotations resolve without per-file imports.
  // Reference: https://react.dev/blog/2024/04/25/react-19-upgrade-guide#typescript
  namespace JSX {
    type Element = ReactJSX.Element;
    type ElementClass = ReactJSX.ElementClass;
    type ElementAttributesProperty = ReactJSX.ElementAttributesProperty;
    type ElementChildrenAttribute = ReactJSX.ElementChildrenAttribute;
    type IntrinsicAttributes = ReactJSX.IntrinsicAttributes;
    type IntrinsicClassAttributes<T> = ReactJSX.IntrinsicClassAttributes<T>;
    type IntrinsicElements = ReactJSX.IntrinsicElements;
    type LibraryManagedAttributes<C, P> = ReactJSX.LibraryManagedAttributes<C, P>;
  }
}

export {};
