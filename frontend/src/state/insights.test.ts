import { describe, expect, it } from 'vitest';
import {
  activityBins,
  adjacency,
  ancestors,
  communities,
  degree,
  descendants,
  explain,
  flow,
  godNodes,
  neighborhood,
  shortestPath,
  statusCounts,
} from './insights';
import type { GraphEdge, GraphNode } from '../api/schemas';

const n = (id: string, kind: GraphNode['kind'], over: Partial<GraphNode> = {}): GraphNode => ({
  id,
  kind,
  label: id,
  status: 'HEALTHY',
  stale: false,
  ...over,
});
const e = (source: string, target: string, type: GraphEdge['type'] = 'LEADS_TO'): GraphEdge => ({ id: `${source}-${target}`, source, target, type });

// s1 -> f1 -> c1 -> a1, a2 ;  s2 -> f2 (separate community)
const nodes = [
  n('s1', 'source'),
  n('f1', 'fact'),
  n('c1', 'conclusion'),
  n('a1', 'action', { action: { type: 'refund', reversibility: 'COMPENSABLE', executed: true, confirmed: true, gate: 'PASSED' } }),
  n('a2', 'action'),
  n('s2', 'source'),
  n('f2', 'fact', { status: 'QUARANTINED' }),
];
const edges = [e('s1', 'f1', 'FROM_SOURCE'), e('f1', 'c1'), e('c1', 'a1'), e('c1', 'a2'), e('s2', 'f2', 'FROM_SOURCE'), e('f2', 'f1', 'CONTRADICTS')];
const adj = adjacency(nodes, edges);

describe('graph insights', () => {
  it('ignores non-causal edges', () => {
    expect(degree(adj, 'f2')).toBe(1);
    expect(adj.out.get('f2')).toEqual([]);
  });
  it('finds descendants and ancestors', () => {
    expect([...descendants(adj, 'f1')].sort()).toEqual(['a1', 'a2', 'c1']);
    expect([...ancestors(adj, 'a1')].sort()).toEqual(['c1', 'f1', 's1']);
  });
  it('builds a local graph by hop depth', () => {
    expect([...neighborhood(adj, 'c1', 1).keys()].sort()).toEqual(['a1', 'a2', 'c1', 'f1']);
    expect(neighborhood(adj, 'c1', 2).has('s1')).toBe(true);
    expect(neighborhood(adj, 'c1', 3).has('s2')).toBe(false);
  });
  it('finds the shortest path and reports none across communities', () => {
    expect(shortestPath(adj, 's1', 'a2')).toEqual(['s1', 'f1', 'c1', 'a2']);
    expect(shortestPath(adj, 's1', 's2')).toBeNull();
    expect(shortestPath(adj, 's1', 's1')).toEqual(['s1']);
  });
  it('ranks god nodes by what is built on them', () => {
    const g = godNodes(nodes, adj, 3);
    expect(g[0]?.id).toBe('s1');
    expect(g[0]?.downstream).toBe(4);
  });
  it('splits the graph into communities named by their sources', () => {
    const c = communities(nodes, adj);
    expect(c).toHaveLength(2);
    expect(c[0]?.ids).toHaveLength(5);
    expect(c[0]?.roots).toEqual(['s1']);
    expect(c[1]?.quarantined).toBe(1);
  });
  it('counts statuses', () => {
    expect(statusCounts(nodes)).toMatchObject({ total: 7, quarantined: 1, healthy: 6 });
  });
  it('summarises belief flow between neighbouring kinds', () => {
    const f = flow(nodes, edges);
    expect(f.columns.map((c) => c.total)).toEqual([2, 2, 1, 2]);
    expect(f.links.find((l) => l.from === 0)?.total).toBe(2);
    expect(f.links.find((l) => l.from === 0)?.bad).toBe(1);
  });
  it('bins activity and marks epoch changes', () => {
    const b = activityBins([
      { ts: 1000, type: 'node_added' },
      { ts: 1500, type: 'node_added' },
      { ts: 3000, type: 'epoch_changed', reason: 'quarantine' },
      { ts: 3100, type: 'heartbeat' },
    ])!;
    expect(b.counts.reduce((a, c) => a + c, 0)).toBe(3);
    expect(b.marks).toEqual([{ ts: 3000, kind: 'quarantine' }]);
    expect(activityBins([])).toBeNull();
  });
  it('explains a node in plain sentences', () => {
    const lines = explain(nodes[1] as GraphNode, adj, nodes);
    expect(lines.join(' ')).toContain('3 nodes are built on it');
    expect(lines.join(' ')).toContain('1 already executed');
    expect(lines.join(' ')).toContain('traces back to s1');
  });
});
