import type { GraphEdge, GraphNode } from '../api/schemas';

export const NODE_W = 160;
export const NODE_H = 54;
export const COL_X = [0, 206, 412, 618];
export const GAP_Y = 68;

export const KIND_COL: Record<string, number> = { source: 0, fact: 1, conclusion: 2, action: 3 };
export const colOf = (n: Pick<GraphNode, 'kind'>) => KIND_COL[n.kind] ?? 1;

export interface Pos {
  x: number;
  y: number;
}

const CAUSAL = new Set(['LEADS_TO', 'SUPPORTS', 'FROM_SOURCE']);

function spread(items: { id: string; want: number }[], gap: number): Record<string, number> {
  const sorted = [...items].sort((a, b) => a.want - b.want);
  const out: Record<string, number> = {};
  let floor = -Infinity;
  for (const it of sorted) {
    const y = Math.max(it.want, floor);
    out[it.id] = y;
    floor = y + gap;
  }
  return out;
}

/** Column positions fixed by node kind; rows follow the parents so causal lines stay short. */
export function layoutAll(nodes: GraphNode[], edges: GraphEdge[]): Record<string, Pos> {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const parents = new Map<string, string[]>();
  const children = new Map<string, string[]>();
  for (const e of edges) {
    if (!CAUSAL.has(e.type) || !byId.has(e.source) || !byId.has(e.target)) continue;
    parents.set(e.target, [...(parents.get(e.target) ?? []), e.source]);
    children.set(e.source, [...(children.get(e.source) ?? []), e.target]);
  }
  const y: Record<string, number> = {};
  const mean = (ids: string[]) => {
    const v = ids.map((i) => y[i]).filter((q): q is number => q !== undefined);
    return v.length ? v.reduce((a, b) => a + b, 0) / v.length : undefined;
  };

  const facts = nodes.filter((n) => n.kind === 'fact');
  facts.forEach((n, i) => (y[n.id] = i * GAP_Y));

  const sources = nodes.filter((n) => n.kind === 'source');
  Object.assign(
    y,
    spread(
      sources.map((n, i) => ({ id: n.id, want: mean(children.get(n.id) ?? []) ?? i * GAP_Y })),
      GAP_Y,
    ),
  );

  for (const kind of ['conclusion', 'action'] as const) {
    const col = nodes.filter((n) => n.kind === kind);
    const wants: { id: string; want: number }[] = [];
    col.forEach((n, i) => {
      const p = mean(parents.get(n.id) ?? []);
      wants.push({ id: n.id, want: p ?? i * GAP_Y });
    });
    const placed = spread(wants, GAP_Y);
    Object.assign(y, placed);
  }

  const out: Record<string, Pos> = {};
  for (const n of nodes) out[n.id] = { x: COL_X[colOf(n)] ?? 0, y: y[n.id] ?? 0 };
  return out;
}

/** Finds a free row in a column near `want` without moving anything else. */
export function freeSlot(taken: number[], want: number, gap = GAP_Y): number {
  const free = (v: number) => taken.every((t) => Math.abs(t - v) >= gap - 1);
  if (free(want)) return want;
  for (let k = 1; k < 200; k++) {
    if (free(want + k * gap)) return want + k * gap;
    if (free(want - k * gap)) return want - k * gap;
  }
  return want;
}
