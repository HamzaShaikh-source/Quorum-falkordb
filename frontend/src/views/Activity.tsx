import { useEffect, useMemo, useState } from 'react';
import { SEEN_ACTIVITY } from '../components/GettingStarted';
import { useQuery } from '@tanstack/react-query';
import { API_MODE } from '../api/client';
import { Icon } from '../components/Icon';
import { fmt } from '../context/tokens';
import { useStore } from '../state/store';
import { sentenceFor } from '../workspace/audit';
import { useLive } from '../workspace/live';
import { downloadAuditCsv, getAudit, type AuditEvent } from '../workspace/server';

type Sev = 'all' | AuditEvent['severity'];

const SEV_LABEL: Record<AuditEvent['severity'], string> = { read: 'Looked something up', write: 'Changed memory', alert: 'Needs a person', failed: 'Refused' };
const SEV_ICON: Record<AuditEvent['severity'], string> = { read: 'search', write: 'fact', alert: 'shield', failed: 'alert' };

const time = (ts: number) => new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
const dayKey = (ts: number) => new Date(ts).toDateString();
function dayLabel(ts: number): string {
  const d = new Date(ts);
  const today = new Date();
  const y = new Date(today.getTime() - 86_400_000);
  if (d.toDateString() === today.toDateString()) return 'Today';
  if (d.toDateString() === y.toDateString()) return 'Yesterday';
  return d.toLocaleDateString([], { weekday: 'long', month: 'long', day: 'numeric' });
}

/** The sample build has no audit endpoint: turn the simulated feed into the same shape so the page can be reviewed. */
function useSampleEvents(): AuditEvent[] {
  const activity = useLive((s) => s.activity);
  return useMemo(
    () =>
      activity.map((a) => {
        const s = sentenceFor(a.tool, a.summary, true, true);
        return { id: a.id, ts: a.ts, who: a.member, team: undefined, client: undefined, tool: a.tool, text: s.text, severity: s.severity, ok: true, tokens: a.used, saved: a.naive > a.used ? a.naive - a.used : 0 };
      }),
    [activity],
  );
}

export function Activity() {
  const live = API_MODE === 'live';
  useEffect(() => {
    try {
      localStorage.setItem(SEEN_ACTIVITY, '1');
    } catch {
      /* ignore */
    }
  }, []);
  const pushToast = useStore((s) => s.pushToast);
  const setView = useStore((s) => s.set);
  const [sev, setSev] = useState<Sev>('all');
  const [who, setWho] = useState('all');
  const q = useQuery({ queryKey: ['audit'], queryFn: () => getAudit({}), enabled: live, refetchInterval: 6000 });
  const sample = useSampleEvents();
  const liveEvents = q.data?.events;
  const all = useMemo(() => (live ? (liveEvents ?? []) : sample), [live, liveEvents, sample]);
  const people = useMemo(() => Array.from(new Set(all.map((e) => e.who))).sort(), [all]);
  const rows = useMemo(() => all.filter((e) => (sev === 'all' || e.severity === sev) && (who === 'all' || e.who === who)), [all, sev, who]);
  const groups = useMemo(() => {
    const out: { key: string; label: string; rows: AuditEvent[] }[] = [];
    for (const e of rows.slice(0, 300)) {
      const k = dayKey(e.ts);
      const last = out[out.length - 1];
      if (last && last.key === k) last.rows.push(e);
      else out.push({ key: k, label: dayLabel(e.ts), rows: [e] });
    }
    return out;
  }, [rows]);

  const count = (s: AuditEvent['severity']) => all.filter((e) => e.severity === s).length;
  const attention = count('alert') + count('failed');
  const saved = all.reduce((n, e) => n + e.saved, 0);

  return (
    <div className="page au" tabIndex={0}>
      <header className="page-head">
        <p className="page-tag">
          <span>[09]</span> Activity
        </p>
        <h1>What your agents did</h1>
        <p>A plain record for people who do not read graphs: who did what, when, and what it cost. Nothing here needs a diagram to understand, and it exports as a spreadsheet for audit.</p>
      </header>

      <section className="card wide" aria-label="Summary">
        <dl className="au-tiles">
        <div>
          <dt className="muted">Events</dt>
          <dd className="num">{fmt(all.length)}</dd>
        </div>
        <div>
          <dt className="muted">People and agents</dt>
          <dd className="num">{people.length}</dd>
        </div>
        <div>
          <dt className="muted">Tokens not sent</dt>
          <dd className="num ok-text">{fmt(saved)}</dd>
        </div>
        <div className={attention ? 'au-attn' : ''}>
          <dt className="muted">Needed a person, or refused</dt>
          <dd className={`num ${attention ? 'warn-text' : ''}`}>{attention}</dd>
        </div>
        </dl>
      </section>

      <section className="card wide" aria-label="Activity log">
        <header className="card-h">
          <h2>Activity log</h2>
          {!live && (
            <span className="chip warn" title="The sample build has no backend. These rows are generated in your browser from the real context packer.">
              Sample data
            </span>
          )}
          <span className="grow" />
          {live && (
            <button
              className="btn quiet"
              onClick={() => void downloadAuditCsv().then((ok) => pushToast(ok ? 'Saved quorum-audit.csv' : 'Could not export the log', ok ? 'good' : 'warn'))}
            >
              <Icon name="doc" size={14} /> Export CSV
            </button>
          )}
        </header>
        <div className="cn-filters">
          <div className="seg" role="group" aria-label="Show">
            {(['all', 'write', 'alert', 'failed', 'read'] as const).map((k) => (
              <button key={k} className={`seg-btn${sev === k ? ' on' : ''}`} aria-pressed={sev === k} onClick={() => setSev(k)}>
                {k === 'all' ? 'Everything' : SEV_LABEL[k]}
              </button>
            ))}
          </div>
          <label className="field inline">
            <span>Who</span>
            <select value={who} onChange={(e) => setWho(e.target.value)}>
              <option value="all">Everyone</option>
              {people.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </label>
        </div>

        {live && q.isLoading && <p className="muted cn-empty">Loading the log…</p>}
        {live && q.isError && <p className="cn-empty bad-text">Could not load the log from the backend.</p>}
        {groups.length ? (
          groups.map((g) => (
            <div key={g.key} className="au-day">
              <h3>{g.label}</h3>
              <ul className="au-list">
                {g.rows.map((e) => (
                  <li key={e.id} className={`au-row ${e.severity}`}>
                    <time className="muted num">{time(e.ts)}</time>
                    <span className={`au-ico ${e.severity}`} role="img" title={SEV_LABEL[e.severity]} aria-label={SEV_LABEL[e.severity]}>
                      <Icon name={SEV_ICON[e.severity]} size={14} />
                    </span>
                    <p>
                      <b>{e.who}</b> {e.text}
                      {e.severity === 'failed' && e.text.includes('Team page') && (
                        <button className="btn quiet au-fix" onClick={() => setView({ view: 'team' })}>
                          Change permissions
                        </button>
                      )}
                      {(e.team || e.client) && (
                        <small className="muted">
                          {[e.team, e.client].filter(Boolean).join(' · ')}
                        </small>
                      )}
                    </p>
                    <span className="au-tok num muted" title="Tokens the agent received for this step, and tokens it did not need">
                      {e.tokens ? `${fmt(e.tokens)} tok` : ''}
                      {e.saved > 0 && <b className="ok-text"> −{fmt(e.saved)}</b>}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ))
        ) : (
          !q.isLoading && <p className="muted cn-empty">{all.length ? 'Nothing matches those filters.' : 'No activity yet. When an AI connected to Quorum looks something up or records a fact, it appears here in plain words.'}</p>
        )}
        {live && q.data && <p className="cn-help">The log keeps {q.data.retention}. Export it before it rolls over if you need a longer record.</p>}
      </section>
    </div>
  );
}
