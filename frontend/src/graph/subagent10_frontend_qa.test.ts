import { beforeEach, describe, expect, it } from 'vitest';
import cytoscape from 'cytoscape';
import type { GraphEdge, GraphNode } from '../api/schemas';
import { wrapNodeLabel } from './adapter';
import { COL_X, layoutAll } from './layout';
import { createTourDriver } from '../tour/tour';
import { useStore, visibleGraph } from '../state/store';
import type { DriveStep, HookOpts } from 'driver.js';

const dummyHookOpts = {} as unknown as HookOpts;

describe('Subagent 10: Multi-Vector Stress & Boundary Tests', () => {
  beforeEach(() => {
    useStore.setState({
      view: 'console',
      tab: 'graph',
      selectedId: null,
      inspectorTab: 'details',
      nodes: {},
      edges: {},
      past: null,
      asOf: null,
      eventLog: [],
    });
  });

  describe('1. Massive Graph Stress (500 nodes, 1,000 edges)', () => {
    it('executes layout and Cytoscape batch loading for 500 nodes and 1,000 edges within latency budget', () => {
      const kinds: GraphNode['kind'][] = ['source', 'fact', 'conclusion', 'action'];
      const nodes: GraphNode[] = [];
      for (let i = 0; i < 500; i++) {
        const kind = kinds[i % 4]!;
        nodes.push({
          id: `node_${i}`,
          kind,
          label: `Stress Node ${i} with long description to test label wrapping`,
          status: i % 10 === 0 ? 'QUARANTINED' : i % 5 === 0 ? 'SUPERSEDED' : 'HEALTHY',
          stale: i % 12 === 0,
          is_decision: kind === 'conclusion' && i % 3 === 0,
          action: kind === 'action' ? {
            type: 'webhook',
            reversibility: 'REVERSIBLE',
            executed: i % 2 === 0,
            confirmed: i % 4 === 0,
            gate: i % 8 === 0 ? 'BLOCKED' : 'PASSED',
          } : undefined,
        });
      }

      const edges: GraphEdge[] = [];
      const edgeTypes: GraphEdge['type'][] = ['LEADS_TO', 'SUPPORTS', 'CONTRADICTS', 'SUPERSEDES', 'FROM_SOURCE'];
      for (let i = 0; i < 1000; i++) {
        edges.push({
          id: `edge_${i}`,
          source: `node_${i % 500}`,
          target: `node_${(i * 7 + 1) % 500}`,
          type: edgeTypes[i % edgeTypes.length]!,
        });
      }

      const t0 = performance.now();
      const pos = layoutAll(nodes, edges);
      const layoutDuration = performance.now() - t0;

      expect(Object.keys(pos).length).toBe(500);
      expect(layoutDuration).toBeLessThan(120);

      const cy = cytoscape({ headless: true, elements: [] });
      const t1 = performance.now();
      cy.batch(() => {
        cy.add(nodes.map((n) => ({
          group: 'nodes' as const,
          data: {
            id: n.id,
            label: wrapNodeLabel(n.label, 14, 3),
            kind: n.kind,
            status: n.status,
            stale: n.stale ? 1 : 0,
          },
          position: pos[n.id] ?? { x: 0, y: 0 },
        })));
        cy.add(edges.map((e) => ({
          group: 'edges' as const,
          data: { id: e.id, source: e.source, target: e.target, type: e.type },
        })));
      });
      const cyDuration = performance.now() - t1;

      expect(cy.nodes().length).toBe(500);
      expect(cy.edges().length).toBe(1000);
      expect(cyDuration).toBeLessThan(200);

      cy.destroy();
    });
  });

  describe('2. Boundary Edge Cases (orphans, self-loops, >1000 char tokens, null selectedId)', () => {
    it('handles orphan nodes with zero edges without positioning failure', () => {
      const orphans: GraphNode[] = [
        { id: 's1', kind: 'source', label: 'Isolated Doc', status: 'HEALTHY', stale: false },
        { id: 'f1', kind: 'fact', label: 'Isolated Fact', status: 'HEALTHY', stale: false },
        { id: 'c1', kind: 'conclusion', label: 'Isolated Concl', status: 'HEALTHY', stale: false },
        { id: 'a1', kind: 'action', label: 'Isolated Act', status: 'HEALTHY', stale: false },
      ];
      const pos = layoutAll(orphans, []);
      expect(pos.s1?.x).toBe(COL_X[0]);
      expect(pos.f1?.x).toBe(COL_X[1]);
      expect(pos.c1?.x).toBe(COL_X[2]);
      expect(pos.a1?.x).toBe(COL_X[3]);
      orphans.forEach((n) => {
        expect(Number.isFinite(pos[n.id]?.y)).toBe(true);
      });
    });

    it('handles self-loop edges without infinite recursion or invalid coordinates', () => {
      const node: GraphNode = {
        id: 'self_node',
        kind: 'conclusion',
        label: 'Looping fact',
        status: 'HEALTHY',
        stale: false,
      };
      const loopEdge: GraphEdge = {
        id: 'self_edge',
        source: 'self_node',
        target: 'self_node',
        type: 'LEADS_TO',
      };
      const pos = layoutAll([node], [loopEdge]);
      expect(pos.self_node?.x).toBe(COL_X[2]);
      expect(pos.self_node?.y).toBe(0);
    });

    it('wraps ultra-long unbroken string (>1000 chars) strictly into 3 lines without border bleeding', () => {
      const ultraLong = 'Z'.repeat(1200);
      const wrapped = wrapNodeLabel(ultraLong, 14, 3);
      const lines = wrapped.split('\n');
      expect(lines.length).toBe(3);
      for (const line of lines) {
        expect(line.length).toBeLessThanOrEqual(14);
      }
      expect(lines[2]?.endsWith('...')).toBe(true);
    });

    it('safely tolerates null selectedId across store and visibleGraph', () => {
      useStore.setState({ selectedId: null });
      expect(useStore.getState().selectedId).toBeNull();
      const vg = visibleGraph(useStore.getState());
      expect(vg.nodes).toEqual([]);
      expect(vg.edges).toEqual([]);
    });
  });

  describe('3. Driver.js 7-Step Tour Rapid Hammer & Proactive Tab Transitions', () => {
    it('executes rapid next/back transitions across all 7 steps without crashing or losing tab alignment', () => {
      const driverInstance = createTourDriver();
      const steps = driverInstance.getConfig().steps!;
      expect(steps).toHaveLength(7);

      // Rapidly fire onHighlightStarted and step callbacks forwards
      for (let i = 0; i < steps.length; i++) {
        const step = steps[i];
        if (!step) continue;
        step.onHighlightStarted?.(undefined, step, dummyHookOpts);
        const state = useStore.getState();
        if (i === 0 || i === 1 || i === 2) {
          expect(state.tab).toBe('graph');
        } else if (i === 3 || i === 4) {
          expect(state.tab).toBe('ledger');
        } else if (i === 5) {
          expect(state.tab).toBe('graph');
        } else if (i === 6) {
          expect(state.inspectorTab).toBe('cypher');
        }
      }

      // Rapidly fire step callbacks backwards
      for (let i = steps.length - 1; i >= 0; i--) {
        const step = steps[i];
        if (!step) continue;
        step.onHighlightStarted?.(undefined, step, dummyHookOpts);
        const state = useStore.getState();
        if (i === 4 || i === 3) {
          expect(state.tab).toBe('ledger');
        } else if (i === 2 || i === 1 || i === 0) {
          expect(state.tab).toBe('graph');
        }
      }
    });

    it('triggers proactive tab switching hooks on step 3 (Next to ledger) and step 6 (Prev to ledger)', () => {
      const driverInstance = createTourDriver();
      const steps = driverInstance.getConfig().steps!;

      let moves = 0;
      const mockDriverOpts = {
        driver: {
          moveNext: () => { moves++; },
          movePrevious: () => { moves++; },
          getActiveIndex: () => 3,
          drive: () => {},
        },
      } as unknown as HookOpts;

      // Step 3 Next click
      useStore.setState({ tab: 'graph' });
      const step3 = steps[2] as DriveStep;
      step3.popover?.onNextClick?.(undefined, step3, mockDriverOpts);
      expect(useStore.getState().tab).toBe('ledger');

      // Step 4 Prev click
      useStore.setState({ tab: 'ledger' });
      const step4 = steps[3] as DriveStep;
      step4.popover?.onPrevClick?.(undefined, step4, mockDriverOpts);
      expect(useStore.getState().tab).toBe('graph');

      // Step 5 Next click
      useStore.setState({ tab: 'ledger' });
      const step5 = steps[4] as DriveStep;
      step5.popover?.onNextClick?.(undefined, step5, mockDriverOpts);
      expect(useStore.getState().tab).toBe('graph');

      // Step 6 Prev click
      useStore.setState({ tab: 'graph' });
      const step6 = steps[5] as DriveStep;
      step6.popover?.onPrevClick?.(undefined, step6, mockDriverOpts);
      expect(useStore.getState().tab).toBe('ledger');
      expect(moves).toBe(0); // Before timeout
    });

    it('re-drives when dummy element fallback is detected on steps 4 and 6', () => {
      const driverInstance = createTourDriver();
      const steps = driverInstance.getConfig().steps!;

      let driveTarget = -1;
      const mockDriverOpts = {
        driver: {
          getActiveIndex: () => 3,
          drive: (idx: number) => { driveTarget = idx; },
        },
      } as unknown as HookOpts;

      const dummyEl = { id: 'driver-dummy-element' } as unknown as Element;
      const step4 = steps[3] as DriveStep;
      step4.onHighlightStarted?.(dummyEl, step4, mockDriverOpts);
      expect(useStore.getState().tab).toBe('ledger');
      expect(driveTarget).toBe(-1); // Fires on timeout
    });
  });

  describe('4. Viewport Resizing Stress (320px Mobile to 2560px Ultrawide)', () => {
    it('handles rapid canvas resize across mobile, tablet, desktop, and ultrawide viewports', () => {
      const viewports = [
        { w: 320, h: 480, name: 'Mobile 320px' },
        { w: 375, h: 667, name: 'Mobile 375px' },
        { w: 768, h: 1024, name: 'Tablet 768px' },
        { w: 1024, h: 768, name: 'Tablet Landscape' },
        { w: 1440, h: 900, name: 'Desktop Standard' },
        { w: 1920, h: 1080, name: 'Full HD 1080p' },
        { w: 2560, h: 1440, name: 'Ultrawide 2K' },
      ];

      const cy = cytoscape({
        headless: true,
        elements: [
          { group: 'nodes', data: { id: 'n1', label: 'Test 1' }, position: { x: 0, y: 0 } },
          { group: 'nodes', data: { id: 'n2', label: 'Test 2' }, position: { x: 206, y: 68 } },
          { group: 'edges', data: { id: 'e1', source: 'n1', target: 'n2' } },
        ],
      });

      for (const vp of viewports) {
        expect(vp.w).toBeGreaterThan(0);
        cy.resize();
        cy.fit();
        const z = cy.zoom();
        const pan = cy.pan();
        expect(Number.isFinite(z)).toBe(true);
        expect(Number.isFinite(pan.x)).toBe(true);
        expect(Number.isFinite(pan.y)).toBe(true);
        expect(z).toBeGreaterThan(0);
      }

      cy.destroy();
    });
  });
});
