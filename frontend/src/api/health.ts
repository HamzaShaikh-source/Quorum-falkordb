export interface Health {
  status: 'ok' | 'blocked';
  persistence: 'falkordb' | 'memory';
  falkordb: { up: boolean; url: string; latency_ms: number | null; version: string | null; error: string | null };
  steps: string[];
  warnings: string[];
  env?: string;
  demo?: boolean;
}

export type HealthResult = { kind: 'ok'; health: Health } | { kind: 'blocked'; health: Health } | { kind: 'unreachable'; reason: string };

/** Asks the backend whether FalkorDB is ready. No token needed. A backend that is not running at all is its own case. */
export async function fetchHealth(): Promise<HealthResult> {
  try {
    const res = await fetch('/api/health', { cache: 'no-store', signal: AbortSignal.timeout(4000) });
    if (res.status !== 200 && res.status !== 503) return { kind: 'unreachable', reason: `The backend answered HTTP ${res.status} at /api/health.` };
    const health = (await res.json()) as Health;
    return { kind: health.status === 'ok' ? 'ok' : 'blocked', health };
  } catch (e) {
    return { kind: 'unreachable', reason: e instanceof Error ? e.message : 'The backend did not answer.' };
  }
}
