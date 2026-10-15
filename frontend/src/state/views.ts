import type { View } from './store';

/**
 * Which screens exist. A fresh install shows the six that matter day to day. The guided story (QUORUM_DEMO=1, and the
 * sample build) adds the four teaching screens and keeps the original number keys.
 */
const CLEAN: View[] = ['overview', 'console', 'context', 'connect', 'team', 'activity'];
const DEMO: View[] = ['overview', 'console', 'memory', 'brain', 'tenants', 'benchmark', 'context', 'connect', 'team', 'activity'];

export const visibleViews = (demo: boolean): View[] => (demo ? DEMO : CLEAN);

const DEMO_KEYS: Partial<Record<View, string>> = { overview: '0', console: '1', memory: '2', brain: '3', tenants: '4', benchmark: '5', context: '6', connect: '7', team: '8', activity: '9' };

/** The number key that opens a screen. */
export function viewKey(view: View, demo: boolean): string | undefined {
  if (demo) return DEMO_KEYS[view];
  const i = CLEAN.indexOf(view);
  return i < 0 ? undefined : String(i + 1);
}

export function viewForKey(key: string, demo: boolean): View | undefined {
  return visibleViews(demo).find((v) => viewKey(v, demo) === key);
}
