import { describe, expect, it } from 'vitest';
import cytoscape from 'cytoscape';
import type { GraphEdge, GraphNode } from '../api/schemas';
import { wrapNodeLabel } from './adapter';
import { COL_X, GAP_Y, layoutAll } from './layout';

describe('Massive Graph Stress Test (500 nodes, 1000 edges)', () => {
  it('computes layoutAll for 500 nodes and 1000 edges within performance budget', () => {
    const kinds: GraphNode['kind'][] = ['source', 'fact', 'conclusion', 'action'];
    const nodes: GraphNode[] = [];
    for (let i = 0; i < 500; i++) {
      const kind = kinds[i % kinds.length]!;
      nodes.push({
        id: `node_${i}`,
        kind,
        label: `Node ${i} with a reasonable label`,
        status: i % 10 === 0 ? 'QUARANTINED' : i % 7 === 0 ? 'SUPERSEDED' : 'HEALTHY',
        stale: i % 15 === 0,
        is_decision: kind === 'conclusion' && i % 4 === 0,
        action: kind === 'action' ? {
          type: 'webhook',
          reversibility: 'REVERSIBLE',
          executed: i % 2 === 0,
          confirmed: i % 4 === 0,
          gate: i % 6 === 0 ? 'BLOCKED' : 'PASSED',
        } : undefined,
      });
    }

    const edgeTypes: GraphEdge['type'][] = ['LEADS_TO', 'SUPPORTS', 'CONTRADICTS', 'SUPERSEDES', 'FROM_SOURCE'];
    const edges: GraphEdge[] = [];
    for (let i = 0; i < 1000; i++) {
      const srcIdx = i % 500;
      const tgtIdx = (i * 3 + 1) % 500;
      edges.push({
        id: `edge_${i}`,
        source: `node_${srcIdx}`,
        target: `node_${tgtIdx}`,
        type: edgeTypes[i % edgeTypes.length]!,
      });
    }

    const start = performance.now();
    const pos = layoutAll(nodes, edges);
    const duration = performance.now() - start;

    expect(Object.keys(pos).length).toBe(500);
    expect(duration).toBeLessThan(100); // Must be fast (< 100ms)

    // Verify all nodes have finite positions
    for (const n of nodes) {
      const p = pos[n.id];
      expect(p).toBeDefined();
      expect(Number.isFinite(p!.x)).toBe(true);
      expect(Number.isFinite(p!.y)).toBe(true);
    }

    // Verify column separation and no overlapping within columns
    for (let col = 0; col < 4; col++) {
      const colNodes = nodes.filter((n) => COL_X[col] === pos[n.id]?.x);
      const ys = colNodes.map((n) => pos[n.id]!.y).sort((a, b) => a - b);
      for (let k = 0; k < ys.length - 1; k++) {
        const diff = ys[k + 1]! - ys[k]!;
        expect(diff).toBeGreaterThanOrEqual(GAP_Y - 1);
      }
    }
  });

  it('renders 500 nodes and 1000 edges in Cytoscape without memory corruption or crash', () => {
    const kinds: GraphNode['kind'][] = ['source', 'fact', 'conclusion', 'action'];
    const nodes: GraphNode[] = [];
    for (let i = 0; i < 500; i++) {
      nodes.push({
        id: `n_${i}`,
        kind: kinds[i % 4]!,
        label: `Label ${i}`,
        status: 'HEALTHY',
        stale: false,
      });
    }
    const edges: GraphEdge[] = [];
    for (let i = 0; i < 1000; i++) {
      edges.push({
        id: `e_${i}`,
        source: `n_${i % 500}`,
        target: `n_${(i + 1) % 500}`,
        type: 'LEADS_TO',
      });
    }
    const pos = layoutAll(nodes, edges);

    const cy = cytoscape({
      headless: true,
      elements: [],
    });

    cy.batch(() => {
      cy.add(nodes.map((n) => ({
        group: 'nodes' as const,
        data: { id: n.id, label: n.label, kind: n.kind, status: n.status },
        position: pos[n.id] ?? { x: 0, y: 0 },
      })));
      cy.add(edges.map((e) => ({
        group: 'edges' as const,
        data: { id: e.id, source: e.source, target: e.target, type: e.type },
      })));
    });

    expect(cy.nodes().length).toBe(500);
    expect(cy.edges().length).toBe(1000);
    cy.destroy();
  });
});

describe('Boundary & Edge Cases', () => {
  it('handles completely disconnected orphan nodes with no edges', () => {
    const orphans: GraphNode[] = [
      { id: 'orphan_s', kind: 'source', label: 'Orphan Source', status: 'HEALTHY', stale: false },
      { id: 'orphan_f', kind: 'fact', label: 'Orphan Fact', status: 'HEALTHY', stale: false },
      { id: 'orphan_c', kind: 'conclusion', label: 'Orphan Conclusion', status: 'HEALTHY', stale: false },
      { id: 'orphan_a', kind: 'action', label: 'Orphan Action', status: 'HEALTHY', stale: false },
    ];
    const pos = layoutAll(orphans, []);
    expect(pos.orphan_s?.x).toBe(COL_X[0]);
    expect(pos.orphan_f?.x).toBe(COL_X[1]);
    expect(pos.orphan_c?.x).toBe(COL_X[2]);
    expect(pos.orphan_a?.x).toBe(COL_X[3]);
    for (const n of orphans) {
      expect(Number.isFinite(pos[n.id]?.y)).toBe(true);
    }
  });

  it('handles self-loop edges without infinite loop or NaN', () => {
    const selfLoopNode: GraphNode = {
      id: 'loop_c',
      kind: 'conclusion',
      label: 'Self looper',
      status: 'HEALTHY',
      stale: false,
    };
    const edges: GraphEdge[] = [
      { id: 'self_1', source: 'loop_c', target: 'loop_c', type: 'LEADS_TO' },
    ];
    const pos = layoutAll([selfLoopNode], edges);
    expect(Number.isFinite(pos.loop_c?.x)).toBe(true);
    expect(Number.isFinite(pos.loop_c?.y)).toBe(true);
    expect(pos.loop_c?.y).toBe(0);
  });

  it('handles empty and whitespace node labels cleanly', () => {
    expect(wrapNodeLabel('')).toBe('');
    expect(wrapNodeLabel('   ')).toBe('');
    expect(wrapNodeLabel('\t\n  ')).toBe('');
  });

  it('handles ultra-long unbroken alphanumeric tokens without border bleeding', () => {
    const unbroken100 = 'A'.repeat(100);
    const wrapped = wrapNodeLabel(unbroken100, 14, 3);
    const lines = wrapped.split('\n');
    expect(lines.length).toBeLessThanOrEqual(3);
    for (const line of lines) {
      expect(line.length).toBeLessThanOrEqual(14);
    }
    expect(lines[lines.length - 1]?.endsWith('...')).toBe(true);
  });

  it('handles 1000-character mixed long tokens without crashing or exceeding line limits', () => {
    const longString = 'SuperLongWordThatNeverEnds_UntilItReaches_SomeLimit-OrDot.OrSlash/AndKeepsGoingOnAndOn'.repeat(12);
    const wrapped = wrapNodeLabel(longString, 14, 3);
    const lines = wrapped.split('\n');
    expect(lines.length).toBeLessThanOrEqual(3);
    for (const line of lines) {
      expect(line.length).toBeLessThanOrEqual(14);
    }
  });

  it('never exceeds maxLineLen on any line for any combination of words', () => {
    const testCases = [
      'a'.repeat(14),
      'a'.repeat(15),
      'a'.repeat(13),
      'a_'.repeat(10),
      'a-b-c-d-e-f-g-h-i-j-k-l-m-n-o-p',
      'hello world this is a normal test string',
      'https://api.github.com/repos/owner/repo/pulls/123/comments',
      '{"key": "value", "id": 1234567890}',
    ];
    for (const tc of testCases) {
      const wrapped = wrapNodeLabel(tc, 14, 3);
      const lines = wrapped.split('\n');
      expect(lines.length).toBeLessThanOrEqual(3);
      for (const line of lines) {
        expect(line.length).toBeLessThanOrEqual(14);
      }
    }
  });
});

describe('Minimap coordinate calculation edge cases', () => {
  it('scales correctly for single node without division by zero', () => {
    const pad = 90;
    const xs = [100];
    const ys = [200];
    const minX = Math.min(...xs) - pad;
    const maxX = Math.max(...xs) + pad;
    const minY = Math.min(...ys) - pad;
    const maxY = Math.max(...ys) + pad;
    const W = 176;
    const H = 112;
    const sc = Math.min(W / (maxX - minX), H / (maxY - minY));
    expect(Number.isFinite(sc)).toBe(true);
    expect(sc).toBeGreaterThan(0);
  });

  it('computes correct viewport box scaling when zoomed in or out', () => {
    const W = 176;
    const H = 112;
    const pad = 90;
    const xs = [0, 618];
    const ys = [0, 500];
    const minX = Math.min(...xs) - pad;
    const maxX = Math.max(...xs) + pad;
    const minY = Math.min(...ys) - pad;
    const maxY = Math.max(...ys) + pad;
    const sc = Math.min(W / (maxX - minX), H / (maxY - minY));
    const ox = (W - (maxX - minX) * sc) / 2;
    const oy = (H - (maxY - minY) * sc) / 2;

    const px = (x: number) => ox + (x - minX) * sc;
    const py = (y: number) => oy + (y - minY) * sc;

    // Viewport box in model coordinates
    const v = { x: 0, y: 0, w: 1000, h: 800 };
    const boxX = px(v.x);
    const boxY = py(v.y);
    const boxW = Math.max(8, v.w * sc);
    const boxH = Math.max(6, v.h * sc);

    expect(Number.isFinite(boxX)).toBe(true);
    expect(Number.isFinite(boxY)).toBe(true);
    expect(boxW).toBeGreaterThan(0);
    expect(boxH).toBeGreaterThan(0);
  });
});
