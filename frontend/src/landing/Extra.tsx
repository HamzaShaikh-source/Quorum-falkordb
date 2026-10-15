import { useMemo, useState } from 'react';
import { Icon } from '../components/Icon';
import { Measured } from '../components/Measured';
import { buildMap, pack, savings } from '../context/packer';
import { synthGraph } from '../context/synth';
import { fmt, pct } from '../context/tokens';
import { copyText } from '../workspace/clipboard';
import { CLIENTS } from '../workspace/mcp';
import { PLANS } from '../workspace/store';
import { go } from './hooks';
import { SplitText } from './fx/SplitText';

function Head({ tag, kicker, title, sub, dark = false }: { tag: string; kicker: string; title: string; sub: string; dark?: boolean }) {
  return (
    <div className={`lp-head${dark ? ' dark' : ''}`}>
      <p className="lp-tag">
        <span>[{tag}]</span> {kicker}
      </p>
      <SplitText text={title} className="lp-h2" />
      <p className="lp-sub">{sub}</p>
    </div>
  );
}

const SIZES = [120, 400, 1200] as const;

/** Runs the real packer in the browser on a synthetic memory. Nothing here is hard-coded. */
export function Savings() {
  const [size, setSize] = useState<(typeof SIZES)[number]>(400);
  const [q, setQ] = useState('refund window');
  const g = useMemo(() => synthGraph(size), [size]);
  const r = useMemo(() => {
    const p = pack(g, { query: q, budget: 420 });
    const s = savings(g, p, { tenant: 'acme', epoch: 1 }, q);
    return { p, s, map: buildMap(g, { tenant: 'acme', epoch: 1 }) };
  }, [g, q]);
  const sent = r.s.packedTokens + r.s.mapTokens;
  const max = Math.max(r.s.rawTokens, r.s.proseTokens, 1);
  const rows: [string, number, string][] = [
    ['Paste the whole memory (JSON)', r.s.rawTokens, 'bad'],
    ['Paste it as notes', r.s.proseTokens, 'warn'],
    ['Typical top-8 retrieval', r.s.ragTokens, 'warn'],
    ['Quorum map + packed answer', sent, 'ok'],
  ];
  return (
    <section className="lp-section" id="savings">
      <Head
        tag="06"
        kicker="Token savings"
        title="Send the AI a map, not the library."
        sub="Quorum keeps one organized memory. Agents read a tiny map, ask for the slice they need, cite ids, and refresh with deltas. Try it on a synthetic memory: the numbers below are computed live, not typed in."
      />
      <div className="lp-sv">
        <div className="lp-sv-controls">
          <div className="seg" role="group" aria-label="Memory size">
            {SIZES.map((n) => (
              <button key={n} className={`seg-btn${size === n ? ' on' : ''}`} aria-pressed={size === n} onClick={() => setSize(n)}>
                {fmt(n)} nodes
              </button>
            ))}
          </div>
          <label className="lp-sv-q">
            <span>Agent asks</span>
            <input value={q} onChange={(e) => setQ(e.target.value)} aria-label="What the agent asks" />
          </label>
        </div>
        <div className="lp-sv-grid">
          <div className="lp-sv-bars">
            {rows.map(([label, t, tone]) => (
              <div key={label} className="lp-sv-bar">
                <div className="lp-sv-top">
                  <span>{label}</span>
                  <b className="num">{fmt(t)}</b>
                </div>
                <div className="lp-sv-track" aria-hidden="true">
                  <i className={tone} style={{ width: `${Math.max(1.5, (t / max) * 100)}%` }} />
                </div>
              </div>
            ))}
            <p className="lp-sv-fine">Estimated tokens per question (about 4.5 characters per token, within roughly 15%). The map is sent once per session. Synthetic data, fixed seed.</p>
          </div>
          <div className="lp-sv-big">
            <p className="lp-k">Fewer than a top-8 lookup</p>
            <div className="lp-sv-pct num">
              {pct(r.s.ragTokens - sent, r.s.ragTokens)}
              <small>%</small>
            </div>
            <p>
              and {pct(r.s.vsProse, r.s.proseTokens)}% fewer than pasting notes. It also never returns a quarantined fact; a plain retriever cannot tell.
            </p>
          </div>
        </div>
        <Measured />
        <details className="lp-sv-peek">
          <summary>See exactly what the agent receives</summary>
          <pre className="mono">{`${r.map}\n\n${r.p.text}`}</pre>
        </details>
      </div>
    </section>
  );
}

const SNIPPET = 'claude mcp add --transport http quorum http://localhost:8000/mcp';

export function ConnectSection() {
  const [copied, setCopied] = useState(false);
  return (
    <section className="lp-section lp-dark" id="connect">
      <Head dark tag="07" kicker="Connect any AI" title="One MCP server. Every laptop. Any model." sub="Add Quorum as a tool set in Claude, Cursor, VS Code, Windsurf, Antigravity or anything else that speaks MCP. Everyone reads and writes the same graph." />
      <div className="lp-cn">
        <ul className="lp-cn-clients" aria-label="Works with">
          {CLIENTS.map((c) => (
            <li key={c.id}>{c.name}</li>
          ))}
        </ul>
        <div className="lp-cn-code">
          <code className="mono">{SNIPPET}</code>
          <button
            className="lp-pill"
            onClick={() => void copyText(SNIPPET).then((ok) => ok && (setCopied(true), setTimeout(() => setCopied(false), 1600)))}
          >
            <Icon name="copy" size={15} />
            {copied ? 'Copied' : 'Copy'}
          </button>
        </div>
        <ol className="lp-cn-steps">
          <li>
            <b>Start the server</b>
            <span>docker compose up</span>
          </li>
          <li>
            <b>Paste one line</b>
            <span>Per client, or use stdio for a single laptop</span>
          </li>
          <li>
            <b>Watch it connect</b>
            <span>Status, live feed and chats in the console</span>
          </li>
        </ol>
        <button className="lp-pill mint xl" onClick={() => go('app')}>
          Open the Connect page
          <Icon name="conclusion" size={18} />
        </button>
      </div>
    </section>
  );
}

export function TeamsSection() {
  return (
    <section className="lp-section" id="teams">
      <Head tag="08" kicker="Teams and enterprises" title="Many teams. Many laptops. One shared context." sub="Choose a plan, split people into teams, give each person a token and their own AI, then link their MCP servers. Linked members receive each other's changes as small deltas, not pasted context." />
      <div className="lp-plans">
        {(Object.keys(PLANS) as (keyof typeof PLANS)[]).map((k) => (
          <article key={k} className={`lp-plan${k === 'business' ? ' pick' : ''}`}>
            <h3>{PLANS[k].label}</h3>
            <p className="lp-plan-n num">
              {PLANS[k].seats}
              <small> seats</small>
            </p>
            <p>{PLANS[k].teams} teams</p>
            <p className="lp-plan-note">{PLANS[k].note}</p>
          </article>
        ))}
      </div>
      <ul className="lp-team-points">
        <li>
          <Icon name="user" size={18} /> Per-person tokens with agent, auditor and human roles
        </li>
        <li>
          <Icon name="link" size={18} /> Link members across laptops; pause or cut a link any time
        </li>
        <li>
          <Icon name="lock" size={18} /> Tenant isolation is enforced by the graph, not by convention
        </li>
      </ul>
      <button className="lp-pill dark xl" onClick={() => go('app')}>
        Try the Team page
        <Icon name="conclusion" size={18} />
      </button>
    </section>
  );
}
