export function queryTiming(c: { ms: number; origin?: string; outcome?: string }): string {
  const duration = c.ms > 0 ? `${Number(c.ms.toPrecision(3))}ms` : 'below timer resolution';
  if (c.outcome === 'failure') return `Failed after ${duration}`;
  if (c.origin === 'falkordb') return `Executed in ${c.ms > 0 && c.ms < 1 ? '<1ms' : duration} on FalkorDB`;
  if (c.origin === 'memory') return `In-memory operation · ${duration}`;
  return 'Illustrative statement · not executed';
}
