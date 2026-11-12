import { describe, expect, it } from 'vitest';
import { diffGraphs } from './diff';
import type { GraphNode } from '../api/schemas';

const n = (id: string, status: GraphNode['status'] = 'HEALTHY'): GraphNode => ({
  id,
  kind: 'fact',
  label: id,
  status,
  stale: false,
});

describe('diffGraphs', () => {
  it('finds added, removed, quarantined and replaced nodes', () => {
    const a = [n('keep'), n('gone'), n('bad'), n('old')];
    const b = [n('keep'), n('bad', 'QUARANTINED'), n('old', 'SUPERSEDED'), n('new')];
    const d = diffGraphs(a, b);
    expect(d.added.map((x) => x.id)).toEqual(['new']);
    expect(d.removed.map((x) => x.id)).toEqual(['gone']);
    expect(d.quarantined.map((x) => x.id)).toEqual(['bad']);
    expect(d.replaced.map((x) => x.id)).toEqual(['old']);
  });

  it('does not recount a node that was already quarantined', () => {
    const a = [n('x', 'QUARANTINED')];
    expect(diffGraphs(a, a).quarantined).toHaveLength(0);
  });
});
