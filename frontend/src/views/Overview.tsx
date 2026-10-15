import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { API_MODE } from '../api/client';
import { Bars, Donut, EmptyGraphArt, Legend, StackBar, type Slice } from '../components/charts';
import { Icon } from '../components/Icon';
import { fmt } from '../context/tokens';
import { useStore, type View } from '../state/store';
import { viewKey } from '../state/views';
import { sentenceFor } from '../workspace/audit';
import { useLive } from '../workspace/live';
import { getActivity, getAudit, getTokenStats, type AuditEvent } from '../workspace/server';
import { useWs } from '../workspace/useWs';

const HOUR = 3_600_000;
const SEV_ICON: Record<AuditEvent['severity'], string> = { read: 'search', write: 'fact', alert: 'shield', failed: 'alert' };
const COLORS = { healthy: '#3ddbb0', quarantined: '#ff6a55', superseded: '#f5c451', source: '#7aa7ff', fact: '#3ddbb0', conclusion: '#b79cff', action: '#f5c451' };

function Go({ to, children, primary = false }: { to: View; children: React.ReactNode; primary?: boolean }) {
  const set = useStore((s) => s.set);
  const story = useStore((s) => s.story);
  const key = viewKey(to, story);
  return (
    <button className={`btn ${primary ? 'primary' : 'quiet'}`} onClick={() => set({ view: to })} title={key ? `Shortcut: ${key}` : undefined}>
      {children}
    </button>
  );
}

export function Overview() {
  const live = API_MODE === 'live';
  const nodes = useStore((s) => s.nodes);
  const handoffs = useStore((s) => s.handoffs);
  const set = useStore((s) => s.set);
  const mcp = useLive((s) => s.mcp);
  const conn = useStore((s) => s.conn);
  const ws = useWs();
  const simulated = useLive((s) => s.activity);
  const totals = useLive((s) => s.totals);

  const act = useQuery({ queryKey: ['activity'], queryFn: () => getActivity(), enabled: live, refetchInterval: 8000 });
  const stats = useQuery({ queryKey: ['token-stats'], queryFn: getTokenStats, enabled: live, refetchInterval: 10_000 });
  const audit = useQuery({ queryKey: ['audit', 'recent'], queryFn: () => getAudit({ limit: 6 }), enabled: live, refetchInterval: 8000 });

  const list = useMemo(() => Object.values(nodes), [nodes]);
  const by = (f: (n: (typeof list)[number]) => boolean) => list.filter(f).length;
  const status: Slice[] = [
    { label: 'Healthy', value: by((n) => n.status === 'HEALTHY' && !n.stale), color: COLORS.healthy },
    { label: 'Quarantined', value: by((n) => n.status === 'QUARANTINED'), color: COLORS.quarantined },
    { label: 'Replaced or out of date', value: by((n) => n.status === 'SUPERSEDED' || (n.status === 'HEALTHY' && !!n.stale)), color: COLORS.superseded },
  ];
  const kinds: Slice[] = [
    { label: 'Sources', value: by((n) => n.kind === 'source'), color: COLORS.source },
    { label: 'Facts', value: by((n) => n.kind === 'fact'), color: COLORS.fact },
    { label: 'Conclusions', value: by((n) => n.kind === 'conclusion'), color: COLORS.conclusion },
    { label: 'Actions', value: by((n) => n.kind === 'action'), color: COLORS.action },
  ];
  const quarantined = status[1]!.value;
  const pendingReviews = Object.values(handoffs).filter((h) => h.kind === 'REVIEW' && h.status === 'PENDING').length;

  // calls per hour over the last 24 hours (live: from the call log; sample build: the simulated feed)
  const stamps = live ? (act.data ?? []).map((c) => c.ts) : simulated.map((a) => a.ts);
  const refused = live ? (act.data ?? []).filter((c) => !c.ok).length : 0;
  const now = useMemo(() => Date.now(), [stamps.length]); // eslint-disable-line react-hooks/exhaustive-deps
  const buckets = useMemo(() => {
    const v = new Array<number>(24).fill(0);
    for (const t of stamps) {
      const ago = Math.floor((now - t) / HOUR);
      if (ago >= 0 && ago < 24) v[23 - ago]! += 1;
    }
    return v;
  }, [stamps, now]);
  const callCount = live ? (act.data?.length ?? 0) : totals.calls;
  const saved = live ? (stats.data?.saved ?? 0) : Math.max(0, totals.naive - totals.used);
  const savedPct = live ? (stats.data?.saved_pct ?? 0) : totals.naive ? Math.round(((totals.naive - totals.used) / totals.naive) * 100) : 0;
  const total24 = buckets.reduce((a, b) => a + b, 0);

  const recent: { id: number; ts: number; who: string; text: string; severity: AuditEvent['severity'] }[] = live
    ? (audit.data?.events ?? []).map((e) => ({ id: e.id, ts: e.ts, who: e.who, text: e.text, severity: e.severity }))
    : simulated.slice(0, 6).map((a) => ({ id: a.id, ts: a.ts, who: a.member, ...sentenceFor(a.tool, a.summary, true, true) }));

  const empty = list.length === 0 && ws.members.length === 0 && callCount === 0;
  const online = ws.members.filter((m) => m.status === 'online').length;
  const hubOk = live ? mcp.state === 'connected' : true;

  return (
    <div className="page ov" tabIndex={0}>
      <header className="page-head">
        <p className="page-tag">
          <span>[00]</span> Overview
        </p>
        <h1>{empty ? 'Welcome. Your memory is empty.' : 'Your shared memory at a glance'}</h1>
        <p>{empty ? 'Nothing here is sample data. Add a person, connect their AI, and what it learns appears on this page.' : 'What your agents know, who is connected, and what needs a person.'}</p>
      </header>

      <section className="ov-hub card wide" aria-label="Hub status">
        <span className={`ov-dot ${hubOk ? 'ok' : 'bad'}`} aria-hidden="true" />
        <b>{hubOk ? 'Hub is running' : mcp.state === 'checking' ? 'Checking the hub…' : 'MCP endpoint not reachable'}</b>
        <span className="muted">{live ? `FalkorDB connected · MCP ${mcp.state === 'connected' ? `answers in ${mcp.latencyMs} ms` : mcp.state} · live stream ${conn}` : 'Sample data: nothing here talks to a real hub.'}</span>
        <span className="grow" />
        <Go to="connect">
          <Icon name="link" size={14} /> Connect an AI
        </Go>
      </section>

      {empty ? (
        <section className="ov-empty card wide" aria-label="Get started">
          <EmptyGraphArt size={220} />
          <div className="ov-steps">
            <h2>Three steps to a working memory</h2>
            <ol>
              {(
                [
                  ['team', 'Add a person', 'Every person gets their own token, so you can see who did what.', 'Open Team'],
                  ['connect', 'Connect their AI', 'Paste one snippet into Claude, Cursor, VS Code or Antigravity.', 'Open Connect'],
                  ['activity', 'Watch it work', 'Every lookup and every fact it saves is written down in plain words.', 'Open Activity'],
                ] as [View, string, string, string][]
              ).map(([to, title, text, cta], i) => (
                <li key={to}>
                  <span className="ov-n num">{i + 1}</span>
                  <div>
                    <b>{title}</b>
                    <p className="muted">{text}</p>
                  </div>
                  <Go to={to} primary={i === 0}>
                    {cta}
                  </Go>
                </li>
              ))}
            </ol>
          </div>
        </section>
      ) : (
        <>
          <div className="ov-grid">
            <section className="card ov-card" aria-label="Memory health">
              <h2>Memory</h2>
              <div className="ov-row">
                <Donut slices={status} centre={list.length} sub={list.length === 1 ? 'node' : 'nodes'} />
                <Legend items={status} />
              </div>
              <StackBar parts={kinds} />
              <Legend items={kinds} />
              <button className="btn quiet" onClick={() => set({ view: 'console' })}>
                <Icon name="graph" size={14} /> Open the graph
              </button>
            </section>

            <section className="card ov-card" aria-label="People and AIs">
              <h2>People and AIs</h2>
              <p className="ov-big num">
                {online}
                <small> of {ws.members.length} online</small>
              </p>
              {ws.members.length === 0 ? (
                <p className="muted">No one added yet.</p>
              ) : (
                <ul className="ov-people">
                  {ws.members.slice(0, 8).map((m) => (
                    <li key={m.id} title={`${m.name}: ${m.status}`}>
                      <span className="avatar">{m.name.slice(0, 1)}</span>
                      <i className={`pres ${m.status === 'unknown' ? 'offline' : m.status}`} aria-hidden="true" />
                      <span className="ov-name">{m.name}</span>
                      <span className="sr-only">{m.status}</span>
                    </li>
                  ))}
                </ul>
              )}
              <Go to="team">Manage team</Go>
            </section>

            <section className="card ov-card" aria-label="Tokens saved">
              <h2>Tokens saved</h2>
              <p className="ov-big num ok-text">{fmt(saved)}</p>
              <div className="ov-meter" role="img" aria-label={`${savedPct}% fewer tokens than a typical retriever`}>
                <i style={{ width: `${Math.max(2, Math.min(100, savedPct))}%` }} />
              </div>
              <p className="muted">
                {savedPct}% fewer than a typical top-8 retriever, measured on {fmt(live ? (stats.data?.measured_calls ?? 0) : totals.calls)} real lookups.
              </p>
              <Go to="context">See how</Go>
            </section>

            <section className={`card ov-card${quarantined + pendingReviews + refused > 0 ? ' attn' : ''}`} aria-label="Needs attention">
              <h2>Needs attention</h2>
              <ul className="ov-attn">
                <li className={quarantined ? 'warn' : ''}>
                  <b className="num">{quarantined}</b> quarantined {quarantined === 1 ? 'node' : 'nodes'}
                </li>
                <li className={pendingReviews ? 'warn' : ''}>
                  <b className="num">{pendingReviews}</b> waiting for a person to review
                </li>
                <li className={refused ? 'warn' : ''}>
                  <b className="num">{refused}</b> refused tool {refused === 1 ? 'call' : 'calls'}
                </li>
              </ul>
              <Go to="activity">Read the log</Go>
            </section>
          </div>

          <section className="card wide" aria-label="Activity over the last 24 hours">
            <header className="card-h">
              <h2>Activity, last 24 hours</h2>
              <span className="card-sub num">{fmt(total24)} calls</span>
            </header>
            <Bars values={buckets} labels={['24h ago', '12h ago', 'now']} title={`Tool calls per hour over the last 24 hours, ${total24} in total`} />
          </section>

          <section className="card wide" aria-label="Recent activity">
            <header className="card-h">
              <h2>Latest</h2>
              <span className="grow" />
              <Go to="activity">All activity</Go>
            </header>
            {recent.length ? (
              <ul className="au-list">
                {recent.map((e) => (
                  <li key={e.id} className={`au-row ${e.severity}`}>
                    <time className="muted num">{new Date(e.ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</time>
                    <span className={`au-ico ${e.severity}`} role="img" aria-label={e.severity}>
                      <Icon name={SEV_ICON[e.severity]} size={14} />
                    </span>
                    <p>
                      <b>{e.who}</b> {e.text}
                    </p>
                    <span />
                  </li>
                ))}
              </ul>
            ) : (
              <p className="muted">Nothing yet. When a connected AI looks something up or saves a fact, it appears here.</p>
            )}
          </section>
        </>
      )}
    </div>
  );
}
