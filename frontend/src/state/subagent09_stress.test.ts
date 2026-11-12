import { describe, expect, it } from 'vitest';
import { applyEvent, emptyCore, planEvents, type CoreState } from './reducer';
import { SseEvent, type GraphNode, type GraphEdge } from '../api/schemas';

const makeNode = (id: string, over: Partial<GraphNode> = {}): GraphNode => ({
  id,
  kind: 'fact',
  label: id,
  status: 'HEALTHY',
  stale: false,
  ...over,
});

describe('Subagent 09: Frontend State & Reducer Audit Suite', () => {
  describe('1. Out-of-Order Sequences & Gap Detection', () => {
    const mkEv = (s: number, type: SseEvent['type'] = 'node_added') =>
      ({
        seq: s,
        ts: 1000 + s,
        epoch: 1,
        type,
        data: { node: makeNode(`n_${s}`) },
      }) as unknown as SseEvent;

    it('receiving seq 15 before seq 12 (lastSeq=11) flags a gap with 0 applied', () => {
      const res = planEvents(11, [mkEv(15)]);
      expect(res.gap).toBe(true);
      expect(res.apply).toHaveLength(0);
    });

    it('correctly reorders scrambled batch [15, 12, 14, 13] without gap when lastSeq=11', () => {
      const batch = [mkEv(15), mkEv(12), mkEv(14), mkEv(13)];
      const res = planEvents(11, batch);
      expect(res.gap).toBe(false);
      expect(res.apply.map((e) => e.seq)).toEqual([12, 13, 14, 15]);
    });

    it('handles gap in the middle: [12, 15, 16] when lastSeq=11 applies 12 and flags gap', () => {
      const res = planEvents(11, [mkEv(12), mkEv(15), mkEv(16)]);
      expect(res.gap).toBe(true);
      expect(res.apply.map((e) => e.seq)).toEqual([12]);
    });

    it('ignores stale duplicate sequences already recorded (seq <= lastSeq)', () => {
      const batch = [mkEv(9), mkEv(10), mkEv(11), mkEv(12)];
      const res = planEvents(11, batch);
      expect(res.gap).toBe(false);
      expect(res.apply.map((e) => e.seq)).toEqual([12]);
    });

    it('immediately flags gap and clears apply on resync_required', () => {
      const resync = { seq: 0, ts: Date.now(), epoch: 1, type: 'resync_required', data: {} } as SseEvent;
      const res = planEvents(11, [mkEv(12), resync, mkEv(13)]);
      expect(res.gap).toBe(true);
      expect(res.apply).toHaveLength(0);
    });
  });

  describe('2. State Reducer Immutability & Status Transitions', () => {
    it('guarantees strict immutability of CoreState during node and edge mutations', () => {
      const initial: CoreState = emptyCore();
      Object.freeze(initial);
      Object.freeze(initial.nodes);
      Object.freeze(initial.edges);
      Object.freeze(initial.feed);
      Object.freeze(initial.eventLog);

      const n1 = makeNode('f1');
      const ev1: SseEvent = {
        seq: 1,
        ts: 1000,
        epoch: 1,
        type: 'node_added',
        data: { node: n1 },
      } as SseEvent;

      const r1 = applyEvent(initial, ev1);
      expect(r1.state).not.toBe(initial);
      expect(r1.state.nodes).not.toBe(initial.nodes);
      expect(r1.state.nodes.f1).toEqual(n1);

      const e1: GraphEdge = { id: 'e1', source: 'f1', target: 'f2', type: 'LEADS_TO' };
      const ev2: SseEvent = {
        seq: 2,
        ts: 1010,
        epoch: 1,
        type: 'edge_added',
        data: { edge: e1 },
      } as SseEvent;

      const r2 = applyEvent(r1.state, ev2);
      expect(r2.state.edges.e1).toEqual(e1);
      expect(r2.state.edges).not.toBe(r1.state.edges);
    });

    it('handles quarantine status transition with hop-wave delays and cascade root', () => {
      let state = emptyCore();
      state = applyEvent(state, {
        seq: 1,
        ts: 1000,
        epoch: 1,
        type: 'node_added',
        data: { node: makeNode('root_fact', { status: 'HEALTHY' }) },
      } as SseEvent).state;

      state = applyEvent(state, {
        seq: 2,
        ts: 1010,
        epoch: 1,
        type: 'node_added',
        data: { node: makeNode('leaf_action', { status: 'HEALTHY' }) },
      } as SseEvent).state;

      // Quarantine root at depth 0
      const q1 = applyEvent(state, {
        seq: 3,
        ts: 1020,
        epoch: 1,
        type: 'status_changed',
        data: { id: 'root_fact', status: 'QUARANTINED', depth: 0, root_id: 'root_fact' },
      } as SseEvent);

      expect(q1.state.nodes.root_fact?.status).toBe('QUARANTINED');
      expect(q1.state.nodes.root_fact?.quarantined_at).toBe(1020);
      expect(q1.state.nodes.root_fact?.quarantine_root).toBe('root_fact');
      expect(q1.state.cascade?.rootId).toBe('root_fact');
      expect(q1.state.cascade?.ids).toEqual(['root_fact']);

      const deltaEffect1 = q1.effects.find((e) => e.kind === 'delta');
      expect(deltaEffect1 && deltaEffect1.kind === 'delta' && deltaEffect1.delays.root_fact).toBe(0);

      // Quarantine leaf at depth 2 (240ms wave delay)
      const q2 = applyEvent(q1.state, {
        seq: 4,
        ts: 1030,
        epoch: 1,
        type: 'status_changed',
        data: { id: 'leaf_action', status: 'QUARANTINED', depth: 2, root_id: 'root_fact' },
      } as SseEvent);

      expect(q2.state.nodes.leaf_action?.status).toBe('QUARANTINED');
      expect(q2.state.cascade?.ids).toEqual(['root_fact', 'leaf_action']);
      const deltaEffect2 = q2.effects.find((e) => e.kind === 'delta');
      expect(deltaEffect2 && deltaEffect2.kind === 'delta' && deltaEffect2.delays.leaf_action).toBe(240);
    });

    it('handles restore status transition: clears quarantine_root and timestamps released_at', () => {
      let state = emptyCore();
      state = applyEvent(state, {
        seq: 1,
        ts: 1000,
        epoch: 1,
        type: 'node_added',
        data: { node: makeNode('fact_1', { status: 'QUARANTINED', quarantine_root: 'root' }) },
      } as SseEvent).state;

      const restored = applyEvent(state, {
        seq: 2,
        ts: 2000,
        epoch: 2,
        type: 'status_changed',
        data: { id: 'fact_1', status: 'HEALTHY' },
      } as SseEvent);

      expect(restored.state.nodes.fact_1?.status).toBe('HEALTHY');
      expect(restored.state.nodes.fact_1?.quarantine_root).toBeNull();
      expect(restored.state.nodes.fact_1?.released_at).toBe(2000);
    });

    it('handles scenario reset: clears feed, eventLog, cascade, claimRace, and triggers resync', () => {
      let state = emptyCore();
      state = applyEvent(state, {
        seq: 1,
        ts: 1000,
        epoch: 1,
        type: 'node_added',
        data: { node: makeNode('f1') },
      } as SseEvent).state;

      const resetProgress = applyEvent(state, {
        seq: 2,
        ts: 1050,
        epoch: 2,
        type: 'scenario_progress',
        data: { run_id: 'run_123', step: 'reset', state: 'finished' },
      } as SseEvent);

      expect(resetProgress.state.feed).toHaveLength(0);
      expect(resetProgress.state.eventLog).toHaveLength(0);
      expect(resetProgress.state.cascade).toBeNull();

      const epochReset = applyEvent(resetProgress.state, {
        seq: 3,
        ts: 1060,
        epoch: 2,
        type: 'epoch_changed',
        data: { epoch: 2, reason: 'reset' },
      } as SseEvent);

      expect(epochReset.effects.some((e) => e.kind === 'resync')).toBe(true);
    });
  });
});
