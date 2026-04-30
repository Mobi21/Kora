import { spawn } from 'node:child_process';
import { probeDaemon } from './lifecycle.js';
import { readLockfile } from './lockfile.js';

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

interface ExecOutcome {
  ok: boolean;
  stdout: string;
  stderr: string;
  code: number | null;
}

function exec(cmd: string, args: readonly string[], timeoutMs = 3000): Promise<ExecOutcome> {
  return new Promise((resolve) => {
    let resolved = false;
    let stdout = '';
    let stderr = '';
    const child = spawn(cmd, args, { stdio: ['ignore', 'pipe', 'pipe'] });

    const timer = setTimeout(() => {
      if (resolved) return;
      resolved = true;
      try {
        child.kill('SIGKILL');
      } catch {
        /* noop */
      }
      resolve({ ok: false, stdout, stderr, code: null });
    }, timeoutMs);

    child.stdout?.on('data', (b: Buffer) => {
      stdout += b.toString('utf8');
    });
    child.stderr?.on('data', (b: Buffer) => {
      stderr += b.toString('utf8');
    });
    child.on('error', () => {
      if (resolved) return;
      resolved = true;
      clearTimeout(timer);
      resolve({ ok: false, stdout, stderr, code: null });
    });
    child.on('close', (code) => {
      if (resolved) return;
      resolved = true;
      clearTimeout(timer);
      resolve({ ok: code === 0, stdout, stderr, code });
    });
  });
}

async function locateCli(): Promise<{ path?: string; available: boolean }> {
  const configured = process.env.KORA_CLI_PATH;
  if (configured) {
    const probe = await exec(configured, ['--version']);
    if (probe.ok) return { path: configured, available: true };
  }
  const which = await exec(process.platform === 'win32' ? 'where' : 'which', ['kora']);
  if (which.ok) {
    const first = which.stdout.split(/\r?\n/).map((l) => l.trim()).find(Boolean);
    return { path: first, available: true };
  }
  return { available: false };
}

async function readCliVersion(): Promise<string | undefined> {
  const out = await exec(process.env.KORA_CLI_PATH || 'kora', ['--version']);
  if (!out.ok) return undefined;
  const trimmed = out.stdout.trim() || out.stderr.trim();
  return trimmed || undefined;
}

/**
 * Typed first-run probe. Combines:
 *   1. Daemon lockfile + token + /health (via probeDaemon)
 *   2. Presence of `kora` CLI on PATH (via `which kora` + `kora --version`)
 *
 * `found` means: there is enough information for the app to talk to a daemon
 * (running or not). `running` means: the daemon answered /health.
 *
 * Designed to never throw; renderer treats failure as "not found".
 */
export async function probeForFirstRun(repoRoot: string): Promise<ProbeResult> {
  try {
    const [status, cli] = await Promise.all([probeDaemon(repoRoot), locateCli()]);
    const lock = await readLockfile(repoRoot);
    const version = cli.available ? await readCliVersion() : undefined;

    return {
      found: status.running || cli.available || lock != null,
      running: status.running,
      cliAvailable: cli.available,
      version,
      cliPath: cli.path,
      host: lock?.api_host ?? undefined,
      port: lock?.api_port ?? undefined,
      pid: status.pid ?? undefined,
      state: status.state ?? undefined,
      message: !cli.available
        ? 'kora CLI not found on PATH'
        : !status.running
        ? 'kora CLI is installed but the daemon is not responding'
        : undefined,
    };
  } catch (err) {
    return {
      found: false,
      running: false,
      cliAvailable: false,
      message: err instanceof Error ? err.message : String(err),
    };
  }
}
