import type { GraphEdge, GraphNode } from '../api/schemas';

const CAUSAL = new Set(['LEADS_TO', 'SUPPORTS', 'FROM_SOURCE']);

export interface Adj {
  out: Map<string, string[]>;
  inn: Map<string, string[]>;
  und: Map<string, string[]>;
}

/** Adjacency over the causal edges only (not CONTRADICTS or SUPERSEDES). */
export function adjacency(nodes: GraphNode[], edges: GraphEdge[]): Adj {
  const out = new Map<string, string[]>();
  const inn = new Map<string, string[]>();
  const und = new Map<string, string[]>();
  for (const n of nodes) {
    out.set(n.id, []);
    inn.set(n.id, []);
    und.set(n.id, []);
  }
  for (const e of edges) {
    if (!CAUSAL.has(e.type) || !out.has(e.source) || !out.has(e.target)) continue;
    out.get(e.source)?.push(e.target);
    inn.get(e.target)?.push(e.source);
    und.get(e.source)?.push(e.target);
    und.get(e.target)?.push(e.source);
  }
  return { out, inn, und };
}

export const degree = (adj: Adj, id: string) => adj.und.get(id)?.length ?? 0;

/** Everything within `depth` hops of `id`, ignoring edge direction (an Obsidian-style local graph). */
export function neighborhood(adj: Adj, id: string, depth: number): Map<string, number> {
  const seen = new Map<string, number>([[id, 0]]);
  const queue = [id];
  while (queue.length) {
    const cur = queue.shift() as string;
    const d = seen.get(cur) ?? 0;
    if (d >= depth) continue;
    for (const nb of adj.und.get(cur) ?? []) {
      if (!seen.has(nb)) {
        seen.set(nb, d + 1);
        queue.push(nb);
      }
    }
  }
  return seen;
}

export function shortestPath(adj: Adj, from: string, to: string): string[] | null {
  if (!adj.und.has(from) || !adj.und.has(to)) return null;
  if (from === to) return [from];
  const prev = new Map<string, string>();
  const seen = new Set<string>([from]);
  const queue = [from];
  while (queue.length) {
    const cur = queue.shift() as string;
    for (const nb of adj.und.get(cur) ?? []) {
      if (seen.has(nb)) continue;
      seen.add(nb);
      prev.set(nb, cur);
      if (nb === to) {
        const path = [to];
        let p = to;
        while (prev.has(p)) {
          p = prev.get(p) as string;
          path.unshift(p);
        }
        return path;
      }
      queue.push(nb);
    }
  }
  return null;
}

export function descendants(adj: Adj, id: string): Set<string> {
  const seen = new Set<string>();
  const queue = [id];
  while (queue.length) {
    const cur = queue.shift() as string;
    for (const nb of adj.out.get(cur) ?? []) {
      if (!seen.has(nb)) {
        seen.add(nb);
        queue.push(nb);
      }
    }
  }
  return seen;
}

export function ancestors(adj: Adj, id: string): Set<string> {
  const seen = new Set<string>();
  const queue = [id];
  while (queue.length) {
    const cur = queue.shift() as string;
    for (const nb of adj.inn.get(cur) ?? []) {
      if (!seen.has(nb)) {
        seen.add(nb);
        queue.push(nb);
      }
    }
  }
  return seen;
}

export interface GodNode {
  id: string;
  degree: number;
  downstream: number;
}

/** The most connected nodes: where one wrong belief does the most damage. */
export function godNodes(nodes: GraphNode[], adj: Adj, limit = 5): GodNode[] {
  return nodes
    .map((n) => ({ id: n.id, degree: degree(adj, n.id), downstream: descendants(adj, n.id).size }))
    .filter((g) => g.degree > 0)
    .sort((a, b) => b.downstream - a.downstream || b.degree - a.degree)
    .slice(0, limit);
}

export interface Community {
  ids: string[];
  roots: string[];
  quarantined: number;
}

/** Connected components of the causal graph, each named by the sources it starts from. */
export function communities(nodes: GraphNode[], adj: Adj): Community[] {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const seen = new Set<string>();
  const out: Community[] = [];
  for (const n of nodes) {
    if (seen.has(n.id)) continue;
    const ids: string[] = [];
    const queue = [n.id];
    seen.add(n.id);
    while (queue.length) {
      const cur = queue.shift() as string;
      ids.push(cur);
      for (const nb of adj.und.get(cur) ?? []) {
        if (!seen.has(nb)) {
          seen.add(nb);
          queue.push(nb);
        }
      }
    }
    const roots = ids.filter((i) => (adj.inn.get(i)?.length ?? 0) === 0).map((i) => byId.get(i)?.label ?? i);
    out.push({ ids, roots, quarantined: ids.filter((i) => byId.get(i)?.status === 'QUARANTINED').length });
  }
  return out.sort((a, b) => b.ids.length - a.ids.length);
}

export interface StatusCounts {
  healthy: number;
  quarantined: number;
  superseded: number;
  stale: number;
  total: number;
}

export function statusCounts(nodes: GraphNode[]): StatusCounts {
  return {
    healthy: nodes.filter((n) => n.status === 'HEALTHY' && !n.stale).length,
    quarantined: nodes.filter((n) => n.status === 'QUARANTINED').length,
    superseded: nodes.filter((n) => n.status === 'SUPERSEDED').length,
    stale: nodes.filter((n) => n.status === 'HEALTHY' && n.stale).length,
    total: nodes.length,
  };
}

export const KIND_ORDER = ['source', 'fact', 'conclusion', 'action'] as const;

export interface FlowColumn {
  kind: string;
  total: number;
  quarantined: number;
}
export interface FlowLink {
  from: number;
  to: number;
  total: number;
  bad: number;
}

/** Belief flow: nodes per kind and the causal edges between neighbouring kinds, split by whether the target is quarantined. */
export function flow(nodes: GraphNode[], edges: GraphEdge[]): { columns: FlowColumn[]; links: FlowLink[]; skipped: number } {
  const col = (n: GraphNode | undefined) => (n ? KIND_ORDER.indexOf(n.kind as (typeof KIND_ORDER)[number]) : -1);
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const columns = KIND_ORDER.map((kind) => {
    const ns = nodes.filter((n) => n.kind === kind);
    return { kind, total: ns.length, quarantined: ns.filter((n) => n.status === 'QUARANTINED').length };
  });
  const links = new Map<number, FlowLink>();
  let skipped = 0;
  for (const e of edges) {
    if (!CAUSAL.has(e.type)) continue;
    const a = byId.get(e.source);
    const b = byId.get(e.target);
    const i = col(a);
    const j = col(b);
    if (i < 0 || j < 0) continue;
    if (j !== i + 1) {
      skipped += 1;
      continue;
    }
    const l = links.get(i) ?? { from: i, to: j, total: 0, bad: 0 };
    l.total += 1;
    if (b?.status === 'QUARANTINED') l.bad += 1;
    links.set(i, l);
  }
  return { columns, links: [...links.values()].sort((x, y) => x.from - y.from), skipped };
}

export interface Bins {
  start: number;
  end: number;
  counts: number[];
  marks: { ts: number; kind: string }[];
}

/** Event density over time, plus the moments that changed the epoch. */
export function activityBins(
  log: { ts: number; type: string; reason?: string; step?: string }[],
  bins = 48,
): Bins | null {
  const real = log.filter((e) => e.type !== 'heartbeat');
  if (real.length === 0) return null;
  const start = real[0]?.ts ?? 0;
  const end = Math.max(real[real.length - 1]?.ts ?? start, start + 1000);
  const width = (end - start) / bins;
  const counts = new Array<number>(bins).fill(0);
  for (const e of real) counts[Math.min(bins - 1, Math.floor((e.ts - start) / width))]! += 1;
  const marks = real
    .filter((e) => e.type === 'epoch_changed' || (e.type === 'scenario_progress' && e.step === 'inject_poison'))
    .map((e) => ({ ts: e.ts, kind: e.type === 'epoch_changed' ? String(e.reason) : 'poison' }));
  return { start, end, counts, marks };
}

export function explain(node: GraphNode, adj: Adj, nodes: GraphNode[]): string[] {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const down = descendants(adj, node.id);
  const up = ancestors(adj, node.id);
  const downNodes = [...down].map((i) => byId.get(i)).filter((n): n is GraphNode => !!n);
  const actions = downNodes.filter((n) => n.kind === 'action');
  const ran = actions.filter((n) => n.action?.executed);
  const roots = [...up].filter((i) => (adj.inn.get(i)?.length ?? 0) === 0).map((i) => byId.get(i)?.label ?? i);
  const lines: string[] = [];
  const kind = node.is_decision ? 'decision' : node.kind;
  lines.push(
    `${node.label} is a ${kind}${node.asserted_by ? ` asserted by ${node.asserted_by.name}` : ''}${node.owner_team ? `, owned by ${node.owner_team}` : ''}.`,
  );
  if (roots.length) lines.push(`It traces back to ${roots.join(' and ')}.`);
  if (down.size === 0) lines.push('Nothing is built on it yet, so quarantining it would affect only itself.');
  else
    lines.push(
      `${down.size} node${down.size === 1 ? '' : 's'} are built on it, including ${actions.length} action${actions.length === 1 ? '' : 's'}${ran.length ? ` (${ran.length} already executed)` : ''}.`,
    );
  if (node.status === 'QUARANTINED') lines.push('It is quarantined, so no later retrieval returns it.');
  else if (node.status === 'SUPERSEDED') lines.push('It was replaced by a newer fact and is kept only as history.');
  else if (node.stale) lines.push('It is out of date: something it depends on was replaced.');
  return lines;
}
