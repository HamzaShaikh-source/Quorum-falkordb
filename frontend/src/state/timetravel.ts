import { graphBus } from '../graph/bus';
import { getGraph } from './endpoints';
import { useStore, type PastGraph } from './store';

let token = 0;

export async function fetchPast(ts: number): Promise<PastGraph> {
  const g = await getGraph(ts);
  return { ts, nodes: g.nodes, edges: g.edges };
}

/** Swaps the canvas into the past (or back to now). Past states never animate. */
export async function goToTime(ts: number | null): Promise<void> {
  const mine = ++token;
  const st = useStore.getState();
  if (ts === null) {
    st.set({ asOf: null, past: null });
    graphBus.full.emit();
    return;
  }
  try {
    const past = await fetchPast(ts);
    if (mine !== token) return;
    useStore.getState().set({ asOf: ts, past });
    graphBus.full.emit();
  } catch {
    if (mine === token) useStore.getState().pushToast('Could not load that moment.');
  }
}
