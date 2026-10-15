import { runStep } from './endpoints';
import { goToTime } from './timetravel';
import { useStore } from './store';

let run = 0;
const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

export function stopAutopilot() {
  run += 1;
  useStore.getState().set({ autopilot: false });
}

/** The 90-second demo with fixed pauses, so the video can be recorded in one take. */
export async function startAutopilot(): Promise<void> {
  const mine = ++run;
  const alive = () => mine === run;
  const set = useStore.getState().set;
  set({ autopilot: true, view: 'console', tab: 'graph', brainQuestion: null });
  const step = async (s: string, wait: number) => {
    if (!alive()) return;
    await runStep(s).catch(() => undefined);
    await sleep(wait);
  };
  try {
    await goToTime(null);
    await step('reset', 1400);

    await step('inject_poison', 2400);
    await step('run_agents', 6200);

    await step('audit', 5200);
    if (!alive()) return;
    set({ tab: 'diff' });
    await sleep(4500);
    if (!alive()) return;
    set({ tab: 'graph' });

    const log = useStore.getState().eventLog;
    const poisonAt = log.find((e) => e.type === 'scenario_progress' && e.step === 'inject_poison')?.ts;
    if (poisonAt) {
      await goToTime(poisonAt - 1);
      await sleep(3200);
      await goToTime(null);
      await sleep(900);
    }

    if (!alive()) return;
    set({ view: 'memory' });
    await sleep(3800);
    if (!alive()) return;
    set({ view: 'console' });
    await sleep(600);

    await step('supersede', 3200);

    if (!alive()) return;
    set({ view: 'brain', brainQuestion: 'Who owns the refund policy?' });
    await sleep(4200);
    if (!alive()) return;
    set({ view: 'tenants' });
    await sleep(3600);
    if (!alive()) return;
    set({ view: 'console', brainQuestion: null });
  } finally {
    if (alive()) set({ autopilot: false });
  }
}
