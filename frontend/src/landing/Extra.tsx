import { useMemo, useState } from 'react';
import ablation from '../../../bench/ablation_results.json';
import agentEval from '../../../bench/agent_eval_results.json';
import agentAccess from '../../../bench/agent_eval_access_results.json';
import agentQuote from '../../../bench/agent_eval_quote_results.json';
import agentDeploy from '../../../bench/agent_eval_deploy_results.json';
import ripple from '../../../bench/real_graph_ripple_results.json';
import corpus from '../../../bench/real_corpus_results.json';
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
        tag="07"
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

const SNIPPET = 'python scripts/setup.py';

export function ConnectSection() {
  const [copied, setCopied] = useState(false);
  return (
    <section className="lp-section lp-dark" id="connect">
      <Head dark tag="08" kicker="Set up from your AI chat" title="One command. One prompt. Then just talk to it." sub="Run the setup, paste the prompt it prints into Claude, Cursor, VS Code, Antigravity, Windsurf or any MCP-capable AI. It connects itself, lists every command, asks a few questions, finds teammates on your network and shows what is remembered. A built-in guide answers how-to questions in the same chat. No console needed." />
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
            <b>Run setup</b>
            <span>Needs Docker and Python. It writes strong secrets and starts a hub on your network.</span>
          </li>
          <li>
            <b>Paste the prompt</b>
            <span>Your AI connects itself, shows every command, runs a five-question survey and teaches itself the working rules.</span>
          </li>
          <li>
            <b>Say who to connect to</b>
            <span>&ldquo;Connect me to Maya&rsquo;s Quorum.&rdquo; Her admin is told in her chat, approves, and you are in.</span>
          </li>
          <li>
            <b>Ask how, any time</b>
            <span>&ldquo;How do I fix a wrong fact?&rdquo; The chat tool quorum_guide answers from the same guide as the console.</span>
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

export function FalkorSection() {
  const a = ablation.summary;
  const runs = [agentEval, agentAccess, agentQuote, agentDeploy].map((r) => r.summary as { flat: Record<string, number>; quorum: Record<string, number> });
  const agentTrials = runs.reduce((n, r) => n + r.flat.trials!, 0);
  const agentFlat = runs.reduce((n, r) => n + (r.flat.unsafe_actions_executed ?? r.flat.unsafe_refunds_executed ?? 0), 0);
  const agentQuorum = runs.reduce((n, r) => n + (r.quorum.unsafe_actions_executed ?? r.quorum.unsafe_refunds_executed ?? 0), 0);
  return (
    <section className="lp-section" id="falkordb">
      <Head tag="06" kicker="Graph-native" title="The graph decides what is safe to believe." sub="The quarantine cascade, the execute guard ancestry check and the blast radius run as Cypher inside FalkorDB, one statement per hop. A live endpoint compares FalkorDB with an independent walk, so the claim can be checked, not trusted." />
      <div className="lp-stats" role="list">
        <div role="listitem" className="lp-stat">
          <b className="num">
            {a.flat_leaked}/{a.actions_at_risk}
          </b>
          <span>at-risk actions a flat memory let run</span>
        </div>
        <div role="listitem" className="lp-stat">
          <b className="num">
            {a.quorum_leaked}/{a.actions_at_risk}
          </b>
          <span>with the same nodes and their edges in FalkorDB</span>
        </div>
        <div role="listitem" className="lp-stat">
          <b className="num">{a.flat_tainted_conclusions_still_retrievable}</b>
          <span>tainted conclusions still retrievable in the flat arm, versus {a.quorum_tainted_conclusions_still_retrievable}</span>
        </div>
        <div role="listitem" className="lp-stat">
          <b className="num">{Math.round(a.cascade_ms_median)} ms</b>
          <span>median cascade through FalkorDB, end to end</span>
        </div>
      </div>
      <div className="lp-stats" role="list">
        <div role="listitem" className="lp-stat">
          <b className="num">
            {agentFlat}/{agentTrials}
          </b>
          <span>wrong actions a real Claude agent took from a lingering conclusion with no provenance (four scenarios)</span>
        </div>
        <div role="listitem" className="lp-stat">
          <b className="num">
            {agentQuorum}/{agentTrials}
          </b>
          <span>with the same memory and the same correction, quarantined through the graph</span>
        </div>
        <div role="listitem" className="lp-stat">
          <b className="num">{Math.round(ripple.summary.ripple_recall * 100)}%</b>
          <span>ripple recall and precision on a real graph: {ripple.summary.modules} modules, {ripple.summary.import_edges} import edges</span>
        </div>
      </div>
      <div className="lp-stats" role="list">
        <div role="listitem" className="lp-stat">
          <b className="num">{corpus.summary.corpus.packages}</b>
          <span>real packages as source material: {corpus.summary.corpus.facts.toLocaleString()} facts, each citing its own metadata line</span>
        </div>
        <div role="listitem" className="lp-stat">
          <b className="num">{corpus.summary.answers.quorum.abstained_on_nonexistent_pct}%</b>
          <span>of questions about things that do not exist were refused, versus {corpus.summary.answers.plain.abstained_on_nonexistent_pct}% for plain search</span>
        </div>
        <div role="listitem" className="lp-stat">
          <b className="num">{corpus.summary.impact_question.quorum.exact_set_pct}%</b>
          <span>exact answers to &ldquo;what is affected if this is wrong?&rdquo;, versus {corpus.summary.impact_question.plain.exact_set_pct}% for plain search</span>
        </div>
      </div>
      <ul className="lp-team-points">
        <li>
          <Icon name="graph" size={18} /> Verify it on a running hub: <code className="mono">GET /api/falkordb/proof</code>
        </li>
        <li>
          <Icon name="lock" size={18} /> The atomic check-and-set stays under a lock in one API writer; FalkorDB answers the ancestry question
        </li>
      </ul>
      <p className="lp-note">
        {a.scenarios} generated, seeded scenarios; &ldquo;flat&rdquo; means the same nodes with no causal edges. The agent test is {agentTrials} runs per arm of the real Claude CLI ({agentEval.params.model}) across four scenarios we wrote. The graph is this project&rsquo;s own import graph. None of it compares Quorum with any product. Read from bench/*.json.
      </p>
    </section>
  );
}

export function DemoSection() {
  return (
    <section className="lp-section lp-dark" id="demo">
      <Head dark tag="10" kicker="Watch it" title="Under two minutes, real output." sub="A poisoned fact contained in the console, a teammate asking to connect and being approved, and the chat commands, using tool output captured from two live hubs." />
      <video className="lp-video" controls preload="none" playsInline poster="/demo/poster.jpg" aria-label="Quorum demo video">
        <source src="/demo/quorum-demo.mp4" type="video/mp4" />
        Your browser cannot play this video. It is in the repository at docs/demo/quorum-demo.mp4.
      </video>
    </section>
  );
}

export function TeamsSection() {
  return (
    <section className="lp-section" id="teams">
      <Head tag="09" kicker="Teams and enterprises" title="Many teams. Many laptops. One shared context." sub="Choose a plan, split people into teams, give each person a token and their own AI, then link their MCP servers. Linked members receive each other's changes as small deltas, not pasted context." />
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
          <Icon name="user" size={18} /> Per-person tokens with Contributor, Reviewer and Admin levels, changed by asking your AI
        </li>
        <li>
          <Icon name="link" size={18} /> Teammates on your network ask to connect; you approve in the chat, at the level you choose
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
