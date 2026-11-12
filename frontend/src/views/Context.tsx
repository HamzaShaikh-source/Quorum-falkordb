import { PageTag } from '../components/PageTag';
import { useDeferredValue, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getContextPreview, getTokenStats } from '../workspace/server';
import { API_MODE } from '../api/client';
import { Icon } from '../components/Icon';
import { Measured } from '../components/Measured';
import { RetrievalQuality } from '../components/RetrievalQuality';
import { MindMap } from '../components/MindMap';
import { useCountUp } from '../components/ui';
import { buildMap, pack, savings } from '../context/packer';
import { synthGraph } from '../context/synth';
import { fmt, pct } from '../context/tokens';
import { useStore } from '../state/store';
import { useLive } from '../workspace/live';
import { copyText } from '../workspace/clipboard';

type Source = 'live' | '120' | '400' | '1200';

function Header() {
  return (
    <header className="page-head">
      <PageTag view="context">Context</PageTag>
      <h1>Send the AI a map, not the library</h1>
      <p>One organized memory, two views: the mind map is for you, the compact map and packed context are what the agent reads. Every number below is computed from the graph you are looking at.</p>
    </header>
  );
}

function Bar({ label, tokens, max, tone, note }: { label: string; tokens: number; max: number; tone: string; note?: string }) {
  return (
    <div className="ctx-bar">
      <div className="ctx-bar-top">
        <span>{label}</span>
        <b className="num">{fmt(tokens)}</b>
      </div>
      <div className="ctx-track" aria-hidden="true">
        <i className={tone} style={{ width: `${Math.max(1.5, (tokens / max) * 100)}%` }} />
      </div>
      {note && <small className="muted">{note}</small>}
    </div>
  );
}

export function Context() {
  const nodesMap = useStore((s) => s.nodes);
  const edgesMap = useStore((s) => s.edges);
  const tenant = useStore((s) => s.tenant);
  const epoch = useStore((s) => s.epoch);
  const selectedId = useStore((s) => s.selectedId);
  const set = useStore((s) => s.set);
  const totals = useLive((s) => s.totals);
  const pushToast = useStore((s) => s.pushToast);

  const [source, setSource] = useState<Source>(API_MODE === 'live' ? 'live' : '400');
  const [query, setQuery] = useState(API_MODE === 'live' ? '' : 'refund window');
  const [budget, setBudget] = useState(420);
  // On a live memory the example question comes from the person's own facts, not from the demo.
  const suggested = useMemo(() => {
    const facts = Object.values(nodesMap).filter((n) => n.kind === 'fact' && n.status === 'HEALTHY');
    const f = facts[facts.length - 1];
    return f ? f.label.split(/\s+/).filter((w) => w.length > 2).slice(0, 4).join(' ') : '';
  }, [nodesMap]);
  const dq = useDeferredValue(query || (API_MODE === 'live' ? suggested : 'refund window'));

  const live = useMemo(() => ({ nodes: Object.values(nodesMap), edges: Object.values(edgesMap) }), [nodesMap, edgesMap]);
  const graph = useMemo(() => (source === 'live' ? live : synthGraph(Number(source))), [source, live]);

  const packedLocal = useMemo(() => pack(graph, { query: dq, budget }), [graph, dq, budget]);
  const mapLocal = useMemo(() => buildMap(graph, { tenant, epoch }), [graph, tenant, epoch]);
  const svLocal = useMemo(() => savings(graph, packedLocal, { tenant, epoch }, dq), [graph, packedLocal, tenant, epoch, dq]);

  // Live graph on a live backend: show what the backend computes, with the same code the agents call.
  const server = API_MODE === 'live' && source === 'live';
  const preview = useQuery({ queryKey: ['ctx-preview', dq, budget], queryFn: () => getContextPreview(dq, budget), enabled: server, refetchInterval: 15_000 });
  const stats = useQuery({ queryKey: ['token-stats'], queryFn: getTokenStats, enabled: API_MODE === 'live', refetchInterval: 10_000 });
  const p = server ? preview.data : undefined;
  const packed = p
    ? ({ text: p.packed, tokens: p.packed_tokens, included: new Array<string>(p.included).fill(''), dropped: p.dropped, truncated: p.dropped > 0, delta: null, stages: p.stages } as typeof packedLocal)
    : packedLocal;
  const mapText = p ? p.map : mapLocal;
  const sv = p
    ? {
        rawTokens: p.raw_json_tokens,
        proseTokens: p.prose_tokens,
        ragTokens: p.top8_tokens,
        vsRag: p.top8_tokens - p.packed_tokens,
        packedTokens: p.packed_tokens,
        mapTokens: p.map_tokens,
        vsRaw: p.raw_json_tokens - p.packed_tokens - p.map_tokens,
        vsProse: p.prose_tokens - p.packed_tokens - p.map_tokens,
      }
    : svLocal;

  const sentTokens = sv.packedTokens + sv.mapTokens;
  const noMatch = packed.included.length === 0;
  const comparable = !noMatch && sv.ragTokens > sentTokens;
  const cut = comparable ? pct(sv.ragTokens - sentTokens, sv.ragTokens) : 0;
  const shown = useCountUp(cut);
  const max = Math.max(sv.rawTokens, sv.proseTokens, 1);
  const saved = API_MODE === 'mock' ? Math.max(0, totals.naive - totals.used) : (stats.data?.saved ?? 0);

  const copy = (text: string, what: string) => {
    void copyText(text).then((ok) => pushToast(ok ? `${what} copied` : 'Copy was blocked by the browser', ok ? 'good' : 'warn'));
  };

  const download = () => {
    const url = URL.createObjectURL(new Blob([mapText], { type: 'text/markdown' }));
    const a = document.createElement('a');
    a.href = url;
    a.download = 'quorum_map.md';
    a.click();
    URL.revokeObjectURL(url);
  };

  const small = source === 'live' && live.nodes.length < 40;

  return (
    <div className="page ctx" tabIndex={0}>
      <Header />

      <section className="card wide ctx-controls" aria-label="Context controls">
        <label className="field">
          <span>Memory size</span>
          <select value={source} onChange={(e) => setSource(e.target.value as Source)}>
            <option value="live">Live graph ({live.nodes.length} nodes)</option>
            <option value="120">Sample enterprise, 120 nodes</option>
            <option value="400">Sample enterprise, 400 nodes</option>
            <option value="1200">Sample enterprise, 1,200 nodes</option>
          </select>
        </label>
        <label className="field grow">
          <span>What is the agent asking?</span>
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder={suggested || 'Ask about something an agent has saved'} />
        </label>
        <label className="field">
          <span>Token budget: {fmt(budget)}</span>
          <input type="range" min={100} max={1500} step={20} value={budget} onChange={(e) => setBudget(Number(e.target.value))} />
        </label>
      </section>
      {source !== 'live' && <p className="ctx-note">Sample memories are generated in your browser from a fixed seed. They are synthetic, shaped like the real graph, and clearly not your data.</p>}
      {small && <p className="ctx-note">The live graph only has {live.nodes.length} nodes, so there is little to save yet. Pick a larger sample to see how it scales.</p>}

      <div className="ctx-hero">
        <section className="card ctx-big" aria-label="Savings">
          <p className="lp-k">Fewer tokens than a top-8 RAG lookup</p>
          {comparable ? (
            <>
              <div className="ctx-pct num">
                {shown}
                <small>%</small>
              </div>
              <p className="muted">
                A typical top-8 retriever sends {fmt(sv.ragTokens)} tokens per question. Quorum sends {fmt(sentTokens)}: the map ({fmt(sv.mapTokens)}, once per session) plus the packed answer ({fmt(sv.packedTokens)}). Against pasting the whole memory as notes ({fmt(sv.proseTokens)}) the cut is {pct(sv.vsProse, sv.proseTokens)}%, and against raw graph JSON ({fmt(sv.rawTokens)}) it is {pct(sv.vsRaw, sv.rawTokens)}%.
              </p>
            </>
          ) : (
            <>
              <div className="ctx-pct num">
                <small>{noMatch ? 'No match' : 'Too small to compare'}</small>
              </div>
              <p className="muted">
                {!dq
                  ? live.nodes.length > 0 && source === 'live'
                    ? `Every belief here (${live.nodes.length}) is quarantined or replaced, so agents are shown nothing and there is nothing to pack. Restore one, or let an agent save a new fact, and the comparison appears.`
                    : 'This memory is empty, so there is nothing to pack and compare yet. Once an agent has saved facts, ask a question about them here.'
                  : noMatch
                  ? `Nothing in this memory matches "${dq}", so there is nothing to pack and no fair comparison. Ask about something an agent has actually saved${suggested && source === 'live' ? ', for example:' : '.'}`
                  : `For this question a plain retriever sends ${fmt(sv.ragTokens)} tokens, less than Quorum's map plus answer (${fmt(sentTokens)}). The map is sent once per session and the saving appears on larger memories and across many questions.`}
              </p>
              {noMatch && suggested && source === 'live' && (
                <button className="btn primary" onClick={() => setQuery(suggested)}>
                  Try &ldquo;{suggested}&rdquo;
                </button>
              )}
            </>
          )}
          <p className="ctx-fine">
            Estimates: about 4.5 characters per token for words and one token per symbol, within roughly 15% of a real tokenizer. The map is sent once per session; each question after that only costs the packed answer.
          </p>
        </section>
        <section className="card" aria-label="Comparison">
          <header className="card-h">
            <h2>Per question, in tokens</h2>
          </header>
          <Bar label="Raw graph JSON" tokens={sv.rawTokens} max={max} tone="bad" note="Every field of every node pasted in" />
          <Bar label="Notes-style memory" tokens={sv.proseTokens} max={max} tone="warn" note="Every node as a sentence" />
          <Bar label="Typical top-8 RAG" tokens={sv.ragTokens} max={max} tone="warn" note="Full chunks, cannot tell a quarantined fact from a healthy one" />
          <Bar label="Quorum: map + packed answer" tokens={sentTokens} max={max} tone="ok" note={`${packed.included.length} nodes, ${packed.dropped} dropped by the budget`} />
        </section>
        <section className="card ctx-session" aria-label="This session">
          <header className="card-h">
            <h2>This session</h2>
          </header>
          <div className="ctx-pct small num">{fmt(saved)}</div>
          {API_MODE === 'mock' ? (
            <p className="muted">tokens saved across {fmt(totals.calls)} tool calls in the live feed. Counted per call against a typical top-8 RAG lookup, so it is the conservative number.</p>
          ) : (
            <p className="muted">
              tokens not sent, measured on {fmt(stats.data?.measured_calls ?? 0)} real read calls ({stats.data?.saved_pct ?? 0}% fewer than a typical top-8 retriever would have sent for the same calls). Calls that used the full format count against it.
            </p>
          )}
        </section>
      </div>

      <details className="card wide ctx-evidence">
        <summary>
          <b>Evidence</b> <span className="muted">Measured with real AIs: token savings and whether the right fact comes back</span>
        </summary>
        <section aria-label="Measured with a real AI">
          <Measured />
        </section>
        <section aria-label="Retrieval quality">
          <RetrievalQuality />
        </section>
      </details>

      <section className="card wide" aria-label="Where the savings come from">
        <header className="card-h">
          <h2>Where the savings come from</h2>
          <span className="card-sub">Token count after each technique, in order</span>
        </header>
        <ol className="ctx-stages">
          {packed.stages.map((s, i) => (
            <li key={s.label}>
              <span className="ctx-step num">{i + 1}</span>
              <div>
                <b>{s.label}</b>
                <small className="muted">{s.note}</small>
              </div>
              <div className="ctx-stage-bar" aria-hidden="true">
                <i style={{ width: `${Math.max(1, (s.tokens / (packed.stages[0]?.tokens || 1)) * 100)}%` }} />
              </div>
              <b className="num">{fmt(s.tokens)}</b>
            </li>
          ))}
        </ol>
      </section>

      <div className="ctx-split">
        <section className="card" aria-label="What you see">
          <header className="card-h">
            <h2>
              <Icon name="graph" size={15} /> What you see
            </h2>
            <span className="card-sub">Mind map, click to fold or select</span>
          </header>
          <MindMap
            nodes={graph.nodes}
            edges={graph.edges}
            tenant={tenant}
            selected={selectedId}
            compact={source === 'live'}
            onPick={(id) => {
              if (source === 'live') set({ selectedId: id, view: 'console', tab: 'graph' });
            }}
          />
        </section>
        <section className="card" aria-label="What the AI receives">
          <header className="card-h">
            <h2>
              <Icon name="sparkle" size={15} /> What the AI receives
            </h2>
            <span className="card-sub num">{fmt(sentTokens)} tokens</span>
          </header>
          <div className="ctx-doc-head">
            <b>quorum_map.md</b>
            <span className="muted num">{fmt(sv.mapTokens)} tokens, once per session</span>
            <span className="grow" />
            <button className="btn quiet" onClick={() => copy(mapText, 'Map')}>
              <Icon name="copy" size={14} /> Copy
            </button>
            <button className="btn quiet" onClick={download}>
              Download
            </button>
          </div>
          <pre className="terminal mono ctx-pre" tabIndex={0}>{mapText}</pre>
          <div className="ctx-doc-head">
            <b>packed answer</b>
            <span className="muted num">{fmt(packed.tokens)} tokens, per question</span>
            <span className="grow" />
            <button className="btn quiet" onClick={() => copy(packed.text, 'Packed context')}>
              <Icon name="copy" size={14} /> Copy
            </button>
          </div>
          <pre className="terminal mono ctx-pre" tabIndex={0}>{packed.text}</pre>
        </section>
      </div>
    </div>
  );
}
