import type { GraphNode } from '../api/schemas';

export interface BeliefDiff {
  added: GraphNode[];
  removed: GraphNode[];
  quarantined: GraphNode[];
  replaced: GraphNode[];
}

/** Client-side belief diff between two /graph snapshots (a = earlier, b = later). */
export function diffGraphs(a: GraphNode[], b: GraphNode[]): BeliefDiff {
  const before = new Map(a.map((n) => [n.id, n]));
  const after = new Map(b.map((n) => [n.id, n]));
  const added: GraphNode[] = [];
  const removed: GraphNode[] = [];
  const quarantined: GraphNode[] = [];
  const replaced: GraphNode[] = [];
  for (const n of b) {
    const prev = before.get(n.id);
    if (!prev) {
      added.push(n);
      continue;
    }
    if (n.status === 'QUARANTINED' && prev.status !== 'QUARANTINED') quarantined.push(n);
    if (n.status === 'SUPERSEDED' && prev.status !== 'SUPERSEDED') replaced.push(n);
  }
  for (const n of a) if (!after.has(n.id)) removed.push(n);
  return { added, removed, quarantined, replaced };
}
