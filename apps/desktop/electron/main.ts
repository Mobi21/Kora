import type { BrowserWindow as BrowserWindowInstance } from 'electron';
import { app, BrowserWindow, ipcMain, session, shell, Notification } from 'electron';
import path from 'node:path';
import { readConnection } from './daemon/lockfile.js';
import { startDaemon, stopDaemon } from './daemon/lifecycle.js';
import { probeForFirstRun } from './daemon/probe.js';
import { tailDaemonLog } from './daemon/logs.js';
import { openDirectoryDialog, type OpenDirectoryOptions } from './dialog/openDirectory.js';
// import { autoUpdater } from 'electron-updater'; // TODO: wire automatic updates — see docs/PACKAGING.md

const REPO_ROOT = path.resolve(__dirname, '..', '..', '..');
const DEV_URL = process.env.VITE_DEV_SERVER_URL ?? 'http://127.0.0.1:5173';
const IS_DEV = !app.isPackaged;

function isAllowedSender(senderUrl: string): boolean {
  if (IS_DEV) {
    return senderUrl.startsWith(new URL(DEV_URL).origin);
  }
  return senderUrl.startsWith('file://');
}

let mainWindow: BrowserWindowInstance | null = null;

async function createWindow(): Promise<void> {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1024,
    minHeight: 640,
    show: false,
    backgroundColor: '#f8f5ee',
    titleBarStyle: 'hiddenInset',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
      webSecurity: true,
      spellcheck: true,
    },
  });

  mainWindow.once('ready-to-show', () => {
    mainWindow?.show();
  });

  if (IS_DEV) {
    await mainWindow.loadURL(DEV_URL);
  } else {
    await mainWindow.loadFile(path.join(__dirname, '..', 'dist', 'index.html'));
  }

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    void shell.openExternal(url);
    return { action: 'deny' };
  });
}

function registerIpc(): void {
  ipcMain.handle('kora:get-connection', async (event) => {
    const senderUrl = event.senderFrame?.url ?? '';
    if (!isAllowedSender(senderUrl)) {
      throw new Error('IPC sender not allowed');
    }
    return await readConnection(REPO_ROOT);
  });

  ipcMain.handle('kora:open-external', async (event, url: unknown) => {
    const senderUrl = event.senderFrame?.url ?? '';
    if (!isAllowedSender(senderUrl)) {
      throw new Error('IPC sender not allowed');
    }
    if (typeof url !== 'string') throw new Error('url must be a string');
    if (!/^https?:\/\//i.test(url)) throw new Error('Only http(s) URLs are allowed');
    await shell.openExternal(url);
    return { ok: true } as const;
  });

  ipcMain.handle('kora:notify', (event, payload: unknown) => {
    const senderUrl = event.senderFrame?.url ?? '';
    if (!isAllowedSender(senderUrl)) {
      throw new Error('IPC sender not allowed');
    }
    const data = (payload ?? {}) as { title?: string; body?: string };
    const n = new Notification({
      title: data.title ?? 'Kora',
      body: data.body ?? '',
      silent: false,
    });
    n.show();
    return { ok: true } as const;
  });

  ipcMain.handle('kora:daemon-probe', async (event) => {
    const senderUrl = event.senderFrame?.url ?? '';
    if (!isAllowedSender(senderUrl)) throw new Error('IPC sender not allowed');
    return await probeForFirstRun(REPO_ROOT);
  });

  ipcMain.handle('kora:open-directory-dialog', async (event, opts: unknown) => {
    const senderUrl = event.senderFrame?.url ?? '';
    if (!isAllowedSender(senderUrl)) throw new Error('IPC sender not allowed');
    const options: OpenDirectoryOptions = {};
    if (opts && typeof opts === 'object') {
      const o = opts as Record<string, unknown>;
      if (typeof o.title === 'string') options.title = o.title;
      if (typeof o.defaultPath === 'string') options.defaultPath = o.defaultPath;
      if (typeof o.buttonLabel === 'string') options.buttonLabel = o.buttonLabel;
    }
    return await openDirectoryDialog(mainWindow, options);
  });

  ipcMain.handle('kora:daemon-start', async (event) => {
    const senderUrl = event.senderFrame?.url ?? '';
    if (!isAllowedSender(senderUrl)) throw new Error('IPC sender not allowed');
    return await startDaemon(REPO_ROOT);
  });

  ipcMain.handle('kora:daemon-stop', async (event) => {
    const senderUrl = event.senderFrame?.url ?? '';
    if (!isAllowedSender(senderUrl)) throw new Error('IPC sender not allowed');
    return await stopDaemon(REPO_ROOT);
  });

  ipcMain.handle('kora:daemon-logs', async (event, lines: unknown) => {
    const senderUrl = event.senderFrame?.url ?? '';
    if (!isAllowedSender(senderUrl)) throw new Error('IPC sender not allowed');
    const n = typeof lines === 'number' && Number.isFinite(lines) ? Math.max(1, Math.min(2000, Math.floor(lines))) : 200;
    return await tailDaemonLog(REPO_ROOT, n);
  });
}

/**
 * Production-grade CSP applied via response headers, not just the meta tag in
 * index.html. Sending it as a real header is more robust for packaged builds
 * (the meta tag is honored only at parse time and forbids a few directives
 * like frame-ancestors).
 *
 * Policy:
 *   default-src 'none'                                — deny by default
 *   script-src  'self' 'wasm-unsafe-eval'             — allow renderer chunks + wasm
 *   style-src   'self' 'unsafe-inline'                — Tailwind / Radix inject
 *   img-src     'self' data:                          — bundled images + tiny pngs
 *   font-src    'self' data:                          — bundled font assets
 *   connect-src 'self' http://127.0.0.1:* ws://127.0.0.1:* — local daemon only
 *   frame-ancestors 'none'                            — no embedding
 *   base-uri    'none'                                — no <base> tampering
 *   form-action 'none'                                — no form posts
 *   object-src  'none'                                — no plugins
 */
function installCsp(): void {
  // Vite dev server needs eval + websocket HMR + inline modules. We relax
  // script-src in dev only; production stays strict.
  const prodPolicy = [
    "default-src 'none'",
    "script-src 'self' 'wasm-unsafe-eval'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data:",
    "font-src 'self' data:",
    "connect-src 'self' http://127.0.0.1:* ws://127.0.0.1:*",
    "frame-ancestors 'none'",
    "base-uri 'none'",
    "form-action 'none'",
    "object-src 'none'",
  ].join('; ');

  const devPolicy = [
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline' 'unsafe-eval' 'wasm-unsafe-eval'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    "connect-src 'self' http://127.0.0.1:* http://localhost:* ws://127.0.0.1:* ws://localhost:*",
    "frame-ancestors 'none'",
    "object-src 'none'",
  ].join('; ');

  const policy = IS_DEV ? devPolicy : prodPolicy;

  session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
    const responseHeaders = { ...(details.responseHeaders ?? {}) };
    delete responseHeaders['content-security-policy'];
    delete responseHeaders['Content-Security-Policy'];
    responseHeaders['Content-Security-Policy'] = [policy];
    callback({ responseHeaders });
  });
}

app.on('web-contents-created', (_event, contents) => {
  contents.on('will-navigate', (event, navigationUrl) => {
    const url = new URL(navigationUrl);
    if (url.origin !== DEV_URL && url.protocol !== 'file:') {
      event.preventDefault();
      void shell.openExternal(navigationUrl);
    }
  });
});

app.whenReady().then(async () => {
  installCsp();
  registerIpc();
  await createWindow();

  // ---- electron-updater placeholder ----------------------------------
  // When ready to ship auto-updates:
  //   1. Add `electron-updater` to dependencies (see PACKAGING.md).
  //   2. Configure `publish` in electron-builder.yml.
  //   3. Uncomment the import at the top of this file and the block below.
  //
  // if (!IS_DEV) {
  //   autoUpdater.autoDownload = true;
  //   autoUpdater.autoInstallOnAppQuit = true;
  //   autoUpdater.on('update-downloaded', () => {
  //     mainWindow?.webContents.send('kora:update-ready');
  //   });
  //   void autoUpdater.checkForUpdatesAndNotify();
  // }
  // --------------------------------------------------------------------

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) void createWindow();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});
