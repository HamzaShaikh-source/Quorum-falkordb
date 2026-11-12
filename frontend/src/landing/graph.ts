export type Kind = 'source' | 'fact' | 'conclusion' | 'decision' | 'action';

export interface LNode {
  id: string;
  kind: Kind;
  label: string;
  sub?: string;
  x: number;
  y: number;
  root?: boolean;
  executed?: boolean;
  blocked?: boolean;
}
export interface LEdge {
  from: string;
  to: string;
}

export const W = 940;
export const H = 410;
export const NW = 156;
export const NH = 46;

export const NODES: LNode[] = [
  { id: 's1', kind: 'source', label: 'Policy v3', sub: 'document', x: 82, y: 70, root: true },
  { id: 's2', kind: 'source', label: 'Partner feed', sub: 'webhook', x: 82, y: 262 },
  { id: 'f1', kind: 'fact', label: 'Window: 30 days', x: 318, y: 70, root: true },
  { id: 'f2', kind: 'fact', label: 'Window: 90 days', x: 318, y: 262 },
  { id: 'c2', kind: 'conclusion', label: 'Standard: 30 days', x: 556, y: 36 },
  { id: 'd2', kind: 'decision', label: 'Macro quotes 30', x: 556, y: 104 },
  { id: 'c1', kind: 'conclusion', label: 'Up to 90 qualify', x: 556, y: 226 },
  { id: 'd1', kind: 'decision', label: 'Approve 90d refunds', x: 556, y: 300 },
  { id: 'a1', kind: 'action', label: 'Refund #1042', x: 806, y: 234, executed: true },
  { id: 'a2', kind: 'action', label: 'Refund #1043', x: 806, y: 296, executed: true },
  { id: 'a3', kind: 'action', label: 'Email customers', x: 806, y: 358, blocked: true },
];

export const EDGES: LEdge[] = [
  { from: 's1', to: 'f1' },
  { from: 's2', to: 'f2' },
  { from: 'f1', to: 'c2' },
  { from: 'c2', to: 'd2' },
  { from: 'f2', to: 'c1' },
  { from: 'c1', to: 'd1' },
  { from: 'd1', to: 'a1' },
  { from: 'd1', to: 'a2' },
  { from: 'd1', to: 'a3' },
];

export const byId = Object.fromEntries(NODES.map((n) => [n.id, n])) as Record<string, LNode>;

/** Hop depth of every node reachable from `start`, following causal edges downstream. */
export function cascadeFrom(start: string): Map<string, number> {
  const depth = new Map<string, number>([[start, 0]]);
  const queue = [start];
  while (queue.length) {
    const cur = queue.shift() as string;
    for (const e of EDGES) {
      if (e.from === cur && !depth.has(e.to)) {
        depth.set(e.to, (depth.get(cur) ?? 0) + 1);
        queue.push(e.to);
      }
    }
  }
  return depth;
}

export type ActionPhase = 'idle' | 'rolling' | 'rolled' | 'cancelled';

export interface CascadeStats {
  held: number;
  rolling: number;
  rolled: number;
  cancelled: number;
}

export function edgePath(a: LNode, b: LNode): string {
  const x1 = a.x + NW / 2;
  const x2 = b.x - NW / 2;
  const mid = x1 + (x2 - x1) / 2;
  const r = 10;
  const dy = b.y - a.y;
  if (Math.abs(dy) < 2) return `M${x1} ${a.y} H${x2}`;
  const s = dy > 0 ? 1 : -1;
  return `M${x1} ${a.y} H${mid - r} Q${mid} ${a.y} ${mid} ${a.y + s * r} V${b.y - s * r} Q${mid} ${b.y} ${mid + r} ${b.y} H${x2}`;
}
