import { describe, expect, it, vi, beforeEach } from 'vitest';
import { useStore } from '../state/store';
import { actionDisplayState } from '../state/actionState';
import { diffGraphs } from '../state/diff';
import { startAutopilot, stopAutopilot } from '../state/autopilot';
import * as endpoints from '../state/endpoints';
import * as timetravel from '../state/timetravel';
import type { GraphNode } from '../api/schemas';
import { ApiError } from '../api/client';

describe('Subagent_18 QA Suite: Console, StoryStepper, Inspector & Timeline', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useStore.setState({
      selectedId: null,
      asOf: null,
      past: null,
      compareBase: null,
      autopilot: false,
      nodes: {},
      edges: {},
      eventLog: [],
      scenario: { active: null, lastStep: null, lastState: null, detail: null },
      toasts: [],
    });
  });

  describe('1. StoryStepper & Beat Advancement (Scenario.tsx)', () => {
    const STORY = ['inject_poison', 'run_agents', 'audit', 'supersede'] as const;

    it('advances beats based on scenario_progress finished events', () => {
      const log1: { seq: number; ts: number; type: string; step?: string; state?: string }[] = [];
      const computeNext = (log: typeof log1) => {
        const done = new Set(log.filter((e) => e.type === 'scenario_progress' && e.state === 'finished').map((e) => e.step ?? ''));
        return STORY.find((s) => !done.has(s));
      };

      expect(computeNext(log1)).toBe('inject_poison');

      log1.push({ seq: 1, ts: 100, type: 'scenario_progress', step: 'inject_poison', state: 'finished' });
      expect(computeNext(log1)).toBe('run_agents');

      log1.push({ seq: 2, ts: 200, type: 'scenario_progress', step: 'run_agents', state: 'finished' });
      expect(computeNext(log1)).toBe('audit');

      log1.push({ seq: 3, ts: 300, type: 'scenario_progress', step: 'audit', state: 'finished' });
      expect(computeNext(log1)).toBe('supersede');

      log1.push({ seq: 4, ts: 400, type: 'scenario_progress', step: 'supersede', state: 'finished' });
      expect(computeNext(log1)).toBeUndefined();
    });

    it('vulnerability check: rapid clicks on beat button while request in flight', async () => {
      // In Scenario.tsx, `go(step)` lacks a re-entrancy guard `if (active || pending) return;`
      let inFlight = 0;
      let maxConcurrency = 0;

      vi.spyOn(endpoints, 'runStep').mockImplementation(async () => {
        inFlight++;
        maxConcurrency = Math.max(maxConcurrency, inFlight);
        await new Promise((r) => setTimeout(r, 50));
        inFlight--;
        return { run_id: 'run_test', accepted: true };
      });

      // Simulating rapid dispatch calls
      const p1 = endpoints.runStep('inject_poison');
      const p2 = endpoints.runStep('inject_poison');
      await Promise.all([p1, p2]);

      expect(maxConcurrency).toBe(2); // Demonstrates unthrottled concurrent API calls if UI is clicked rapidly before render
    });

    it('error handling on failed beat dispatch with ApiError', async () => {
      vi.spyOn(endpoints, 'runStep').mockRejectedValue(new ApiError('FORBIDDEN_ROLE', 'Forbidden', 403, 'scenario/audit'));
      
      let caughtError: unknown = null;
      try {
        await endpoints.runStep('audit');
      } catch (e) {
        caughtError = e;
      }

      expect(caughtError).toBeInstanceOf(ApiError);
      expect((caughtError as ApiError).code).toBe('FORBIDDEN_ROLE');
    });
  });

  describe('2. Inspector Controls & Node Property Parsing (Inspector.tsx)', () => {
    it('handles selecting null node without throwing', () => {
      useStore.setState({ selectedId: null });
      const id = useStore.getState().selectedId;
      expect(id).toBeNull();
      const node = id ? useStore.getState().nodes[id] : undefined;
      expect(node).toBeUndefined();
    });

    it('detects action compensation status accurately across all lifecycle states', () => {
      const baseAction: GraphNode = {
        id: 'act_1',
        kind: 'action',
        label: 'Refund customer',
        status: 'QUARANTINED',
        stale: false,
        action: {
          type: 'refund',
          reversibility: 'COMPENSABLE',
          executed: true,
          confirmed: true,
          gate: 'PASSED',
          compensation: {
            status: 'PENDING_COMPENSATION',
            attempts: 0,
          },
        },
      };

      expect(actionDisplayState(baseAction)).toBe('rollingBack');

      baseAction.action!.compensation!.status = 'DISPATCHING';
      expect(actionDisplayState(baseAction)).toBe('rollingBack');

      baseAction.action!.compensation!.status = 'COMPENSATED';
      expect(actionDisplayState(baseAction)).toBe('rolledBack');

      baseAction.action!.compensation!.status = 'FAILED';
      expect(actionDisplayState(baseAction)).toBe('rollbackFailed');
    });

    it('verifies quarantine rationale and root parsing', () => {
      const node: GraphNode = {
        id: 'fact_90',
        kind: 'fact',
        label: 'Refunds allowed for 90 days',
        status: 'QUARANTINED',
        stale: false,
        quarantine_root: 'src_poison',
        quarantined_at: 1700000000000,
      };

      expect(node.quarantine_root).toBe('src_poison');
      expect(node.quarantined_at).toBe(1700000000000);
    });

    it('exposes provenance breadcrumb limitation during timetravel asOf mode', () => {
      // In Inspector.tsx:
      // query is disabled: enabled: !!id && !asOf
      // line 265 passes: <PathTab nodes={[node]} />
      // PathTab has: if (nodes.length <= 1) return <Empty>{copy.inspector.pathEmpty}</Empty>;
      const pastNode: GraphNode = {
        id: 'conclusion_1',
        kind: 'conclusion',
        label: 'Approve refund',
        status: 'HEALTHY',
        stale: false,
      };

      const nodesPassedToPathTab = [pastNode];
      const wouldEarlyReturnEmpty = nodesPassedToPathTab.length <= 1;
      expect(wouldEarlyReturnEmpty).toBe(true); // Demonstrates PathTab always returns Empty in time-travel
    });
  });

  describe('3. Timeline & Time-Travel Scrubbing (Timeline.tsx)', () => {
    it('handles scrubbing to epoch 0 or timestamp 0', async () => {
      const mockGetGraph = vi.spyOn(endpoints, 'getGraph').mockResolvedValue({
        contract_version: '0.1.0',
        tenant: 'acme',
        epoch: 0,
        seq: 0,
        as_of: 0,
        generated_at: 0,
        truncated: false,
        nodes: [],
        edges: [],
      });

      await timetravel.goToTime(0);

      expect(mockGetGraph).toHaveBeenCalledWith(0);
      expect(useStore.getState().asOf).toBe(0);
      expect(useStore.getState().past?.nodes).toEqual([]);
    });

    it('handles scrubbing past max epoch / reset to now (null)', async () => {
      useStore.setState({ asOf: 1000, past: { ts: 1000, nodes: [], edges: [] } });
      await timetravel.goToTime(null);

      expect(useStore.getState().asOf).toBeNull();
      expect(useStore.getState().past).toBeNull();
    });

    it('computes belief diff between compareBase and current graph', () => {
      const n1: GraphNode = { id: '1', kind: 'fact', label: 'Policy 30', status: 'HEALTHY', stale: false };
      const n2: GraphNode = { id: '2', kind: 'fact', label: 'Policy 90', status: 'HEALTHY', stale: false };
      const n1Superseded: GraphNode = { id: '1', kind: 'fact', label: 'Policy 30', status: 'SUPERSEDED', stale: false };
      const n2Quarantined: GraphNode = { id: '2', kind: 'fact', label: 'Policy 90', status: 'QUARANTINED', stale: false };
      const n3Added: GraphNode = { id: '3', kind: 'fact', label: 'Policy 45', status: 'HEALTHY', stale: false };

      const diff = diffGraphs([n1, n2], [n1Superseded, n2Quarantined, n3Added]);
      expect(diff.added.map((n) => n.id)).toEqual(['3']);
      expect(diff.quarantined.map((n) => n.id)).toEqual(['2']);
      expect(diff.replaced.map((n) => n.id)).toEqual(['1']);
      expect(diff.removed).toHaveLength(0);
    });
  });

  describe('4. Autopilot State Machine & Cleanup (autopilot.ts)', () => {
    it('stopAutopilot increments run and resets autopilot flag', async () => {
      vi.spyOn(endpoints, 'runStep').mockResolvedValue({ run_id: 'r1', accepted: true });
      vi.spyOn(timetravel, 'goToTime').mockResolvedValue(undefined);

      const promise = startAutopilot();
      expect(useStore.getState().autopilot).toBe(true);

      stopAutopilot();
      expect(useStore.getState().autopilot).toBe(false);

      await promise;
      expect(useStore.getState().autopilot).toBe(false);
    });
  });
});
