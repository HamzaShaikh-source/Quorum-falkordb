import { ApiError, loadConfig, session } from '../api/client';
import { openEventStream } from '../api/events';
import { CONTRACT_VERSION, type SseEvent } from '../api/schemas';
import { copy } from '../copy';
import { graphBus } from '../graph/bus';
import { applyEvent, emptyCore, planEvents, snapshotToCore, type Effect } from './reducer';
import { getGraph, getHandoffs, getMeta } from './endpoints';
import { queryClient } from './queries';
import { useStore } from './store';

let close: (() => void) | null = null;
let buffer: SseEvent[] = [];
let raf = 0;
let generation = 0;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

const major = (v: string) => v.split('.')[0] ?? '';
const minor = (v: string) => v.split('.')[1] ?? '';

function errorMessage(e: unknown): { endpoint: string; message: string } {
  if (e instanceof ApiError) {
    if (e.code === 'UNREACHABLE') return { endpoint: e.endpoint, message: copy.errors.unreachable };
    if (e.code === 'SCHEMA') return { endpoint: e.endpoint, message: copy.errors.schema(e.endpoint, e.field ?? '') };
    if (e.code === 'UNAUTHENTICATED') return { endpoint: e.endpoint, message: copy.errors.unauthenticated };
    if (e.code === 'FORBIDDEN_TENANT') return { endpoint: e.endpoint, message: copy.errors.forbiddenTenant };
    return { endpoint: e.endpoint, message: copy.errors.generic(e.code, e.message) };
  }
  return { endpoint: 'startup', message: copy.errors.unreachable };
}

function runEffects(effects: Effect[]) {
  const st = useStore.getState();
  for (const ef of effects) {
    switch (ef.kind) {
      case 'toast':
        st.pushToast(ef.text);
        break;
      case 'invalidate':
        ef.keys.forEach((k) => void queryClient.invalidateQueries({ queryKey: [k] }));
        break;
      case 'report':
        st.set({ reportRoot: ef.rootId });
        void queryClient.invalidateQueries({ queryKey: ['report'] });
        break;
      case 'delta':
        if (!useStore.getState().past) graphBus.delta.emit({ ids: ef.ids, edgeIds: ef.edgeIds, delays: ef.delays });
        break;
      case 'claim':
        break;
      case 'resync':
        st.set({ reportRoot: null, selectedId: null });
        void resync();
        break;
    }
  }
}

function flush() {
  raf = 0;
  const st = useStore.getState();
  if (!st.loaded || st.resyncing) return;
  const batch = buffer;
  buffer = [];
  if (batch.some((e) => e.type === 'resync_required')) {
    void resync();
    return;
  }
  const plan = planEvents(st.seq, batch);
  let core = st;
  const effects: Effect[] = [];
  for (const ev of plan.apply) {
    if (ev.type === 'mcp_call') {
      for (const key of ['activity', 'token-stats', 'workspace']) void queryClient.invalidateQueries({ queryKey: [key] });
    }
    if (ev.type === 'workspace_changed') {
      void queryClient.invalidateQueries({ queryKey: ['workspace'] });
      const d = ev.data as { reason?: string; person?: string };
      if (d.reason === 'connect_request') st.pushToast(`${d.person ?? 'Someone'} wants to connect to your hub. Open Team to answer.`, 'warn');
    }
    const r = applyEvent(core, ev);
    core = { ...core, ...r.state };
    effects.push(...r.effects);
  }
  if (plan.apply.length) {
    useStore.setState({
      nodes: core.nodes,
      edges: core.edges,
      seq: core.seq,
      epoch: core.epoch,
      feed: core.feed,
      invariant: core.invariant,
      scenario: core.scenario,
      cascade: core.cascade,
      nodeRequest: core.nodeRequest,
      claimRace: core.claimRace,
      handoffs: core.handoffs,
      eventLog: core.eventLog,
    });
    runEffects(effects);
  }
  if (plan.gap) {
    buffer = [...batch.filter((e) => e.seq > core.seq), ...buffer];
    void resync();
  }
}

function schedule() {
  if (!raf) raf = requestAnimationFrame(flush);
}

function onEvent(ev: SseEvent) {
  buffer.push(ev);
  schedule();
}

export async function loadGraph(expectedGen?: number): Promise<void> {
  const g = await getGraph();
  if (expectedGen !== undefined && expectedGen !== generation) return;
  const st = useStore.getState();
  useStore.setState({
    ...snapshotToCore(st, g),
    reportRoot: st.reportRoot && g.nodes.some((n) => n.id === st.reportRoot) ? st.reportRoot : null,
    selectedId: st.selectedId && g.nodes.some((n) => n.id === st.selectedId) ? st.selectedId : null,
    // A snapshot carries the graph, not "a step is running". Events up to its seq are dropped as already applied, so a step's
    // `finished` event that arrives during the resync would never clear `active` and the story buttons would stay disabled.
    scenario: { ...st.scenario, active: null },
    loaded: true,
    graphStale: false,
    loadError: null,
    resyncing: false,
  });
  graphBus.full.emit();
  schedule();
}

async function resync() {
  const st = useStore.getState();
  if (st.resyncing) return;
  const gen = generation;
  useStore.setState({ resyncing: true });
  try {
    await loadGraph(gen);
    if (gen !== generation) return;
    const h = await getHandoffs().catch(() => []);
    if (gen !== generation) return;
    useStore.setState({ handoffs: Object.fromEntries(h.map((x) => [x.id, x])) });
    const currentNodes = useStore.getState().nodes;
    void queryClient.invalidateQueries({
      predicate: (query) => !['report', 'node', 'trace'].includes(String(query.queryKey[0])) || !!currentNodes[String(query.queryKey[1])],
    });
    st.set({ asOf: null, past: null });
  } catch (e) {
    if (gen !== generation) return;
    useStore.setState({ resyncing: false, loadError: errorMessage(e) });
    return;
  }
  schedule();
}

export async function startSession(): Promise<void> {
  const gen = ++generation;
  stopSession();
  const st = useStore.getState();
  st.set({ loaded: false, loadError: null, conn: 'connecting', asOf: null, past: null, compareBase: null, reportRoot: null, selectedId: null });
  useStore.setState({ ...emptyCore() });
  queryClient.clear();
  try {
    await loadConfig();
    session.role = useStore.getState().role;
    const meta = await getMeta();
    if (gen !== generation) return;
    const vs = major(meta.contract_version) !== major(CONTRACT_VERSION) ? 'major' : minor(meta.contract_version) !== minor(CONTRACT_VERSION) ? 'minor' : 'ok';
    useStore.getState().set({ meta, versionState: vs, tenant: meta.tenant });
    if (vs === 'major') return;
    await loadGraph(gen);
    if (gen !== generation) return;
    const h = await getHandoffs().catch(() => []);
    if (gen !== generation) return;
    useStore.setState({ handoffs: Object.fromEntries(h.map((x) => [x.id, x])) });
    if (gen !== generation) return;
    close = openEventStream({
      lastSeq: () => useStore.getState().seq,
      onEvent,
      onStatus: (c) => {
        const prev = useStore.getState().conn;
        useStore.getState().set({ conn: c, graphStale: c === 'reconnecting' || c === 'offline' });
        if (c === 'live' && prev === 'reconnecting') void resync();
      },
    });
  } catch (e) {
    if (gen !== generation) return;
    useStore.getState().set({ loadError: errorMessage(e), conn: 'offline' });
    if (reconnectTimer) clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(() => void startSession(), 5000);
  }
}

export function stopSession() {
  close?.();
  close = null;
  buffer = [];
  if (raf) {
    cancelAnimationFrame(raf);
    raf = 0;
  }
  if (reconnectTimer) clearTimeout(reconnectTimer);
  reconnectTimer = null;
}

export async function switchTenant(tenant: string) {
  session.tenant = tenant;
  useStore.getState().set({ tenant });
  await startSession();
}

export function setRole(role: 'human' | 'auditor' | 'agent') {
  session.role = role;
  useStore.getState().set({ role });
  void startSession();
}
