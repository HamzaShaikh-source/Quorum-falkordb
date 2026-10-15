import type { GraphEdge, GraphNode } from '../api/schemas';
import { adjacency, ancestors, communities, godNodes, type Adj } from '../state/insights';
import { estimateTokens } from './tokens';

export interface Graph {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

/* ---------- baselines: what an agent gets today without Quorum ---------- */

/** Everything the API knows, pasted as JSON. The honest worst case. */
export function rawDump(g: Graph): string {
  return JSON.stringify({ nodes: g.nodes, edges: g.edges }, null, 2);
}

/** A tidy prose memory: every node as a sentence, every edge as a sentence. What a "notes" memory feeds a prompt. */
export function proseHistory(g: Graph): string {
  const byId = new Map(g.nodes.map((n) => [n.id, n]));
  const lines = g.nodes.map(
    (n) =>
      `${n.kind[0]!.toUpperCase()}${n.kind.slice(1)} "${n.label}" (${n.id}) is ${n.status.toLowerCase()}${n.text ? `: ${n.text}` : ''}${
        n.asserted_by ? ` Asserted by ${n.asserted_by.name}.` : ''
      }`,
  );
  const links = g.edges.map(
    (e) => `${byId.get(e.source)?.label ?? e.source} ${String(e.type).toLowerCase().replace('_', ' ')} ${byId.get(e.target)?.label ?? e.target}.`,
  );
  return [...lines, ...links].join('\n');
}

/**
 * The fair baseline: a typical top-k retriever. It ranks every node by keyword overlap (it cannot tell healthy from
 * quarantined), then pastes the top 8 as full JSON chunks. This is what most RAG memories send per question.
 */
export function ragTopK(g: Graph, query: string, k = 8): GraphNode[] {
  const qw = new Set(query.toLowerCase().match(/[a-z0-9]{2,}/g) ?? []);
  return g.nodes
    .map((n) => ({ n, s: (`${n.label} ${n.text ?? ''} ${n.key ?? ''}`.toLowerCase().match(/[a-z0-9]{2,}/g) ?? []).filter((w) => qw.has(w)).length }))
    .filter((x) => x.s > 0)
    .sort((a, b) => b.s - a.s)
    .slice(0, k)
    .map((x) => x.n);
}

export function ragBaseline(g: Graph, query: string, k = 8): number {
  return estimateTokens(JSON.stringify(ragTopK(g, query, k), null, 2));
}

/* ---------- Quorum formats ---------- */

const KIND_CODE: Record<string, string> = { source: 'S', fact: 'F', conclusion: 'C', action: 'A' };
const clip = (s: string, n: number) => (s.length > n ? `${s.slice(0, n - 1).trimEnd()}…` : s);

/** Compact short ids: F1, C3. Stable for a given node order, shared by the map and the packed context. */
export function shortIds(nodes: GraphNode[]): Map<string, string> {
  const counters: Record<string, number> = {};
  const out = new Map<string, string>();
  for (const n of nodes) {
    const k = KIND_CODE[n.kind] ?? 'N';
    counters[k] = (counters[k] ?? 0) + 1;
    out.set(n.id, `${k}${counters[k]}`);
  }
  return out;
}

export interface MapArea {
  name: string;
  size: number;
  counts: Record<string, number>;
  hub: string | null;
  quarantined: number;
  ids: string[];
}

export function mapAreas(g: Graph, adj: Adj): MapArea[] {
  const byId = new Map(g.nodes.map((n) => [n.id, n]));
  const gods = godNodes(g.nodes, adj, g.nodes.length);
  return communities(g.nodes, adj).map((c) => {
    const counts: Record<string, number> = { S: 0, F: 0, C: 0, A: 0 };
    for (const id of c.ids) {
      const k = KIND_CODE[byId.get(id)?.kind ?? ''] ?? 'F';
      counts[k] = (counts[k] ?? 0) + 1;
    }
    const hub = gods.find((x) => c.ids.includes(x.id))?.id ?? null;
    return { name: c.roots.slice(0, 2).join(' + ') || 'unrooted', size: c.ids.length, counts, hub, quarantined: c.quarantined, ids: c.ids };
  });
}

/**
 * The map: one small document an agent reads first. It says where everything lives so the agent can fetch
 * single nodes by id instead of loading the whole graph. Modeled on a repo index, not on the data itself.
 */
export function buildMap(g: Graph, meta: { tenant: string; epoch: number }): string {
  const adj = adjacency(g.nodes, g.edges);
  const ids = shortIds(g.nodes);
  const byId = new Map(g.nodes.map((n) => [n.id, n]));
  const healthy = g.nodes.filter((n) => n.status === 'HEALTHY' && !n.stale).length;
  const bad = g.nodes.filter((n) => n.status === 'QUARANTINED').length;
  const lines = [
    `# quorum map · ${meta.tenant} · epoch ${meta.epoch}`,
    `${g.nodes.length} nodes (${healthy} healthy, ${bad} quarantined) · ${g.edges.length} links`,
    '',
    '## areas',
  ];
  for (const c of mapAreas(g, adj).slice(0, 12)) {
    lines.push(
      `- ${clip(c.name, 48)}: S${c.counts.S} F${c.counts.F} C${c.counts.C} A${c.counts.A}${c.hub ? ` hub ${ids.get(c.hub)}` : ''}${c.quarantined ? ` ⚠${c.quarantined} bad` : ''}`,
    );
  }
  lines.push('', '## hubs (most downstream)');
  for (const gn of godNodes(g.nodes, adj, 5)) lines.push(`- ${ids.get(gn.id)} ${clip(byId.get(gn.id)?.label ?? '', 44)} → ${gn.downstream}`);
  lines.push('', '## how to ask', '- retrieve(query,k) → healthy facts + sources', '- node(id) → one node + parents', '- path(a,b) → shortest chain', '- delta(epoch) → what changed since your epoch');
  return lines.join('\n');
}

const words = (s: string) => s.toLowerCase().match(/[a-z0-9]{2,}/g) ?? [];

export interface PackOptions {
  query: string;
  budget: number;
  depth?: number;
  /** epoch + node ids the agent already holds; when given, only changes are returned */
  have?: { epoch: number; ids: string[] };
}

export interface Stage {
  label: string;
  tokens: number;
  note: string;
}

export interface Packed {
  text: string;
  tokens: number;
  included: string[];
  dropped: number;
  truncated: boolean;
  delta: { added: number; removed: number } | null;
  stages: Stage[];
}

function line(n: GraphNode, ids: Map<string, string>, adj: Adj): string {
  const parents = (adj.inn.get(n.id) ?? []).map((p) => ids.get(p) ?? p).join(',');
  const body = n.text && n.text !== n.label ? `|${clip(n.text, 110)}` : '';
  return `${ids.get(n.id)}|${clip(n.label, 60)}${body}${parents ? `|<${parents}` : ''}`;
}

/**
 * Builds the smallest context that still answers the query: healthy nodes only, ranked by relevance, plus the
 * ancestors that prove where each one came from, in a one-line-per-node format with short ids, trimmed to a budget.
 * `stages` records the token count after each technique so the savings are attributable and checkable.
 */
export function pack(g: Graph, o: PackOptions): Packed {
  const depth = o.depth ?? 2;
  const adj = adjacency(g.nodes, g.edges);
  const ids = shortIds(g.nodes);
  const stages: Stage[] = [];
  stages.push({ label: 'Raw graph JSON', tokens: estimateTokens(rawDump(g)), note: 'every field of every node and link' });

  const live = g.nodes.filter((n) => n.status === 'HEALTHY' && !n.stale);
  const liveSet = new Set(live.map((n) => n.id));
  const liveEdges = g.edges.filter((e) => liveSet.has(e.source) && liveSet.has(e.target));
  stages.push({
    label: 'Healthy only',
    tokens: estimateTokens(rawDump({ nodes: live, edges: liveEdges })),
    note: `${g.nodes.length - live.length} quarantined, superseded or stale nodes never enter`,
  });

  const qw = new Set(words(o.query));
  const score = new Map<string, number>();
  for (const n of live) {
    let s = 0;
    for (const w of words(`${n.label} ${n.text ?? ''} ${n.key ?? ''} ${n.value ?? ''}`)) if (qw.has(w)) s += 1;
    if (s > 0 && (n.kind === 'fact' || n.kind === 'conclusion')) s += 0.2;
    if (s > 0) score.set(n.id, s);
  }
  const seeds = [...score.entries()].sort((a, b) => b[1] - a[1]).map(([id]) => id);
  const picked = new Set<string>();
  const order: string[] = [];
  const add = (id: string) => {
    if (liveSet.has(id) && !picked.has(id)) {
      picked.add(id);
      order.push(id);
    }
  };
  for (const s of seeds.slice(0, 8)) {
    add(s);
    [...ancestors(adj, s)].filter((a) => liveSet.has(a)).slice(0, depth * 3).forEach(add);
  }
  const byId = new Map(g.nodes.map((n) => [n.id, n]));
  const relevant = order.map((id) => byId.get(id)).filter((n): n is GraphNode => !!n);
  stages.push({
    label: 'Relevant slice',
    tokens: estimateTokens(rawDump({ nodes: relevant, edges: liveEdges.filter((e) => picked.has(e.source) && picked.has(e.target)) })),
    note: `${relevant.length} nodes matched the query or prove where a match came from`,
  });

  let chosen = relevant;
  let delta: Packed['delta'] = null;
  if (o.have) {
    const had = new Set(o.have.ids);
    const now = new Set(relevant.map((n) => n.id));
    const added = relevant.filter((n) => !had.has(n.id));
    const removed = o.have.ids.filter((id) => !now.has(id));
    delta = { added: added.length, removed: removed.length };
    chosen = added;
    stages.push({ label: 'Delta since your epoch', tokens: estimateTokens(added.map((n) => line(n, ids, adj)).join('\n')), note: `${removed.length} removed, ${added.length} new` });
  }

  const lines = chosen.map((n) => {
    const text = line(n, ids, adj);
    return { id: n.id, text, body: text.split('|').slice(1).join('|'), t: estimateTokens(text) + 1 };
  });
  const seen = new Set<string>();
  const unique = lines.filter((l) => {
    if (seen.has(l.body)) return false;
    seen.add(l.body);
    return true;
  });
  let used = 24;
  let dropped = lines.length - unique.length;
  const kept: typeof unique = [];
  for (const l of unique) {
    if (used + l.t > o.budget) {
      dropped++;
      continue;
    }
    used += l.t;
    kept.push(l);
  }
  const head = o.have ? `# delta · ${delta?.added ?? 0} new, ${delta?.removed ?? 0} gone` : `# context · "${clip(o.query, 40)}"`;
  const text = [head, ...kept.map((l) => l.text)].join('\n');
  const tokens = estimateTokens(text);
  stages.push({ label: 'Compact lines + budget', tokens, note: `one line per node, short ids, within ${o.budget} tokens, ${dropped} dropped` });
  return { text, tokens, included: kept.map((k) => k.id), dropped, truncated: dropped > 0, delta, stages };
}

export interface Savings {
  rawTokens: number;
  proseTokens: number;
  ragTokens: number;
  vsRag: number;
  packedTokens: number;
  mapTokens: number;
  vsRaw: number;
  vsProse: number;
}

export function savings(g: Graph, packed: Packed, meta: { tenant: string; epoch: number }, query = ''): Savings {
  const rawTokens = estimateTokens(rawDump(g));
  const proseTokens = estimateTokens(proseHistory(g));
  const mapTokens = estimateTokens(buildMap(g, meta));
  const total = packed.tokens + mapTokens;
  const ragTokens = ragBaseline(g, query);
  return { rawTokens, proseTokens, ragTokens, vsRag: ragTokens - packed.tokens, packedTokens: packed.tokens, mapTokens, vsRaw: rawTokens - total, vsProse: proseTokens - total };
}
