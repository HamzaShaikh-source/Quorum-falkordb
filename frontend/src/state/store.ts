import { create } from 'zustand';
import type { GraphEdge, GraphNode, Meta } from '../api/schemas';
import type { Connection } from '../api/events';
import type { Role } from '../api/client';
import { emptyCore, type CoreState } from './reducer';
import { addToast, type Toast, type ToastTone } from './toasts';

export type View = 'overview' | 'console' | 'memory' | 'brain' | 'tenants' | 'benchmark' | 'context' | 'connect' | 'team' | 'activity';
export type Tab = 'graph' | 'mindmap' | 'diff' | 'ledger' | 'insights';
export type InspectorTab = 'path' | 'details' | 'links' | 'cypher';
export type FeedFilter = 'all' | 'graph' | 'actions' | 'handoffs' | 'system';

export interface GraphFilters {
  kinds: Record<string, boolean>;
  statuses: Record<string, boolean>;
  hideHealthy: boolean;
  local: boolean;
  depth: number;
  gods: boolean;
}
export const defaultFilters = (): GraphFilters => ({
  kinds: { source: true, fact: true, conclusion: true, action: true },
  statuses: { HEALTHY: true, QUARANTINED: true, SUPERSEDED: true },
  hideHealthy: false,
  local: false,
  depth: 2,
  gods: false,
});
export interface CtxMenu {
  x: number;
  y: number;
  id: string;
}

export interface PastGraph {
  ts: number;
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface UiState {
  view: View;
  /** The guided story and its teaching screens. Off on a fresh install; always on in the sample build. */
  story: boolean;
  tab: Tab;
  inspectorTab: InspectorTab;
  feedFilter: FeedFilter;
  listView: boolean;
  presentation: boolean;
  demo: boolean;
  devOpen: boolean;
  shortcutsOpen: boolean;
  helpOpen: boolean;
  legendOpen: boolean;
  selectedId: string | null;
  previewIds: string[];
  search: string;
  lockLayout: boolean;
  asOf: number | null;
  past: PastGraph | null;
  compareBase: PastGraph | null;
  reportRoot: string | null;
  conn: Connection;
  graphStale: boolean;
  resyncing: boolean;
  meta: Meta | null;
  versionState: 'ok' | 'minor' | 'major';
  versionBannerDismissed: boolean;
  loaded: boolean;
  loadError: { endpoint: string; message: string } | null;
  toasts: Toast[];
  role: Role;
  tenant: string;
  autopilot: boolean;
  brainQuestion: string | null;
  filters: GraphFilters;
  filtersOpen: boolean;
  pathIds: string[];
  ctxMenu: CtxMenu | null;
  paletteOpen: boolean;
  navStack: string[];
  navIdx: number;
  invariantsOpen: boolean;
  showMini: boolean;
}

interface Actions {
  set: (p: Partial<UiState>) => void;
  setCore: (fn: (c: CoreState) => CoreState) => void;
  pushToast: (text: string, tone?: ToastTone) => void;
  dropToast: (id: number) => void;
}

export type Store = CoreState & UiState & Actions;

const params = typeof location !== 'undefined' ? new URLSearchParams(location.search) : new URLSearchParams();
let toastId = 1;

function readMini(): boolean {
  try {
    return localStorage.getItem('quorum:minimap') === '1';
  } catch {
    return false;
  }
}

export const useStore = create<Store>((set, get) => ({
  ...emptyCore(),
  view: 'console',
  story: import.meta.env.VITE_API_MODE === 'mock' || import.meta.env.MODE === 'mock',
  tab: 'graph',
  inspectorTab: 'path',
  feedFilter: 'all',
  listView: false,
  presentation: false,
  demo: params.get('demo') === '1',
  devOpen: false,
  shortcutsOpen: false,
  helpOpen: false,
  legendOpen: false,
  selectedId: null,
  previewIds: [],
  search: '',
  lockLayout: false,
  asOf: null,
  past: null,
  compareBase: null,
  reportRoot: null,
  conn: 'connecting',
  graphStale: false,
  resyncing: false,
  meta: null,
  versionState: 'ok',
  versionBannerDismissed: false,
  loaded: false,
  loadError: null,
  toasts: [],
  role: 'human',
  tenant: 'acme',
  autopilot: false,
  brainQuestion: null,
  filters: defaultFilters(),
  filtersOpen: false,
  pathIds: [],
  ctxMenu: null,
  paletteOpen: false,
  navStack: [],
  navIdx: -1,
  invariantsOpen: false,
  showMini: readMini(),
  set: (p) => set(p),
  setCore: (fn) => set((s) => fn(s)),
  pushToast: (text, tone = 'info') => set({ toasts: addToast(get().toasts, text, tone, () => toastId++) }),
  dropToast: (id) => set({ toasts: get().toasts.filter((t) => t.id !== id) }),
}));

/** The graph the canvas shows: the past snapshot while time-travelling, otherwise the live graph. */
export function visibleGraph(s: Pick<Store, 'nodes' | 'edges' | 'past'>): { nodes: GraphNode[]; edges: GraphEdge[] } {
  if (s.past) return { nodes: s.past.nodes, edges: s.past.edges };
  return { nodes: Object.values(s.nodes), edges: Object.values(s.edges) };
}

/* Selection history, like back and forward in a notes app. */
let navigating = false;
useStore.subscribe((st, prev) => {
  if (navigating || st.selectedId === prev.selectedId || !st.selectedId) return;
  const stack = st.navStack.slice(0, st.navIdx + 1);
  if (stack[stack.length - 1] === st.selectedId) return;
  const next = [...stack, st.selectedId].slice(-40);
  useStore.setState({ navStack: next, navIdx: next.length - 1 });
});

export function navMove(delta: -1 | 1): string | null {
  const { navStack, navIdx } = useStore.getState();
  const idx = navIdx + delta;
  const id = navStack[idx];
  if (id === undefined) return null;
  navigating = true;
  useStore.setState({ navIdx: idx, selectedId: id });
  navigating = false;
  return id;
}

export type { Toast };

/** Only one popover is open at a time; opening one closes the others. */
export const POPOVERS = ['filtersOpen', 'legendOpen', 'invariantsOpen'] as const;
useStore.subscribe((st, prev) => {
  const opened = POPOVERS.find((k) => st[k] && !prev[k]);
  if (!opened) return;
  const close: Partial<UiState> = {};
  for (const k of POPOVERS) if (k !== opened && st[k]) close[k] = false;
  if (Object.keys(close).length) useStore.setState(close);
});

export function closePopovers(): boolean {
  const st = useStore.getState();
  if (!POPOVERS.some((k) => st[k])) return false;
  useStore.setState({ filtersOpen: false, legendOpen: false, invariantsOpen: false });
  return true;
}

useStore.subscribe((st, prev) => {
  if (st.showMini === prev.showMini) return;
  try {
    localStorage.setItem('quorum:minimap', st.showMini ? '1' : '0');
  } catch {
    /* storage can be unavailable */
  }
});

if (typeof window !== 'undefined') {
  (window as unknown as { __toast: (t: string, tone?: ToastTone) => void }).__toast = (t, tone) => useStore.getState().pushToast(t, tone);
}
