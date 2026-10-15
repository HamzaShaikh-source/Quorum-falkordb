export type ToastTone = 'info' | 'good' | 'warn' | 'bad';

export interface Toast {
  id: number;
  text: string;
  tone: ToastTone;
  count: number;
  ttl: number;
}

export const MAX_TOASTS = 3;

/** Longer messages and problems stay longer, so they can be read. */
export function ttlFor(text: string, tone: ToastTone): number {
  const base = tone === 'bad' || tone === 'warn' ? 8000 : 4500;
  return Math.min(14000, base + text.length * 45);
}

/** Adds a toast. An identical message already showing is counted instead of stacking a duplicate. */
export function addToast(list: Toast[], text: string, tone: ToastTone, nextId: () => number): Toast[] {
  const dup = list.find((t) => t.text === text && t.tone === tone);
  if (dup) return list.map((t) => (t === dup ? { ...t, count: t.count + 1 } : t));
  return [...list, { id: nextId(), text, tone, count: 1, ttl: ttlFor(text, tone) }].slice(-MAX_TOASTS);
}
