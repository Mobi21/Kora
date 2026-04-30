import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { fileURLToPath, URL } from 'node:url';
import fs from 'node:fs/promises';
import path from 'node:path';

const REPO_ROOT = fileURLToPath(new URL('../..', import.meta.url));

const PROD_CSP =
  "default-src 'none'; " +
  "script-src 'self' 'wasm-unsafe-eval'; " +
  "style-src 'self' 'unsafe-inline'; " +
  "img-src 'self' data:; " +
  "font-src 'self' data:; " +
  "connect-src 'self' http://127.0.0.1:* ws://127.0.0.1:*; " +
  "base-uri 'none'; form-action 'none'; object-src 'none'";

const DEV_CSP =
  "default-src 'self'; " +
  "script-src 'self' 'unsafe-inline' 'unsafe-eval' 'wasm-unsafe-eval'; " +
  "style-src 'self' 'unsafe-inline'; " +
  "img-src 'self' data: blob:; " +
  "font-src 'self' data:; " +
  "connect-src 'self' http://127.0.0.1:* http://localhost:* ws://127.0.0.1:* ws://localhost:*; " +
  "object-src 'none'";

const cspPlugin = () => ({
  name: 'kora-csp-injector',
  transformIndexHtml: {
    order: 'pre' as const,
    handler(html: string, ctx: { server?: unknown }) {
      const csp = ctx.server ? DEV_CSP : PROD_CSP;
      return html.replace('<!--CSP_PLACEHOLDER-->', csp);
    },
  },
});

interface LockfilePayload {
  pid?: number;
  state?: string;
  api_host?: string | null;
  api_port?: number | null;
}

async function readDevConnection() {
  const [lockRaw, tokenRaw] = await Promise.all([
    fs.readFile(path.join(REPO_ROOT, 'data', 'kora.lock'), 'utf-8'),
    fs.readFile(path.join(REPO_ROOT, 'data', '.api_token'), 'utf-8'),
  ]);
  const lock = JSON.parse(lockRaw) as LockfilePayload;
  const token = tokenRaw.trim();
  const port = lock.api_port ?? null;
  if (!port) throw new Error('Daemon lockfile is missing api_port');
  if (!token) throw new Error('API token is empty');
  return {
    host: lock.api_host ?? '127.0.0.1',
    port,
    token,
    state: lock.state ?? null,
    pid: lock.pid ?? null,
  };
}

async function probeHealth(host: string, port: number): Promise<{ ok: boolean; version?: string }> {
  try {
    const res = await fetch(`http://${host}:${port}/api/v1/health`, {
      signal: AbortSignal.timeout(2_000),
    });
    if (!res.ok) return { ok: false };
    const body = (await res.json()) as { version?: string };
    return { ok: true, version: body.version };
  } catch {
    return { ok: false };
  }
}

function sendJson(
  res: { statusCode: number; setHeader: (name: string, value: string) => void; end: (body: string) => void },
  statusCode: number,
  body: unknown,
): void {
  res.statusCode = statusCode;
  res.setHeader('Content-Type', 'application/json; charset=utf-8');
  res.setHeader('Cache-Control', 'no-store');
  res.end(JSON.stringify(body));
}

const devConnectionPlugin = () => ({
  name: 'kora-dev-connection',
  configureServer(server: {
    middlewares: {
      use: (
        handler: (
          req: { url?: string },
          res: { statusCode: number; setHeader: (name: string, value: string) => void; end: (body: string) => void },
          next: () => void,
        ) => void,
      ) => void;
    };
  }) {
    server.middlewares.use(async (req, res, next) => {
      if (!req.url?.startsWith('/__kora_dev/')) {
        next();
        return;
      }

      try {
        const connection = await readDevConnection();
        const health = await probeHealth(connection.host, connection.port);

        if (req.url.startsWith('/__kora_dev/connection')) {
          sendJson(res, 200, connection);
          return;
        }

        if (req.url.startsWith('/__kora_dev/probe')) {
          sendJson(res, 200, {
            found: true,
            running: health.ok,
            cliAvailable: true,
            version: health.version,
            host: connection.host,
            port: connection.port,
            pid: connection.pid ?? undefined,
            state: connection.state ?? undefined,
            message: health.ok ? 'Daemon is reachable' : 'Daemon did not answer /api/v1/health',
          });
          return;
        }

        next();
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        if (req.url?.startsWith('/__kora_dev/probe')) {
          sendJson(res, 200, {
            found: false,
            running: false,
            cliAvailable: false,
            message,
          });
          return;
        }
        sendJson(res, 503, { message });
      }
    });
  },
});

export default defineConfig({
  plugins: [react(), tailwindcss(), cspPlugin(), devConnectionPlugin()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    strictPort: true,
    host: '127.0.0.1',
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    target: 'es2022',
    sourcemap: true,
  },
});
