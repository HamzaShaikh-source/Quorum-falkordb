import { driver, type Driver, type DriveStep, type HookOpts, type PopoverDOM } from 'driver.js';
import 'driver.js/dist/driver.css';
import '../styles/tour.css';
import { useStore } from '../state/store';

let activeDriver: Driver | null = null;

function markTourSeen() {
  try {
    if (typeof localStorage !== 'undefined') {
      localStorage.setItem('quorum_tour_seen', 'true');
    }
  } catch {
    /* localStorage may fail in restricted/private modes */
  }
}

export function isTourSeen(): boolean {
  try {
    if (typeof localStorage !== 'undefined') {
      return localStorage.getItem('quorum_tour_seen') === 'true';
    }
    return false;
  } catch {
    return false;
  }
}

const TOUR_STEPS: DriveStep[] = [
  {
    element: '#cy',
    onHighlightStarted: () => {
      useStore.getState().set({ view: 'console', tab: 'graph' });
    },
    popover: {
      title: 'Causal Memory Graph',
      description:
        'Active agent memory is stored as a causal DAG in FalkorDB. Green nodes represent verified facts; blue nodes are agent deductions.',
      side: 'bottom',
      align: 'center',
    },
  },
  {
    element: '.autopilot-controls',
    onHighlightStarted: () => {
      useStore.getState().set({ view: 'console', tab: 'graph' });
    },
    popover: {
      title: '1-Click Incident Runner',
      description:
        'Run the live 6-step scenario: inject a poisoned webhook, watch the Auditor detect the contradiction, and observe the rollback.',
      side: 'right',
      align: 'start',
    },
  },
  {
    element: '.containment-trigger',
    onHighlightStarted: () => {
      useStore.getState().set({ view: 'console', tab: 'graph' });
    },
    popover: {
      title: 'Sub-Millisecond Quarantine',
      description:
        'FalkorDB traces all downstream dependencies in <0.4ms. When a premise is revoked, the entire tainted branch turns red.',
      side: 'left',
      align: 'start',
      onNextClick: (_el, _step, opts) => {
        useStore.getState().set({ view: 'console', tab: 'ledger' });
        setTimeout(() => {
          opts.driver.moveNext();
        }, 50);
      },
    },
  },
  {
    element: '.action-ledger',
    onHighlightStarted: (el, _step, opts) => {
      useStore.getState().set({ view: 'console', tab: 'ledger' });
      if (!el || el.id === 'driver-dummy-element') {
        setTimeout(() => {
          if (opts.driver.getActiveIndex() === 3) {
            opts.driver.drive(3);
          }
        }, 60);
      }
    },
    popover: {
      title: 'Transactional Outbox Sagas',
      description:
        'Actions are strictly guarded. Reversible actions (like customer refunds) are automatically compensated via the helpdesk API.',
      side: 'top',
      align: 'center',
      onPrevClick: (_el, _step, opts) => {
        useStore.getState().set({ view: 'console', tab: 'graph' });
        setTimeout(() => {
          opts.driver.movePrevious();
        }, 50);
      },
    },
  },
  {
    element: '.damage-control-badge',
    onHighlightStarted: () => {
      useStore.getState().set({ view: 'console', tab: 'ledger' });
    },
    popover: {
      title: 'Automated Retraction Notices',
      description:
        'For irreversible actions already executed (sent emails), Quorum automatically drafts a verified apology notice for human approval.',
      side: 'bottom',
      align: 'center',
      onNextClick: (_el, _step, opts) => {
        useStore.getState().set({ view: 'console', tab: 'graph' });
        setTimeout(() => {
          opts.driver.moveNext();
        }, 50);
      },
    },
  },
  {
    element: '.as-of-slider',
    onHighlightStarted: (el, _step, opts) => {
      useStore.getState().set({ view: 'console', tab: 'graph' });
      if (!el || el.id === 'driver-dummy-element') {
        setTimeout(() => {
          if (opts.driver.getActiveIndex() === 5) {
            opts.driver.drive(5);
          }
        }, 60);
      }
    },
    popover: {
      title: 'Bitemporal Time Travel',
      description:
        'Slide back in time to audit what the multi-agent team believed at any past timestamp T using FalkorDB as-of queries.',
      side: 'top',
      align: 'center',
      onPrevClick: (_el, _step, opts) => {
        useStore.getState().set({ view: 'console', tab: 'ledger' });
        setTimeout(() => {
          opts.driver.movePrevious();
        }, 50);
      },
    },
  },
  {
    element: '.cypher-drawer-toggle',
    onHighlightStarted: () => {
      const st = useStore.getState();
      if (!st.selectedId) {
        const first = Object.keys(st.nodes)[0];
        if (first) {
          st.set({ selectedId: first, inspectorTab: 'cypher' });
        } else {
          st.set({ inspectorTab: 'cypher' });
        }
      } else {
        st.set({ inspectorTab: 'cypher' });
      }
    },
    popover: {
      title: 'Verified FalkorDB Queries',
      description:
        'Inspect the live Cypher statements and execution latencies powering every graph transition.',
      side: 'left',
      align: 'center',
    },
  },
];

export function createTourDriver(): Driver {
  const driverInstance: Driver = driver({
    animate: true,
    overlayColor: 'rgba(17, 24, 39, 0.5)',
    stagePadding: 8,
    stageRadius: 8,
    allowClose: true,
    showProgress: true,
    progressText: 'Step {{current}} of {{total}}',
    nextBtnText: 'Next',
    prevBtnText: 'Back',
    doneBtnText: 'Finish',
    showButtons: ['next', 'previous', 'close'],
    steps: TOUR_STEPS,
    onPopoverRender: (popover: PopoverDOM, { driver: d }: HookOpts) => {
      if (!popover.footer.querySelector('.driver-popover-skip-btn')) {
        const skipBtn = document.createElement('button');
        skipBtn.type = 'button';
        skipBtn.className = 'driver-popover-skip-btn';
        skipBtn.textContent = 'Skip Tour';
        skipBtn.setAttribute('aria-label', 'Skip Product Tour');
        skipBtn.addEventListener('click', () => {
          markTourSeen();
          d.destroy();
        });
        popover.footer.insertBefore(skipBtn, popover.footerButtons);
      }
    },
    onCloseClick: (_el, _step, opts) => {
      markTourSeen();
      opts.driver.destroy();
    },
    onDoneClick: (_el, _step, opts) => {
      markTourSeen();
      opts.driver.destroy();
    },
    onDestroyStarted: () => {
      markTourSeen();
    },
    onDestroyed: () => {
      markTourSeen();
      activeDriver = null;
    },
  });

  return driverInstance;
}

export function startTour() {
  // Preload lazy tab components used in the tour
  void import('../components/Ledger');

  const st = useStore.getState();
  if (st.view !== 'console') {
    st.set({ view: 'console' });
  }

  if (activeDriver && activeDriver.isActive()) {
    activeDriver.destroy();
  }

  activeDriver = createTourDriver();
  // Small tick to ensure target elements are mounted and visible
  setTimeout(() => {
    activeDriver?.drive();
  }, 50);
}
