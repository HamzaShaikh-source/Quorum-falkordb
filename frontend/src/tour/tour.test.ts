import { beforeEach, describe, expect, it } from 'vitest';
import type { HookOpts } from 'driver.js';
import { createTourDriver, isTourSeen } from './tour';
import { useStore } from '../state/store';

const store: Record<string, string> = {};
const mockLocalStorage: Storage = {
  length: 0,
  key: (index: number) => Object.keys(store)[index] ?? null,
  getItem: (k: string) => store[k] ?? null,
  setItem: (k: string, v: string) => {
    store[k] = v;
  },
  removeItem: (k: string) => {
    delete store[k];
  },
  clear: () => {
    Object.keys(store).forEach((k) => delete store[k]);
  },
};

if (typeof localStorage === 'undefined') {
  Object.defineProperty(globalThis, 'localStorage', { value: mockLocalStorage, writable: true, configurable: true });
}

const dummyHookOpts = {} as unknown as HookOpts;

describe('7-Step Spotlight Onboarding Product Tour Suite', () => {
  beforeEach(() => {
    localStorage.clear();
    useStore.setState({
      view: 'console',
      tab: 'graph',
      selectedId: null,
      inspectorTab: 'details',
      nodes: {
        f_sample: {
          id: 'f_sample',
          kind: 'fact',
          label: 'Sample Fact',
          status: 'HEALTHY',
          epoch: 1,
          valid_from: 1000,
          confidence: 1.0,
          stale: false,
        },
      },
    });
  });

  it('verifies exactly 7 spotlight steps are configured', () => {
    const driverInstance = createTourDriver();
    const config = driverInstance.getConfig();
    expect(config.steps).toBeDefined();
    expect(config.steps).toHaveLength(7);
  });

  it('validates exact required selectors, titles, and descriptions for all 7 steps', () => {
    const driverInstance = createTourDriver();
    const steps = driverInstance.getConfig().steps!;

    const expected = [
      {
        step: 1,
        selector: '#tour-causal-canvas',
        title: 'Causal Memory Graph',
        desc: 'Active agent memory is stored as a causal DAG in FalkorDB. Green nodes represent verified facts; blue nodes are agent deductions.',
      },
      {
        step: 2,
        selector: '#tour-autopilot-btn',
        title: '1-Click Incident Runner',
        desc: 'Run the live 6-step scenario: Inject a poisoned webhook, watch the Auditor detect the contradiction, and observe the rollback.',
      },
      {
        step: 3,
        selector: '#tour-containment-report',
        title: 'Sub-Millisecond Quarantine',
        desc: 'FalkorDB traces all downstream dependencies in <0.4ms. When a premise is revoked, the entire tainted branch turns red.',
      },
      {
        step: 4,
        selector: '#tour-action-ledger',
        title: 'Transactional Outbox Sagas',
        desc: 'Actions are strictly guarded. Reversible actions (like customer refunds) are automatically compensated via the helpdesk API.',
      },
      {
        step: 5,
        selector: '#tour-damage-control',
        title: 'Automated Retraction Notices',
        desc: 'For irreversible actions already executed (sent emails), Quorum automatically drafts a verified apology notice for human approval.',
      },
      {
        step: 6,
        selector: '#tour-as-of-slider',
        title: 'Bitemporal Time Travel',
        desc: 'Slide back in time to audit what the multi-agent team believed at any past timestamp T using FalkorDB as-of queries.',
      },
      {
        step: 7,
        selector: '#tour-cypher-toggle',
        title: 'Verified FalkorDB Queries',
        desc: 'Inspect the live Cypher statements and execution latencies powering every graph transition.',
      },
    ];

    expected.forEach(({ selector, title, desc }, idx) => {
      const step = steps[idx]!;
      expect(step).toBeDefined();
      expect(step.element).toBe(selector);
      expect(step.popover?.title).toBe(title);
      expect(step.popover?.description).toBe(desc);
    });
  });

  it('validates state transitions across tour steps', () => {
    const driverInstance = createTourDriver();
    const steps = driverInstance.getConfig().steps!;

    // Step 1: Sets graph tab
    steps[0]?.onHighlightStarted?.(undefined, steps[0], dummyHookOpts);
    expect(useStore.getState().tab).toBe('graph');

    // Step 4: Switches tab to ledger
    steps[3]?.onHighlightStarted?.(undefined, steps[3], dummyHookOpts);
    expect(useStore.getState().tab).toBe('ledger');

    // Step 5: Keeps tab on ledger
    steps[4]?.onHighlightStarted?.(undefined, steps[4], dummyHookOpts);
    expect(useStore.getState().tab).toBe('ledger');

    // Step 6: Switches back to graph tab
    steps[5]?.onHighlightStarted?.(undefined, steps[5], dummyHookOpts);
    expect(useStore.getState().tab).toBe('graph');

    // Step 7: Selects a node and opens Cypher tab
    steps[6]?.onHighlightStarted?.(undefined, steps[6], dummyHookOpts);
    expect(useStore.getState().selectedId).toBe('f_sample');
    expect(useStore.getState().inspectorTab).toBe('cypher');
  });

  it('tracks tour seen state in localStorage', () => {
    expect(isTourSeen()).toBe(false);
    localStorage.setItem('quorum_tour_seen', 'true');
    expect(isTourSeen()).toBe(true);
  });
});
