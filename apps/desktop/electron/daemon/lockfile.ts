import fs from 'node:fs/promises';
import path from 'node:path';

export interface LockfilePayload {
  pid?: number;
  state?: string;
  api_host?: string | null;
  api_port?: number | null;
  started_at?: string | null;
  ready_at?: string | null;
}

export interface KoraConnectionResolved {
  host: string;
  port: number;
  token: string;
  state: string | null;
  pid: number | null;
}

const LOCKFILE_RELATIVE = path.join('data', 'kora.lock');
const TOKEN_RELATIVE = path.join('data', '.api_token');

export async function readLockfile(repoRoot: string): Promise<LockfilePayload | null> {
  const lockPath = path.join(repoRoot, LOCKFILE_RELATIVE);
  try {
    const raw = await fs.readFile(lockPath, 'utf-8');
    if (!raw.trim()) return null;
    return JSON.parse(raw) as LockfilePayload;
  } catch {
    return null;
  }
}

export async function readToken(repoRoot: string): Promise<string | null> {
  const tokenPath = path.join(repoRoot, TOKEN_RELATIVE);
  try {
    const raw = await fs.readFile(tokenPath, 'utf-8');
    const trimmed = raw.trim();
    return trimmed.length > 0 ? trimmed : null;
  } catch {
    return null;
  }
}

export async function readConnection(repoRoot: string): Promise<KoraConnectionResolved> {
  const [lock, token] = await Promise.all([readLockfile(repoRoot), readToken(repoRoot)]);
  if (!lock) {
    throw new Error(`Daemon lockfile not found at ${LOCKFILE_RELATIVE}`);
  }
  if (!token) {
    throw new Error(`API token not found at ${TOKEN_RELATIVE}`);
  }
  const port = lock.api_port ?? null;
  if (port === null) {
    throw new Error('Daemon lockfile is missing api_port');
  }
  return {
    host: lock.api_host ?? '127.0.0.1',
    port,
    token,
    state: lock.state ?? null,
    pid: lock.pid ?? null,
  };
}
