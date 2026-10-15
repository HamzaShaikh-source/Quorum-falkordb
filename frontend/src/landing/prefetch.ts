let started = false;

/** Starts downloading the console bundle. Safe to call many times. */
export function prefetchConsole() {
  if (started) return;
  started = true;
  void import('../ConsoleApp');
  void import('../views/Console');
}

/** Warm the console once the landing page has settled and the browser is idle. */
export function prefetchOnIdle() {
  const run = () => prefetchConsole();
  const w = window as Window & { requestIdleCallback?: (cb: () => void, o?: { timeout: number }) => number };
  if (w.requestIdleCallback) w.requestIdleCallback(run, { timeout: 6000 });
  else setTimeout(run, 3500);
}
