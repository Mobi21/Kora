import { spawn } from 'node:child_process';
import path from 'node:path';
import { readLockfile, readToken } from './lockfile.js';
import { fetchHealth } from './health.js';

export interface DaemonStatus {
  running: boolean;
  state: string | null;
  pid: number | null;
  port: number | null;
  ownedByApp: boolean;
}

let ownedPid: number | null = null;

function pidIsRunning(pid: number | null): boolean {
  if (pid == null) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

export async function probeDaemon(repoRoot: string): Promise<DaemonStatus> {
  const lock = await readLockfile(repoRoot);
  const token = await readToken(repoRoot);
  if (!lock || !token) {
    return { running: false, state: null, pid: null, port: null, ownedByApp: false };
  }
  const pid = lock.pid ?? null;
  const port = lock.api_port ?? null;
  const alive = pidIsRunning(pid);
  let healthy = false;
  if (alive && port != null) {
    healthy = (await fetchHealth(lock.api_host ?? '127.0.0.1', port, token)) != null;
  }
  return {
    running: alive && healthy,
    state: lock.state ?? null,
    pid,
    port,
    ownedByApp: ownedPid === pid && pid != null,
  };
}

export async function startDaemon(repoRoot: string): Promise<DaemonStatus> {
  const existing = await probeDaemon(repoRoot);
  if (existing.running) return existing;

  const child = spawn('kora', [], {
    cwd: repoRoot,
    detached: true,
    stdio: 'ignore',
    env: {
      ...process.env,
      KORA_DATA_DIR: path.join(repoRoot, 'data'),
    },
  });
  child.unref();
  ownedPid = child.pid ?? null;

  const deadline = Date.now() + 20_000;
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 500));
    const status = await probeDaemon(repoRoot);
    if (status.running) {
      return { ...status, ownedByApp: ownedPid === status.pid };
    }
  }
  return await probeDaemon(repoRoot);
}

export async function stopDaemon(repoRoot: string): Promise<{ ok: boolean }> {
  const lock = await readLockfile(repoRoot);
  const token = await readToken(repoRoot);
  if (!lock || !token || lock.api_port == null) return { ok: false };
  try {
    const res = await fetch(`http://${lock.api_host ?? '127.0.0.1'}:${lock.api_port}/api/v1/daemon/shutdown`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!res.ok) return { ok: false };
    ownedPid = null;
    return { ok: true };
  } catch {
    return { ok: false };
  }
}
