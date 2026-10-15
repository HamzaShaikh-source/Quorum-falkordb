export interface DevLogEntry {
  id: number;
  ts: number;
  level: 'info' | 'warn' | 'error';
  message: string;
}

type Listener = (entries: DevLogEntry[]) => void;

let nextId = 1;
let entries: DevLogEntry[] = [];
const listeners = new Set<Listener>();
const seen = new Set<string>();

export function devLog(level: DevLogEntry['level'], message: string, once = false): void {
  if (once) {
    if (seen.has(message)) return;
    seen.add(message);
  }
  entries = [...entries.slice(-199), { id: nextId++, ts: Date.now(), level, message }];
  if (level !== 'info') console.warn(`[quorum] ${message}`);
  listeners.forEach((l) => l(entries));
}

export function subscribeDevLog(l: Listener): () => void {
  listeners.add(l);
  l(entries);
  return () => {
    listeners.delete(l);
  };
}
