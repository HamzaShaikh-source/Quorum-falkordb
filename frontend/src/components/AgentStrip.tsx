import { useEffect, useState } from 'react';
import { PURPOSE, useAgentFeed } from '../workspace/agentFeed';
import { useStore } from '../state/store';
import { Icon } from './Icon';

const ICON = { read: 'search', write: 'fact', alert: 'shield', failed: 'alert' } as const;
const WORD = { read: 'Reading', write: 'Writing', alert: 'Safety step', failed: 'Refused' } as const;

function ago(ts: number, now: number): string {
  const s = Math.max(0, Math.round((now - ts) / 1000));
  if (s < 5) return 'just now';
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  return `${Math.floor(s / 3600)} h ago`;
}

/**
 * "AI at work": everything connected agents are doing with the memory right now, in sentences, newest first.
 * It answers "what is the AI using this for?" without opening any other screen.
 */
export function AgentStrip({ rows = 5 }: { rows?: number }) {
  const { rows: feed, loading, failed } = useAgentFeed(Math.max(rows, 12));
  const set = useStore((s) => s.set);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 5000);
    return () => clearInterval(t);
  }, []);
  const recent = feed.filter((r) => now - r.ts < 120_000).length;
  const counts = { read: 0, write: 0, alert: 0, failed: 0 };
  for (const r of feed) {
    if (r.severity in counts) counts[r.severity as keyof typeof counts] += 1;
  }
  const busy = recent > 0;
  return (
    <section className="card wide agent-strip" aria-label="AI at work">
      <header className="card-h">
        <h2>
          <span className={`as-dot${busy ? ' on' : ''}`} aria-hidden="true" /> AI at work
        </h2>
        <span className="card-sub">{busy ? `${recent} step${recent === 1 ? '' : 's'} in the last 2 minutes` : 'Quiet right now'}</span>
        <span className="grow" />
        <button className="btn quiet" onClick={() => set({ helpOpen: true })}>
          <Icon name="help" size={14} /> What am I looking at?
        </button>
      </header>
      <ul className="as-counts" aria-label="Recent steps by kind">
        {(['read', 'write', 'alert', 'failed'] as const).map((k) => (
          <li key={k} className={`as-c ${k}${counts[k] ? ' has' : ''}`} title={PURPOSE[k]}>
            <b className="num">{counts[k]}</b> {WORD[k]}
          </li>
        ))}
      </ul>
      {loading && <p className="muted">Loading what your agents are doing…</p>}
      {failed && <p className="bad-text">Could not load agent activity from the backend.</p>}
      {feed.length ? (
        <ol className="as-feed">
          {feed.slice(0, Math.max(0, rows)).map((r) => (
            <li key={r.id} className={`as-row ${r.severity}`}>
              <span className={`au-ico ${r.severity}`} aria-hidden="true">
                <Icon name={ICON[r.severity] ?? 'sparkle'} size={14} />
              </span>
              <p>
                <b>{r.who}</b> {r.text}
                <small className="muted">{PURPOSE[r.severity]}</small>
              </p>
              <time className="muted num">{ago(r.ts, now)}</time>
            </li>
          ))}
        </ol>
      ) : (
        !loading &&
        !failed && (
          <div className="as-empty">
            <p>
              <b>No agent has used the memory yet.</b>
            </p>
            <p className="muted">Connect an AI and every lookup it makes and every fact it saves will appear here the moment it happens.</p>
            <button className="btn primary" onClick={() => set({ view: 'connect' })}>
              <Icon name="link" size={14} /> Connect an AI
            </button>
          </div>
        )
      )}
      <button className="btn quiet as-all" onClick={() => set({ view: 'activity' })}>
        Open the full activity log
      </button>
    </section>
  );
}
