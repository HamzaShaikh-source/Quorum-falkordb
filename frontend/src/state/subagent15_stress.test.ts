import { describe, expect, it } from 'vitest';
import { applyEvent, emptyCore, planEvents, type CoreState } from './reducer';
import { SseEvent, type GraphNode, type GraphEdge } from '../api/schemas';

const node = (id: string, over: Partial<GraphNode> = {}): GraphNode => ({
  id,
  kind: 'fact',
  label: id,
  status: 'HEALTHY',
  stale: false,
  ...over,
});

describe('Subagent 15: Multi-Vector Stress & Boundary Tests', () => {
  describe('Vector 1: Out-of-order Sequence & Gap Detection', () => {
    const mkEv = (s: number, type: SseEvent['type'] = 'node_added') =>
      ({ seq: s, ts: 1000 + s, epoch: 1, type, data: { node: node(`node_${s}`) } }) as unknown as SseEvent;

    it('handles receiving seq 15 before seq 12 (lastSeq=11)', () => {
      // Receive seq 15 when expecting 12
      const res = planEvents(11, [mkEv(15)]);
      expect(res.gap).toBe(true);
      expect(res.apply).toHaveLength(0);
    });

    it('correctly reorders out-of-order batch [15, 12, 14, 13] when lastSeq=11', () => {
      const batch = [mkEv(15), mkEv(12), mkEv(14), mkEv(13)];
      const res = planEvents(11, batch);
      expect(res.gap).toBe(false);
      expect(res.apply.map((e) => e.seq)).toEqual([12, 13, 14, 15]);
    });

    it('applies contiguous subset up to the first missing seq and flags gap', () => {
      // 12, 13 are present, 14 is missing, 15 is present
      const batch = [mkEv(12), mkEv(15), mkEv(13)];
      const res = planEvents(11, batch);
      expect(res.gap).toBe(true);
      expect(res.apply.map((e) => e.seq)).toEqual([12, 13]);
    });

    it('drops duplicates and stale sequences strictly <= lastSeq', () => {
      const batch = [mkEv(10), mkEv(11), mkEv(12), mkEv(12), mkEv(13)];
      const res = planEvents(11, batch);
      expect(res.gap).toBe(false);
      expect(res.apply.map((e) => e.seq)).toEqual([12, 13]);
    });

    it('filters out heartbeats without considering them sequence gaps', () => {
      const heartbeat = { seq: 0, ts: 1000, epoch: 1, type: 'heartbeat', data: {} } as SseEvent;
      const res = planEvents(5, [heartbeat, mkEv(6), mkEv(7)]);
      expect(res.gap).toBe(false);
      expect(res.apply.map((e) => e.seq)).toEqual([6, 7]);
    });

    it('flags gap immediately if resync_required is in incoming', () => {
      const resync = { seq: 0, ts: 1000, epoch: 1, type: 'resync_required', data: {} } as SseEvent;
      const res = planEvents(5, [mkEv(6), resync, mkEv(7)]);
      expect(res.gap).toBe(true);
      expect(res.apply).toHaveLength(0);
    });
  });

  describe('Vector 2: Malformed & Boundary SSE Events', () => {
    it('rejects events missing required base fields (seq, epoch, ts)', () => {
      const missingSeq = { ts: 1000, epoch: 1, type: 'status_changed', data: { id: 'x', status: 'QUARANTINED' } };
      expect(SseEvent.safeParse(missingSeq).success).toBe(false);

      const missingEpoch = { seq: 1, ts: 1000, type: 'status_changed', data: { id: 'x', status: 'QUARANTINED' } };
      expect(SseEvent.safeParse(missingEpoch).success).toBe(false);

      const missingTs = { seq: 1, epoch: 1, type: 'status_changed', data: { id: 'x', status: 'QUARANTINED' } };
      expect(SseEvent.safeParse(missingTs).success).toBe(false);
    });

    it('handles unexpected/unknown event types gracefully via unknown wrapper', () => {
      const unknownRaw = {
        seq: 12,
        ts: 2000,
        epoch: 1,
        type: 'quantum_entanglement_detected',
        data: { arbitrary: 'payload', value: 42 },
      };
      // Parsing directly fails discriminatedUnion
      const r = SseEvent.safeParse(unknownRaw);
      expect(r.success).toBe(false);

      // Wrapping with type: 'unknown' succeeds and preserves sequence
      const wrapped = SseEvent.safeParse({
        ...unknownRaw,
        type: 'unknown',
        data: { original_type: unknownRaw.type },
      });
      expect(wrapped.success).toBe(true);
      if (wrapped.success) {
        const next = applyEvent(emptyCore(), wrapped.data);
        expect(next.state.seq).toBe(12);
        expect(next.effects).toHaveLength(0);
      }
    });

    it('handles malformed status_changed with missing node without crashing', () => {
      const s0 = emptyCore();
      const ev = {
        seq: 1,
        ts: 1000,
        epoch: 1,
        type: 'status_changed' as const,
        data: { id: 'non_existent_node_id', status: 'QUARANTINED' as const },
      };
      const res = applyEvent(s0, ev);
      expect(res.state.nodes).toEqual({});
      expect(res.effects).toHaveLength(0);
    });

    it('handles gate_blocked, action_executed, compensation_updated for missing node or missing action', () => {
      const s0 = emptyCore();
      const evGate = {
        seq: 1,
        ts: 1000,
        epoch: 1,
        type: 'gate_blocked' as const,
        data: { action_id: 'missing_act', handoff_id: 'h1', reason: 'unverified' },
      };
      const r1 = applyEvent(s0, evGate);
      expect(r1.state.nodes).toEqual({});

      const evExec = {
        seq: 2,
        ts: 1001,
        epoch: 1,
        type: 'action_executed' as const,
        data: { action_id: 'missing_act', confirmed: true },
      };
      const r2 = applyEvent(s0, evExec);
      expect(r2.state.nodes).toEqual({});

      const evComp = {
        seq: 3,
        ts: 1002,
        epoch: 1,
        type: 'compensation_updated' as const,
        data: { action_id: 'missing_act', status: 'COMPENSATED' as const, attempts: 1 },
      };
      const r3 = applyEvent(s0, evComp);
      expect(r3.state.nodes).toEqual({});
    });
  });

  describe('Vector 3: State Immutability & Deep Object Integrity', () => {
    it('preserves immutability of prev CoreState during status updates', () => {
      const initialNode = node('n1', { status: 'HEALTHY' });
      const prevState: CoreState = {
        ...emptyCore(),
        nodes: { n1: initialNode },
        edges: { e1: { id: 'e1', source: 'n1', target: 'n2', type: 'LEADS_TO' } },
        seq: 5,
        epoch: 1,
      };

      const frozenPrevNodes = { ...prevState.nodes };
      const frozenPrevEdges = { ...prevState.edges };

      const ev = {
        seq: 6,
        ts: 1050,
        epoch: 1,
        type: 'status_changed' as const,
        data: { id: 'n1', status: 'QUARANTINED' as const, root_id: 'n1', depth: 0 },
      };

      const result = applyEvent(prevState, ev);

      // Verify previous state was NOT mutated
      expect(prevState.nodes.n1?.status).toBe('HEALTHY');
      expect(prevState.seq).toBe(5);
      expect(prevState.nodes).toEqual(frozenPrevNodes);
      expect(prevState.edges).toEqual(frozenPrevEdges);

      // Verify new state has updated values
      expect(result.state.nodes.n1?.status).toBe('QUARANTINED');
      expect(result.state.nodes.n1?.quarantined_at).toBe(1050);
      expect(result.state.seq).toBe(6);
      expect(result.state.nodes).not.toBe(prevState.nodes);
    });

    it('preserves immutability of prev CoreState during node addition and edge addition', () => {
      const s0 = emptyCore();
      const n = node('new_node');
      const r1 = applyEvent(s0, { seq: 1, ts: 100, epoch: 1, type: 'node_added', data: { node: n } });

      expect(s0.nodes).toEqual({});
      expect(r1.state.nodes.new_node).toBeDefined();

      const edge: GraphEdge = { id: 'e1', source: 'new_node', target: 'other', type: 'LEADS_TO' };
      const r2 = applyEvent(r1.state, { seq: 2, ts: 101, epoch: 1, type: 'edge_added', data: { edge } });

      expect(r1.state.edges).toEqual({});
      expect(r2.state.edges.e1).toBeDefined();
    });

    it('enforces maximum feed length of 300 items', () => {
      let state = emptyCore();
      for (let i = 1; i <= 350; i++) {
        state = applyEvent(state, {
          seq: i,
          ts: 1000 + i,
          epoch: 1,
          type: 'node_added',
          data: { node: node(`node_${i}`) },
        }).state;
      }
      expect(state.feed.length).toBe(300);
      expect(state.feed[0]?.seq).toBe(350);
    });

    it('enforces eventLog buffer limit of 600 items', () => {
      let state = emptyCore();
      for (let i = 1; i <= 650; i++) {
        state = applyEvent(state, {
          seq: i,
          ts: 1000 + i,
          epoch: 1,
          type: 'node_added',
          data: { node: node(`node_${i}`) },
        }).state;
      }
      expect(state.eventLog.length).toBe(600);
      expect(state.eventLog[state.eventLog.length - 1]?.seq).toBe(650);
    });
  });

  describe('Vector 4: Quarantine Status & Cascade Logic', () => {
    it('correctly tracks cascade roots and accumulates ids', () => {
      let s = emptyCore();
      s = applyEvent(s, { seq: 1, ts: 1000, epoch: 1, type: 'node_added', data: { node: node('root') } }).state;
      s = applyEvent(s, { seq: 2, ts: 1000, epoch: 1, type: 'node_added', data: { node: node('child1') } }).state;
      s = applyEvent(s, { seq: 3, ts: 1000, epoch: 1, type: 'node_added', data: { node: node('child2') } }).state;

      s = applyEvent(s, {
        seq: 4,
        ts: 1010,
        epoch: 1,
        type: 'status_changed',
        data: { id: 'root', status: 'QUARANTINED', root_id: 'root', depth: 0 },
      }).state;

      expect(s.cascade?.rootId).toBe('root');
      expect(s.cascade?.ids).toEqual(['root']);

      s = applyEvent(s, {
        seq: 5,
        ts: 1020,
        epoch: 1,
        type: 'status_changed',
        data: { id: 'child1', status: 'QUARANTINED', root_id: 'root', depth: 1 },
      }).state;

      expect(s.cascade?.ids).toEqual(['root', 'child1']);

      s = applyEvent(s, {
        seq: 6,
        ts: 1030,
        epoch: 1,
        type: 'status_changed',
        data: { id: 'child2', status: 'QUARANTINED', root_id: 'root', depth: 2 },
      }).state;

      expect(s.cascade?.ids).toEqual(['root', 'child1', 'child2']);
    });

    it('properly restores quarantined nodes to HEALTHY with released_at and cleared quarantine_root', () => {
      let s = emptyCore();
      s = applyEvent(s, { seq: 1, ts: 1000, epoch: 1, type: 'node_added', data: { node: node('root') } }).state;
      s = applyEvent(s, {
        seq: 2,
        ts: 1010,
        epoch: 1,
        type: 'status_changed',
        data: { id: 'root', status: 'QUARANTINED', root_id: 'root', depth: 0 },
      }).state;

      expect(s.nodes.root?.status).toBe('QUARANTINED');
      expect(s.nodes.root?.quarantined_at).toBe(1010);
      expect(s.nodes.root?.quarantine_root).toBe('root');

      // Now restore
      s = applyEvent(s, {
        seq: 3,
        ts: 2000,
        epoch: 2,
        type: 'status_changed',
        data: { id: 'root', status: 'HEALTHY' },
      }).state;

      expect(s.nodes.root?.status).toBe('HEALTHY');
      expect(s.nodes.root?.released_at).toBe(2000);
      expect(s.nodes.root?.quarantine_root).toBeNull();
    });
  });
});
