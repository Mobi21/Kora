export interface HealthResponse {
  status: string;
  version?: string;
}

export async function fetchHealth(host: string, port: number, token: string): Promise<HealthResponse | null> {
  try {
    const res = await fetch(`http://${host}:${port}/api/v1/health`, {
      method: 'GET',
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!res.ok) return null;
    return (await res.json()) as HealthResponse;
  } catch {
    return null;
  }
}
