type Listener<T> = (payload: T) => void;

function channel<T>() {
  const ls = new Set<Listener<T>>();
  return {
    on(l: Listener<T>) {
      ls.add(l);
      return () => {
        ls.delete(l);
      };
    },
    emit(p: T) {
      ls.forEach((l) => l(p));
    },
  };
}

export interface Delta {
  ids: string[];
  edgeIds: string[];
  delays: Record<string, number>;
}

/** Imperative channels between the event pipeline / UI and the Cytoscape adapter (kept out of React state). */
export const graphBus = {
  delta: channel<Delta>(),
  full: channel<void>(),
  fit: channel<void>(),
  focus: channel<string>(),
  relayout: channel<void>(),
  collapse: channel<void>(),
};
