import { describe, expect, it } from 'vitest';
import { applyEvent, emptyCore, planEvents, type CoreState } from './reducer';
import { SseEvent, type GraphNode } from '../api/schemas';

const node = (id: string, over: Partial<GraphNode> = {}): GraphNode => ({
  id,
  kind: 'fact',
  label: id,
  status: 'HEALTHY',
  stale: false,
  ...over,
});

let seq = 0;
const ev = (type: SseEvent['type'], data: unknown, over: Partial<SseEvent> = {}): SseEvent =>
  ({ seq: ++seq, ts: 1000 + seq, epoch: 1, type, data, ...over }) as SseEvent;

const withNodes = (...ns: GraphNode[]): CoreState => ({
  ...emptyCore(),
  nodes: Object.fromEntries(ns.map((n) => [n.id, n])),
});

it('resyncs after reset instead of refetching a removed containment root', () => {
  const parsed = SseEvent.parse(ev('epoch_changed', { epoch: 1, reason: 'reset' }));
  const result = applyEvent(withNodes(node('removed')), parsed);
  expect(result.effects).toContainEqual({ kind: 'resync' });
  expect(result.effects.some((effect) => effect.kind === 'report')).toBe(false);
  expect(result.state.cascade).toBeNull();
});

describe('planEvents', () => {
  const e = (s: number) => ({ seq: s, ts: 0, epoch: 1, type: 'node_added', data: {} }) as unknown as SseEvent;
  it('orders and applies contiguous events', () => {
    const r = planEvents(4, [e(6), e(5)]);
    expect(r.apply.map((x) => x.seq)).toEqual([5, 6]);
    expect(r.gap).toBe(false);
  });
  it('ignores duplicates already applied', () => {
    expect(planEvents(5, [e(5), e(4)]).apply).toHaveLength(0);
  });
  it('keeps seq contiguous across an event type the console does not know', () => {
    const unknown = { seq: 6, ts: 0, epoch: 1, type: 'unknown', data: { original_type: 'correction_notice_created' } } as unknown as SseEvent;
    const r = planEvents(4, [e(5), unknown, e(7)]);
    expect(r.gap).toBe(false);
    expect(r.apply.map((x) => x.seq)).toEqual([5, 6, 7]);
    const next = applyEvent(emptyCore(), unknown);
    expect(next.state.nodes).toEqual({});
  });
  it('flags a gap', () => {
    const r = planEvents(4, [e(5), e(7)]);
    expect(r.apply.map((x) => x.seq)).toEqual([5]);
    expect(r.gap).toBe(true);
  });
  it('treats resync_required as a gap', () => {
    const r = planEvents(4, [{ seq: 0, ts: 0, epoch: 1, type: 'resync_required', data: {} } as SseEvent]);
    expect(r.gap).toBe(true);
  });
});

describe('applyEvent', () => {
  it('adds nodes and edges', () => {
    let s = emptyCore();
    s = applyEvent(s, ev('node_added', { node: node('a') })).state;
    s = applyEvent(s, ev('node_added', { node: node('b') })).state;
    s = applyEvent(s, ev('edge_added', { edge: { id: 'e', source: 'a', target: 'b', type: 'LEADS_TO' } })).state;
    expect(Object.keys(s.nodes)).toEqual(['a', 'b']);
    expect(Object.keys(s.edges)).toEqual(['e']);
  });

  it('schedules the quarantine wave at 120 ms per hop', () => {
    const s0 = withNodes(node('root'), node('kid'));
    const r1 = applyEvent(s0, ev('status_changed', { id: 'root', status: 'QUARANTINED', depth: 0, root_id: 'root' }));
    const r2 = applyEvent(r1.state, ev('status_changed', { id: 'kid', status: 'QUARANTINED', depth: 2, root_id: 'root' }));
    const d1 = r1.effects.find((x) => x.kind === 'delta');
    const d2 = r2.effects.find((x) => x.kind === 'delta');
    expect(d1 && d1.kind === 'delta' && d1.delays.root).toBe(0);
    expect(d2 && d2.kind === 'delta' && d2.delays.kid).toBe(240);
    expect(r2.state.cascade?.ids).toEqual(['root', 'kid']);
    expect(r2.state.nodes.kid?.status).toBe('QUARANTINED');
  });

  it('summarises a cascade on epoch_changed and asks for the report', () => {
    let s = withNodes(
      node('root'),
      node('act', {
        kind: 'action',
        action: { type: 'refund', reversibility: 'COMPENSABLE', executed: true, confirmed: true, gate: 'PASSED' },
      }),
    );
    s = applyEvent(s, ev('status_changed', { id: 'root', status: 'QUARANTINED', depth: 0, root_id: 'root' })).state;
    s = applyEvent(s, ev('status_changed', { id: 'act', status: 'QUARANTINED', depth: 1, root_id: 'root' })).state;
    const r = applyEvent(s, ev('epoch_changed', { epoch: 2, reason: 'quarantine' }));
    expect(r.state.epoch).toBe(2);
    expect(r.state.feed[1]?.text).toBe('Quarantined root: 2 nodes held, 1 action rolling back');
    expect(r.effects.some((x) => x.kind === 'report' && x.rootId === 'root')).toBe(true);
    expect(r.effects.some((x) => x.kind === 'toast')).toBe(true);
  });

  it('marks gate_blocked actions and queues a feed entry', () => {
    const s = withNodes(
      node('act', {
        kind: 'action',
        label: 'Email customers',
        action: { type: 'email', reversibility: 'IRREVERSIBLE', executed: false, confirmed: false, gate: 'PASSED' },
      }),
    );
    const r = applyEvent(s, ev('gate_blocked', { action_id: 'act', handoff_id: 'hand_1', reason: 'One uncorroborated fact' }));
    expect(r.state.nodes.act?.action?.gate).toBe('BLOCKED');
    expect(r.state.feed[0]?.tone).toBe('review');
  });

  it('records compensation progress on the node', () => {
    const s = withNodes(
      node('act', {
        kind: 'action',
        status: 'QUARANTINED',
        action: { type: 'refund', reversibility: 'COMPENSABLE', executed: true, confirmed: true, gate: 'PASSED' },
      }),
    );
    const r = applyEvent(s, ev('compensation_updated', { action_id: 'act', status: 'COMPENSATED', attempts: 1 }));
    expect(r.state.nodes.act?.action?.compensation).toEqual({ status: 'COMPENSATED', attempts: 1 });
  });

  it('ignores events for unknown nodes without throwing', () => {
    const r = applyEvent(emptyCore(), ev('status_changed', { id: 'ghost', status: 'QUARANTINED', depth: 0 }));
    expect(Object.keys(r.state.nodes)).toHaveLength(0);
  });

  it('keeps the latest seq and epoch', () => {
    const r = applyEvent(emptyCore(), ev('invariant_status', { ok: false, violations: 2 }, { seq: 99, epoch: 7 }));
    expect(r.state.seq).toBe(99);
    expect(r.state.epoch).toBe(7);
    expect(r.state.invariant).toEqual({ ok: false, violations: 2 });
  });
});
