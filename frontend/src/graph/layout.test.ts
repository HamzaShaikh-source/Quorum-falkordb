import { describe, expect, it } from 'vitest';
import { COL_X, freeSlot, GAP_Y, layoutAll } from './layout';
import type { GraphEdge, GraphNode } from '../api/schemas';

const n = (id: string, kind: GraphNode['kind']): GraphNode => ({ id, kind, label: id, status: 'HEALTHY', stale: false });

describe('layoutAll', () => {
  const nodes = [n('s', 'source'), n('f1', 'fact'), n('f2', 'fact'), n('c', 'conclusion'), n('a1', 'action'), n('a2', 'action')];
  const edges: GraphEdge[] = [
    { id: '1', source: 's', target: 'f1', type: 'FROM_SOURCE' },
    { id: '2', source: 'f1', target: 'c', type: 'LEADS_TO' },
    { id: '3', source: 'c', target: 'a1', type: 'LEADS_TO' },
    { id: '4', source: 'c', target: 'a2', type: 'LEADS_TO' },
  ];
  const pos = layoutAll(nodes, edges);

  it('puts each kind in its own column', () => {
    expect(pos.s?.x).toBe(COL_X[0]);
    expect(pos.f1?.x).toBe(COL_X[1]);
    expect(pos.c?.x).toBe(COL_X[2]);
    expect(pos.a1?.x).toBe(COL_X[3]);
  });
  it('never stacks two nodes of a column on top of each other', () => {
    expect(Math.abs((pos.a1?.y ?? 0) - (pos.a2?.y ?? 0))).toBeGreaterThanOrEqual(GAP_Y - 1);
  });
  it('aligns a source with the fact it feeds', () => {
    expect(pos.s?.y).toBe(pos.f1?.y);
  });
});

describe('freeSlot', () => {
  it('keeps the wanted row when free', () => expect(freeSlot([0, 200], 100)).toBe(100));
  it('moves to the nearest free row when taken', () => expect(freeSlot([0, GAP_Y], 0)).toBe(-GAP_Y));
});
