import { PageTag } from '../components/PageTag';
import { Network } from '../components/Network';
import { useMemo, useState } from 'react';
import { Icon } from '../components/Icon';
import { fmt } from '../context/tokens';
import { useStore } from '../state/store';
import { CAPABILITIES, LEVELS, levelName, levelSummary } from '../workspace/roles';
import { copyText } from '../workspace/clipboard';
import { CLIENTS, type ClientId } from '../workspace/mcp';
import { PLANS, type MemberRole, type Plan, type Scope } from '../workspace/store';
import { useWs, type Ws } from '../workspace/useWs';

const ROLE_HELP: Record<MemberRole, string> = { agent: 'reads, saves facts, runs actions', auditor: 'also quarantines and restores', human: 'also approves reviews and manages people' };

function ago(ts?: number): string {
  if (!ts) return 'never';
  const s = Math.max(0, Math.round((Date.now() - ts) / 1000));
  if (s < 10) return 'just now';
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  return `${Math.round(s / 3600)}h ago`;
}

function Issued({ ws }: { ws: Ws }) {
  const pushToast = useStore((s) => s.pushToast);
  const set = useStore((s) => s.set);
  if (!ws.issued) return null;
  return (
    <section className="card wide tm-issued" role="alert" aria-label="New token">
      <h2>{ws.issued.member}&apos;s token</h2>
      <p>Copy it now. It is shown once and only its hash is stored, so it cannot be shown again (rotate it to get a new one).</p>
      <div className="block-cmd">
        <code className="mono">{ws.issued.token}</code>
        <button
          className="btn quiet"
          onClick={() => void copyText(ws.issued?.token ?? '').then((ok) => pushToast(ok ? 'Token copied' : 'Copy was blocked by the browser', ok ? 'good' : 'warn'))}
        >
          <Icon name="copy" size={13} /> Copy
        </button>
      </div>
      <div className="tm-issued-actions">
        <button className="btn primary" onClick={() => set({ view: 'connect' })}>
          Set up their AI
        </button>
        <button className="btn quiet" onClick={ws.dismissIssued}>
          I have saved it
        </button>
      </div>
    </section>
  );
}

function Org({ ws }: { ws: Ws }) {
  const [err, setErr] = useState<string | null>(null);
  const pushToast = useStore((s) => s.pushToast);
  const used = ws.members.length;
  const limit = ws.limits.seats;
  const save = async (p: Promise<string | null>) => setErr(await p);
  return (
    <section className="card wide tm-org" aria-label="Workspace">
      <label className="field">
        <span>Company</span>
        <input key={ws.org} defaultValue={ws.org} onBlur={(e) => e.target.value.trim() !== ws.org && void save(ws.rename(e.target.value))} onKeyDown={(e) => e.key === 'Enter' && (e.target as HTMLInputElement).blur()} />
      </label>
      <label className="field">
        <span>Plan</span>
        <select value={ws.plan} onChange={(e) => void save(ws.setPlan(e.target.value as Plan))}>
          {(Object.keys(PLANS) as Plan[]).map((p) => (
            <option key={p} value={p}>
              {PLANS[p].label}: up to {PLANS[p].seats} seats, {PLANS[p].teams} teams
            </option>
          ))}
        </select>
      </label>
      <div className="tm-seats" aria-label={`${used} of ${limit} seats used`}>
        <div className="tm-seats-top">
          <span>Seats</span>
          <b className="num">
            {used} / {limit}
          </b>
        </div>
        <div className="ctx-track" aria-hidden="true">
          <i className={limit && used >= limit ? 'bad' : 'ok'} style={{ width: `${limit ? Math.min(100, (used / limit) * 100) : 0}%` }} />
        </div>
        <small className="muted">{ws.limits.note}</small>
      </div>
      <label className="field grow">
        <span>Workspace hub (the URL laptops connect to)</span>
        <input key={ws.hub} defaultValue={ws.hub} placeholder="https://quorum.your-company.com" onBlur={(e) => e.target.value.trim() !== ws.hub && void save(ws.setHub(e.target.value.trim()))} spellCheck={false} />
      </label>
      {ws.reset && (
        <button
          className="btn quiet"
          onClick={() => {
            ws.reset?.();
            pushToast('Workspace reset to the sample');
          }}
        >
          Reset sample
        </button>
      )}
      <p className="tm-err" role="alert">
        {err ?? ws.error}
      </p>
    </section>
  );
}

function Teams({ ws }: { ws: Ws }) {
  const [name, setName] = useState('');
  const [seats, setSeatsN] = useState(5);
  const [err, setErr] = useState<string | null>(null);
  return (
    <section className="card wide" aria-label="Teams">
      <header className="card-h">
        <h2>Teams</h2>
        <span className="card-sub">
          {ws.teams.length} of {ws.limits.teams}
        </span>
      </header>
      {ws.teams.length === 0 && <p className="muted">No teams yet. Add the first one below, then add people to it.</p>}
      <ul className="tm-teams">
        {ws.teams.map((t) => {
          const n = ws.members.filter((m) => m.teamId === t.id).length;
          return (
            <li key={t.id} className="tm-team">
              <div>
                <b>{t.name}</b>
                <small className="muted num">
                  {n} of {t.seats} seats
                </small>
              </div>
              <div className="tm-stepper" role="group" aria-label={`${t.name} seats`}>
                <button aria-label={`Fewer seats for ${t.name}`} disabled={t.seats <= Math.max(1, n)} onClick={() => void ws.setSeats(t.id, t.seats - 1).then(setErr)}>
                  −
                </button>
                <span className="num">{t.seats}</span>
                <button aria-label={`More seats for ${t.name}`} onClick={() => void ws.setSeats(t.id, t.seats + 1).then(setErr)}>
                  +
                </button>
              </div>
              <button
                className="btn quiet"
                onClick={() => {
                  if (n === 0 || window.confirm(`Remove ${t.name} and its ${n} members?`)) void ws.removeTeam(t.id).then(setErr);
                }}
              >
                Remove
              </button>
            </li>
          );
        })}
      </ul>
      <form
        className="tm-form"
        onSubmit={(e) => {
          e.preventDefault();
          void ws.addTeam(name, seats).then((r) => {
            setErr(r);
            if (!r) setName('');
          });
        }}
      >
        <label className="field grow">
          <span>New team</span>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Customer success" />
        </label>
        <label className="field narrow">
          <span>Seats</span>
          <input type="number" min={1} max={200} value={seats} onChange={(e) => setSeatsN(Number(e.target.value) || 1)} />
        </label>
        <button className="btn primary">Add team</button>
      </form>
      <p className="tm-err" role="alert">
        {err}
      </p>
    </section>
  );
}

function Members({ ws }: { ws: Ws }) {
  const set = useStore((s) => s.set);
  const pushToast = useStore((s) => s.pushToast);
  const [name, setName] = useState('');
  const [role, setRole] = useState<MemberRole>('agent');
  const [teamId, setTeamId] = useState('');
  const [client, setClient] = useState<ClientId>('claude-code');
  const [device, setDevice] = useState('');
  const [days, setDays] = useState('90');
  const [now] = useState(() => Date.now());
  const [err, setErr] = useState<string | null>(null);
  const [shown, setShown] = useState<string | null>(null);
  const team = (id: string) => ws.teams.find((t) => t.id === id)?.name ?? '—';
  const effectiveTeam = teamId || ws.teams[0]?.id || '';

  return (
    <section className="card wide" aria-label="Members">
      <header className="card-h">
        <h2>Members and their AIs</h2>
        <span className="card-sub">Each person connects their own laptop and AI with their own token</span>
      </header>
      {ws.members.length === 0 ? (
        <p className="muted">No members yet. Add one below to get their token.</p>
      ) : (
        <div className="cn-table-wrap" tabIndex={0} role="region" aria-label="Table (scrolls sideways on a narrow screen)">
          <table className="cn-table tm-table">
            <thead>
              <tr>
                <th scope="col">Member</th>
                <th scope="col">Team</th>
                <th scope="col">Permission level</th>
                <th scope="col">AI client</th>
                <th scope="col">Status</th>
                <th scope="col">Calls</th>
                <th scope="col">Tokens saved</th>
                <th scope="col">Token</th>
                <th scope="col">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {ws.members.map((m) => (
                <tr key={m.id}>
                  <th scope="row">
                    <div className="tm-who">
                      <span className="avatar">{[...m.name][0] ?? ''}</span>
                      <span>{m.name}</span>
                      <small className="muted">
                        {levelName(m.role)} · {m.device}
                      </small>
                    </div>
                  </th>
                  <td>{team(m.teamId)}</td>
                  <td>
                    <select
                      className={`lv-select ${m.role}`}
                      aria-label={`Permission level for ${m.name}`}
                      value={m.role}
                      title={levelSummary(m.role)}
                      onChange={(e) => {
                        const next = e.target.value as MemberRole;
                        if (next === 'human' && !window.confirm(`Make ${m.name} an Admin? Admins can manage people, tokens and approvals.`)) return;
                        void ws.setLevel(m.id, next).then((r) => {
                          setErr(r);
                          if (!r) pushToast(`${m.name} is now a ${levelName(next)}. It applies on their next call.`);
                        });
                      }}
                    >
                      {LEVELS.map((l) => (
                        <option key={l.role} value={l.role}>
                          {l.name}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td>{CLIENTS.find((c) => c.id === m.client)?.name ?? m.client}</td>
                  <td>
                    <span className={`pres ${m.status === 'unknown' ? 'offline' : m.status}`} aria-hidden="true" /> {m.status}
                    {ws.live && <small className="muted">{m.lastSeen ? `last call ${ago(m.lastSeen)}` : 'no calls yet'}</small>}
                  </td>
                  <td className="num">{fmt(m.calls)}</td>
                  <td className="num">{fmt(m.tokensSaved)}</td>
                  <td>
                    <code className="mono tm-token">{shown === m.id && m.token ? m.token : m.tokenHint}</code>
                    {ws.live && <small className={m.expiresAt && m.expiresAt < now ? 'bad-text' : 'muted'}>{expiry(m.expiresAt, now)}</small>}
                    {m.token && (
                      <button className="btn quiet" aria-label={`${shown === m.id ? 'Hide' : 'Show'} ${m.name}'s sample token`} onClick={() => setShown(shown === m.id ? null : m.id)}>
                        {shown === m.id ? 'Hide' : 'Show'}
                      </button>
                    )}
                  </td>
                  <td className="tm-actions">
                    <button className="btn quiet" onClick={() => set({ view: 'connect' })}>
                      Set up
                    </button>
                    <button className="btn quiet" aria-label={`Rotate ${m.name}'s token`} onClick={() => void ws.rotate(m.id).then(setErr)}>
                      Rotate
                    </button>
                    <button className="btn quiet" aria-label={`Remove ${m.name}`} onClick={() => void ws.removeMember(m.id).then(setErr)}>
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <form
        className="tm-form"
        onSubmit={(e) => {
          e.preventDefault();
          if (!effectiveTeam) return setErr('Add a team first.');
          void ws.addMember({ name, role, teamId: effectiveTeam, client, device: device.trim(), expiresInDays: Number(days) }).then((r) => {
            setErr(r);
            if (!r) {
              setName('');
              setDevice('');
              pushToast(ws.live ? 'Member added. Copy their token now.' : 'Member added');
            }
          });
        }}
      >
        <label className="field">
          <span>Name</span>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Priya" />
        </label>
        <label className="field">
          <span>Team</span>
          <select value={effectiveTeam} onChange={(e) => setTeamId(e.target.value)}>
            {ws.teams.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Role</span>
          <select value={role} onChange={(e) => setRole(e.target.value as MemberRole)}>
            {(Object.keys(ROLE_HELP) as MemberRole[]).map((r) => (
              <option key={r} value={r}>
                {levelName(r)}: {ROLE_HELP[r]}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Their AI</span>
          <select value={client} onChange={(e) => setClient(e.target.value as ClientId)}>
            {CLIENTS.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Token lasts</span>
          <select value={days} onChange={(e) => setDays(e.target.value)}>
            <option value="30">30 days</option>
            <option value="90">90 days</option>
            <option value="365">1 year</option>
            <option value="0">Never expires</option>
          </select>
        </label>
        <button className="btn primary">Add member</button>
      </form>
      <p className="tm-err" role="alert">
        {err}
      </p>
    </section>
  );
}

const MARK: Record<string, { cls: string; text: string; icon: string }> = {
  true: { cls: 'yes', text: 'Yes', icon: 'check' },
  false: { cls: 'no', text: 'No', icon: 'x' },
  limited: { cls: 'part', text: 'Limited', icon: 'check' },
};

/** What each level can do, as a table you can read at a glance. It is the hub's own table, so it cannot overpromise. */
function LevelGuide({ ws }: { ws: Ws }) {
  return (
    <section className="card wide" aria-label="Permission levels">
      <header className="card-h">
        <h2>Permission levels</h2>
        <span className="card-sub">Pick one per person below. A change applies on their next call.</span>
      </header>
      <div className="cn-table-wrap" tabIndex={0} role="region" aria-label="What each permission level can do (scrolls sideways on a narrow screen)">
        <table className="lv-table">
          <thead>
            <tr>
              <th scope="col">
                <span className="sr-only">What it can do</span>
              </th>
              {LEVELS.map((l) => (
                <th scope="col" key={l.role} className={`lv-head ${l.role}`}>
                  <b>{l.name}</b>
                  <small>{ws.members.filter((m) => m.role === l.role).length} {ws.members.filter((m) => m.role === l.role).length === 1 ? 'person' : 'people'}</small>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {CAPABILITIES.map((c) => (
              <tr key={c.key}>
                <th scope="row">
                  {c.label}
                  <small className="muted">{c.detail}</small>
                </th>
                {LEVELS.map((l) => {
                  const v = String(c[l.role]);
                  const mk = MARK[v]!;
                  return (
                    <td key={l.role} className={`lv-cell ${mk.cls}`} title={v === 'limited' ? c.note : undefined}>
                      <span className="lv-mark" role="img" aria-label={`${l.name}: ${mk.text}${v === 'limited' && c.note ? `. ${c.note}` : ''}`}>
                        <Icon name={mk.icon} size={13} />
                      </span>
                      {v === 'limited' && <small className="muted">{c.note?.replace(/^Contributors can /, '').replace(/, not.*$/, '')}</small>}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <ul className="lv-sum">
        {LEVELS.map((l) => (
          <li key={l.role} className={l.role}>
            <b>{l.name}</b> {l.summary}
          </li>
        ))}
      </ul>
    </section>
  );
}

function groups(ws: Ws): string[][] {
  const adj = new Map<string, string[]>(ws.members.map((m) => [m.id, []]));
  for (const l of ws.links) {
    if (l.paused) continue;
    adj.get(l.a)?.push(l.b);
    adj.get(l.b)?.push(l.a);
  }
  const seen = new Set<string>();
  const out: string[][] = [];
  for (const m of ws.members) {
    if (seen.has(m.id)) continue;
    const comp: string[] = [];
    const q = [m.id];
    seen.add(m.id);
    while (q.length) {
      const c = q.shift() as string;
      comp.push(c);
      for (const n of adj.get(c) ?? []) {
        if (!seen.has(n)) {
          seen.add(n);
          q.push(n);
        }
      }
    }
    out.push(comp);
  }
  return out.sort((a, b) => b.length - a.length);
}

function Mesh({ ws }: { ws: Ws }) {
  const [a, setA] = useState('');
  const [b, setB] = useState('');
  const [scope, setScope] = useState<Scope>('team');
  const [err, setErr] = useState<string | null>(null);
  const gs = useMemo(() => groups(ws), [ws]);
  const gOf = new Map<string, number>();
  gs.forEach((g, i) => g.forEach((id) => gOf.set(id, i)));
  const n = ws.members.length;
  const R = 112;
  const pos = new Map(ws.members.map((m, i) => [m.id, { x: 160 + R * Math.cos((i / Math.max(1, n)) * Math.PI * 2 - Math.PI / 2), y: 140 + R * Math.sin((i / Math.max(1, n)) * Math.PI * 2 - Math.PI / 2) }]));
  const name = (id: string) => ws.members.find((m) => m.id === id)?.name ?? id;
  const shared = gs.filter((g) => g.length > 1);

  return (
    <section className="card wide" aria-label="Share context between members">
      <header className="card-h">
        <h2>Link members so their AIs share context</h2>
        {!ws.live && (
          <span className="chip warn" title="Sample mode: links are kept in this browser only.">
            Sample
          </span>
        )}
      </header>
      <p className="muted tm-explain">
        Facts a member saves with <code>scope=&quot;team&quot;</code> stay private to their team. Linking two members lets each see the other&apos;s team facts, even from a different laptop and a different AI. Pause or cut a link and access ends at once.
      </p>
      <div className="tm-mesh">
        <svg viewBox="0 0 320 300" role="img" aria-label={`Mesh of ${n} members with ${ws.links.length} links`}>
          {ws.links.map((l) => {
            const p = pos.get(l.a);
            const q = pos.get(l.b);
            if (!p || !q) return null;
            return <line key={l.id} x1={p.x} y1={p.y} x2={q.x} y2={q.y} className={`tm-edge${l.paused ? ' paused' : ''} ${l.scope}`} />;
          })}
          {ws.members.map((m) => {
            const p = pos.get(m.id)!;
            return (
              <g key={m.id} transform={`translate(${p.x} ${p.y})`}>
                <circle r="19" className={`tm-n g${(gOf.get(m.id) ?? 0) % 4}`} />
                <text y="5" textAnchor="middle" className="tm-n-t">
                  {[...m.name][0] ?? ''}
                </text>
                <circle cx="14" cy="-14" r="5" className={`tm-pres ${m.status === 'unknown' ? 'offline' : m.status}`} />
                <text y="34" textAnchor="middle" className="tm-n-l">
                  {m.name}
                </text>
              </g>
            );
          })}
        </svg>
        <div className="tm-mesh-side">
          <p className="tm-sum" role="status">
            {shared.length ? (
              <>
                {shared.map((g) => g.map(name).join(', ')).join(' · ')} <span className="muted">share team facts through {ws.links.filter((l) => !l.paused).length} active link(s).</span>
              </>
            ) : (
              'No one is linked yet. Each team sees only the shared commons and its own private facts.'
            )}
          </p>
          <ul className="tm-links">
            {ws.links.map((l) => (
              <li key={l.id}>
                <span>
                  <b>{name(l.a)}</b> ⇄ <b>{name(l.b)}</b> <small className="muted">{l.scope === 'commons' ? 'everything' : 'team facts'}</small>
                </span>
                <button className="btn quiet" aria-pressed={l.paused} onClick={() => void ws.pause(l.id).then(setErr)}>
                  {l.paused ? 'Resume' : 'Pause'}
                </button>
                <button className="btn quiet" onClick={() => void ws.unlink(l.id).then(setErr)}>
                  Unlink
                </button>
              </li>
            ))}
          </ul>
          <form
            className="tm-form tight"
            onSubmit={(e) => {
              e.preventDefault();
              void ws.link(a, b, scope).then(setErr);
            }}
          >
            <label className="field">
              <span>From</span>
              <select value={a} onChange={(e) => setA(e.target.value)}>
                <option value="">Pick…</option>
                {ws.members.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>To</span>
              <select value={b} onChange={(e) => setB(e.target.value)}>
                <option value="">Pick…</option>
                {ws.members.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Share</span>
              <select value={scope} onChange={(e) => setScope(e.target.value as Scope)}>
                <option value="team">Team facts</option>
                <option value="commons">Everything</option>
              </select>
            </label>
            <button className="btn primary">Link</button>
          </form>
          <p className="tm-err" role="alert">
            {err}
          </p>
        </div>
      </div>
    </section>
  );
}

function expiry(at: number | undefined, now: number): string {
  if (!at) return 'never expires';
  const d = Math.ceil((at - now) / 86_400_000);
  return d < 0 ? 'expired: rotate it' : d === 0 ? 'expires today' : `expires in ${d} day${d === 1 ? '' : 's'}`;
}

export function Team() {
  const ws = useWs();
  return (
    <div className="page tm" tabIndex={0}>
      <header className="page-head">
        <PageTag view="team">Team</PageTag>
        <h1>One company, many teams, many AIs</h1>
        <p>Choose a plan, split people into teams, give each person their own token and AI, then link them so context flows between laptops without anyone pasting anything.</p>
      </header>
      <Issued ws={ws} />
      <Network />
      <Org ws={ws} />
      <Teams ws={ws} />
      <LevelGuide ws={ws} />
      <Members ws={ws} />
      <Mesh ws={ws} />
    </div>
  );
}
