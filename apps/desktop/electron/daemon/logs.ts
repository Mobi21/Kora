import fs from 'node:fs/promises';
import path from 'node:path';

export async function tailDaemonLog(repoRoot: string, lines = 200): Promise<string[]> {
  const logPath = path.join(repoRoot, 'data', 'daemon.log');
  try {
    const raw = await fs.readFile(logPath, 'utf-8');
    const all = raw.split(/\r?\n/);
    return all.slice(-lines).filter((l) => l.length > 0);
  } catch {
    return [];
  }
}
