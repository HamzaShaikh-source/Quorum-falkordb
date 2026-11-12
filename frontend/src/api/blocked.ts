/** Tiny pub/sub so the API layer can tell the shell "FalkorDB is down" without importing the store. */
export interface Blocked {
  reason: string;
  url: string;
  steps: string[];
}

type Listener = (b: Blocked) => void;
const listeners = new Set<Listener>();

export function reportBlocked(b: Blocked) {
  listeners.forEach((l) => l(b));
}

export function onBlocked(l: Listener): () => void {
  listeners.add(l);
  return () => {
    listeners.delete(l);
  };
}
