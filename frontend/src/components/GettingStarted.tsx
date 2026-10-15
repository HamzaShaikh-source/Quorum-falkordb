import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { API_MODE } from '../api/client';
import { useStore } from '../state/store';
import { useLive } from '../workspace/live';
import { getActivity } from '../workspace/server';
import { useWs } from '../workspace/useWs';
import { Icon } from './Icon';

const DISMISS = 'quorum:onboarding-dismissed';
export const SEEN_ACTIVITY = 'quorum:seen-activity';

const read = (k: string) => {
  try {
    return localStorage.getItem(k) === '1';
  } catch {
    return false;
  }
};
const write = (k: string) => {
  try {
    localStorage.setItem(k, '1');
  } catch {
    /* private mode: the card just comes back next visit */
  }
};

/**
 * A first-run path that reads the real state, not a tour: add a person, connect their AI, let it save something, read it
 * in the Activity log. Each step ticks itself when it actually happened (a member exists, a tool call arrived), and the
 * whole card goes away once everything is done or when dismissed.
 */
export function GettingStarted() {
  const ws = useWs();
  const set = useStore((s) => s.set);
  const [hidden, setHidden] = useState(() => read(DISMISS));
  const [seen, setSeen] = useState(() => read(SEEN_ACTIVITY));
  const live = API_MODE === 'live';
  const q = useQuery({ queryKey: ['activity'], queryFn: () => getActivity(), enabled: live && !hidden, refetchInterval: 8000 });
  const simulated = useLive((s) => s.activity);
  const calls = live ? (q.data ?? []).map((c) => c.tool) : simulated.map((a) => a.tool);

  useEffect(() => {
    const on = () => setSeen(read(SEEN_ACTIVITY));
    window.addEventListener('storage', on);
    return () => window.removeEventListener('storage', on);
  }, []);

  const steps = [
    { id: 'team', title: 'Add a person', detail: 'Each person gets their own token, so you can see who did what.', done: ws.members.length > 0, go: { label: 'Open Team', view: 'team' as const } },
    { id: 'connect', title: 'Connect their AI', detail: 'Paste the snippet in step 2 below, then ask the AI a question about your memory.', done: calls.length > 0 },
    { id: 'write', title: 'Let it save something', detail: 'Ask it to remember a policy, or install the capture hook so it saves facts on its own.', done: calls.some((t) => ['remember_fact', 'observe', 'derive'].includes(t)) },
    { id: 'see', title: 'Read what happened', detail: 'The Activity page says it in plain words and exports it for audit.', done: seen, go: { label: 'Open Activity', view: 'activity' as const } },
  ];
  const done = steps.filter((s) => s.done).length;
  if (hidden || done === steps.length) return null;
  const next = steps.find((s) => !s.done)!;

  return (
    <section className="card wide gs" aria-label="Getting started">
      <header className="card-h">
        <h2>Get started</h2>
        <span className="card-sub" aria-live="polite">
          {done} of {steps.length} done
        </span>
        <span className="grow" />
        <button
          className="btn quiet"
          onClick={() => {
            write(DISMISS);
            setHidden(true);
          }}
        >
          Dismiss
        </button>
      </header>
      <div className="gs-bar" role="progressbar" aria-valuemin={0} aria-valuemax={steps.length} aria-valuenow={done} aria-label="Getting started progress">
        <i style={{ width: `${(done / steps.length) * 100}%` }} />
      </div>
      <ol className="gs-steps">
        {steps.map((s) => (
          <li key={s.id} className={`${s.done ? 'done' : ''}${s === next ? ' next' : ''}`}>
            <span className="gs-mark" aria-hidden="true">
              {s.done ? <Icon name="check" size={14} /> : steps.indexOf(s) + 1}
            </span>
            <div>
              <b>
                {s.title}
                {s.done && <span className="sr-only"> (done)</span>}
              </b>
              <p className="muted">{s.detail}</p>
              {s === next && s.go && (
                <button
                  className="btn primary"
                  onClick={() => {
                    if (s.go!.view === 'activity') {
                      write(SEEN_ACTIVITY);
                      setSeen(true);
                    }
                    set({ view: s.go!.view });
                  }}
                >
                  {s.go.label}
                </button>
              )}
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}
