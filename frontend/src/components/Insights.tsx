import { useMemo, useState } from 'react';
import type { GraphNode } from '../api/schemas';
import { copy } from '../copy';
import { tools } from '../copy/tools';
import { graphBus } from '../graph/bus';
import {
  activityBins,
  adjacency,
  communities,
  explain,
  flow,
  godNodes,
  shortestPath,
  statusCounts,
  type Bins,
} from '../state/insights';
import { useStore } from '../state/store';
import { Icon } from './Icon';
import { Empty, fmtTime } from './ui';

const T = tools.insights;
const KIND_ICON: Record<string, string> = { source: 'doc', fact: 'fact', conclusion: 'conclusion', action: 'bolt' };
const iconOf = (n: GraphNode) => (n.is_decision ? 'flag' : (KIND_ICON[n.kind] ?? 'fact'));

function Kpi({ label, value, tone }: { label: string; value: number; tone?: string }) {
  return (
    <div className={`ins-kpi${tone ? ` ${tone}` : ''}`}>
      <span className="ins-kpi-v num">{String(value).padStart(2, '0')}</span>
      <span className="ins-kpi-l">{label}</span>
    </div>
  );
}

function StatusBar({ nodes }: { nodes: GraphNode[] }) {
  const c = statusCounts(nodes);
  const parts = [
    { k: 'ok', label: T.legendHealthy, v: c.healthy },
    { k: 'stale', label: T.legendStale, v: c.stale },
    { k: 'bad', label: T.legendHeld, v: c.quarantined },
    { k: 'old', label: T.legendOld, v: c.superseded },
  ];
  return (
    <div>
      <div className="ins-stack" role="img" aria-label={parts.map((p) => `${p.v} ${p.label}`).join(', ')}>
        {parts.map((p) => (
          <i key={p.k} className={p.k} style={{ flexGrow: p.v }} title={`${p.label}: ${p.v}`} />
        ))}
      </div>
      <ul className="ins-legend">
        {parts.map((p) => (
          <li key={p.k}>
            <i className={p.k} />
            {p.label}
            <b className="num">{p.v}</b>
          </li>
        ))}
      </ul>
    </div>
  );
}

function FlowChart({ nodes, edges }: { nodes: GraphNode[]; edges: Parameters<typeof flow>[1] }) {
  const f = useMemo(() => flow(nodes, edges), [nodes, edges]);
  const W = 700;
  const H = 270;
  const colX = [60, 240, 420, 600];
  const barW = 26;
  const maxTotal = Math.max(1, ...f.columns.map((c) => c.total));
  const maxLink = Math.max(1, ...f.links.map((l) => l.total));
  const bh = (n: number) => (n === 0 ? 0 : Math.max(18, (n / maxTotal) * 170));
  const lh = (n: number) => (n === 0 ? 0 : Math.max(5, (n / maxLink) * 64));
  const cy = 150;
  return (
    <svg className="chart ins-flow" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={T.flow}>
      {f.links.map((l) => {
        const x1 = colX[l.from]! + barW;
        const x2 = colX[l.to]!;
        const mid = (x1 + x2) / 2;
        const total = lh(l.total);
        const badH = l.bad ? Math.max(3, (l.bad / l.total) * total) : 0;
        const goodH = total - badH;
        const top = cy - total / 2;
        const ribbon = (y: number, h: number, cls: string) => (
          <path
            className={`ribbon ${cls}`}
            d={`M${x1} ${y} C${mid} ${y} ${mid} ${y} ${x2} ${y} L${x2} ${y + h} C${mid} ${y + h} ${mid} ${y + h} ${x1} ${y + h} Z`}
          />
        );
        return (
          <g key={`${l.from}-${l.to}`}>
            {goodH > 0 && ribbon(top, goodH, 'ok')}
            {badH > 0 && ribbon(top + goodH, badH, 'bad')}
            <text className="val num" x={mid} y={cy + total / 2 + 18} textAnchor="middle">
              {l.total} link{l.total === 1 ? '' : 's'}
              {l.bad ? `, ${l.bad} held` : ''}
            </text>
          </g>
        );
      })}
      {f.columns.map((c, i) => {
        const h = bh(c.total);
        const y = cy - h / 2;
        const badH = c.total ? (c.quarantined / c.total) * h : 0;
        return (
          <g key={c.kind}>
            <text className="axis" x={colX[i]! + barW / 2} y={30} textAnchor="middle">
              {copy.rails[i]}
            </text>
            <rect className="bar-back" x={colX[i]} y={y} width={barW} height={h} rx={5} />
            {badH > 0 && <rect className="bar-bad" x={colX[i]} y={y + h - badH} width={barW} height={badH} rx={5} />}
            <text className="big num" x={colX[i]! + barW / 2} y={cy - h / 2 - 12} textAnchor="middle">
              {c.total}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

function ActivityChart({ bins }: { bins: Bins | null }) {
  if (!bins) return <Empty>{T.activityEmpty}</Empty>;
  const W = 700;
  const H = 150;
  const max = Math.max(1, ...bins.counts);
  const bw = (W - 20) / bins.counts.length;
  const x = (ts: number) => 10 + ((ts - bins.start) / (bins.end - bins.start)) * (W - 20);
  return (
    <svg className="chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={T.activity}>
      <line className="gridline" x1={10} x2={W - 10} y1={H - 30} y2={H - 30} />
      {bins.counts.map((c, i) => {
        const h = c === 0 ? 0 : Math.max(3, (c / max) * (H - 66));
        return <rect key={i} className="act-bar" x={10 + i * bw + 1} y={H - 30 - h} width={Math.max(2, bw - 2)} height={h} rx={2} />;
      })}
      {bins.marks.map((m, i) => (
        <g key={i}>
          <line className={`mark ${m.kind}`} x1={x(m.ts)} x2={x(m.ts)} y1={14} y2={H - 30} />
          <text className="axis" x={x(m.ts) + 4} y={22}>
            {m.kind}
          </text>
        </g>
      ))}
      <text className="axis" x={10} y={H - 10}>
        {fmtTime(bins.start)}
      </text>
      <text className="axis" x={W - 10} y={H - 10} textAnchor="end">
        {fmtTime(bins.end)}
      </text>
    </svg>
  );
}

export function Insights() {
  const nodesRec = useStore((s) => s.nodes);
  const edgesRec = useStore((s) => s.edges);
  const past = useStore((s) => s.past);
  const eventLog = useStore((s) => s.eventLog);
  const selected = useStore((s) => s.selectedId);
  const pathIds = useStore((s) => s.pathIds);
  const set = useStore((s) => s.set);

  const nodes = useMemo(() => (past ? past.nodes : Object.values(nodesRec)), [past, nodesRec]);
  const edges = useMemo(() => (past ? past.edges : Object.values(edgesRec)), [past, edgesRec]);
  const adj = useMemo(() => adjacency(nodes, edges), [nodes, edges]);
  const gods = useMemo(() => godNodes(nodes, adj, 5), [nodes, adj]);
  const parts = useMemo(() => communities(nodes, adj), [nodes, adj]);
  const bins = useMemo(() => activityBins(eventLog), [eventLog]);
  const byId = useMemo(() => new Map(nodes.map((n) => [n.id, n])), [nodes]);
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const lead = parts[0]?.ids ?? [];
  const leadNodes = lead.map((i) => byId.get(i)).filter((n): n is GraphNode => !!n);
  const f = from || leadNodes.find((n) => n.kind === 'source')?.id || nodes[0]?.id || '';
  const t = to || [...leadNodes].reverse().find((n) => n.kind === 'action')?.id || nodes[nodes.length - 1]?.id || '';
  const path = useMemo(() => (f && t ? shortestPath(adj, f, t) : null), [adj, f, t]);
  const sel = selected ? byId.get(selected) : undefined;
  const lines = sel ? explain(sel, adj, nodes) : [];
  const maxDown = Math.max(1, ...gods.map((g) => g.downstream));
  const counts = statusCounts(nodes);

  const pick = (id: string) => {
    set({ selectedId: id, tab: 'graph' });
    setTimeout(() => graphBus.focus.emit(id), 60);
  };

  if (nodes.length === 0) {
    return (
      <div className="insights">
        <Empty>{T.empty}</Empty>
      </div>
    );
  }

  const options = nodes.map((n) => (
    <option key={n.id} value={n.id}>
      {copy.kind[n.kind]}: {n.label}
    </option>
  ));

  return (
    <div className="insights">
      <header className="ins-head">
        <h2>{T.title}</h2>
        <p>{T.sub}</p>
      </header>

      <div className="ins-kpis">
        <Kpi label={T.kpis.nodes} value={nodes.length} />
        <Kpi label={T.kpis.edges} value={edges.filter((e) => ['LEADS_TO', 'SUPPORTS', 'FROM_SOURCE'].includes(e.type)).length} />
        <Kpi label={T.kpis.held} value={counts.quarantined} tone={counts.quarantined ? 'bad' : ''} />
        <Kpi label={T.kpis.stale} value={counts.stale} tone={counts.stale ? 'warn' : ''} />
        <Kpi label={T.kpis.parts} value={parts.length} />
      </div>

      <div className="ins-grid">
        <section className="ins-card span2">
          <header>
            <h3>{T.flow}</h3>
            <span className="card-sub">{T.flowSub}</span>
          </header>
          <FlowChart nodes={nodes} edges={edges} />
        </section>

        <section className="ins-card">
          <header>
            <h3>{T.status}</h3>
          </header>
          <StatusBar nodes={nodes} />
          <h4 className="sub">{T.gods}</h4>
          <p className="card-sub">{T.godsSub}</p>
          <ol className="ins-gods">
            {gods.map((g) => {
              const n = byId.get(g.id);
              if (!n) return null;
              return (
                <li key={g.id}>
                  <button onClick={() => pick(g.id)}>
                    <Icon name={iconOf(n)} size={15} />
                    <span className="g-label">{n.label}</span>
                    <span className="g-meta num">{T.downstream(g.downstream)}</span>
                    <span className="g-bar">
                      <i style={{ width: `${(g.downstream / maxDown) * 100}%` }} />
                    </span>
                  </button>
                </li>
              );
            })}
          </ol>
        </section>

        <section className="ins-card span2">
          <header>
            <h3>{T.activity}</h3>
            <span className="card-sub">{T.activitySub}</span>
          </header>
          <ActivityChart bins={bins} />
        </section>

        <section className="ins-card">
          <header>
            <h3>{T.communities}</h3>
            <span className="card-sub">{T.communitiesSub}</span>
          </header>
          <ul className="ins-comm">
            {parts.map((c, i) => (
              <li key={i}>
                <div className="c-head">
                  <strong>{c.roots.slice(0, 2).join(' and ') || 'Unrooted group'}</strong>
                  <span className="num">{c.ids.length}</span>
                </div>
                <div className="c-bar" role="img" aria-label={`${c.quarantined} of ${c.ids.length} quarantined`}>
                  <i className="bad" style={{ flexGrow: c.quarantined }} />
                  <i className="ok" style={{ flexGrow: c.ids.length - c.quarantined }} />
                </div>
              </li>
            ))}
          </ul>
        </section>

        <section className="ins-card">
          <header>
            <h3>{T.path}</h3>
            <span className="card-sub">{T.pathSub}</span>
          </header>
          <label className="field">
            <span>{T.from}</span>
            <select value={f} onChange={(e) => setFrom(e.target.value)}>
              {options}
            </select>
          </label>
          <label className="field">
            <span>{T.to}</span>
            <select value={t} onChange={(e) => setTo(e.target.value)}>
              {options}
            </select>
          </label>
          {path ? (
            <>
              <p className="card-sub num">{T.hops(path.length - 1)}</p>
              <ol className="ins-path">
                {path.map((id) => {
                  const n = byId.get(id);
                  return n ? (
                    <li key={id}>
                      <button onClick={() => pick(id)}>
                        <Icon name={iconOf(n)} size={13} />
                        {n.label}
                      </button>
                    </li>
                  ) : null;
                })}
              </ol>
              <div className="ins-actions">
                <button
                  className="btn primary"
                  onClick={() => {
                    set({ pathIds: path, tab: 'graph' });
                  }}
                >
                  <Icon name="path" size={14} />
                  {T.showPath}
                </button>
                {pathIds.length > 0 && (
                  <button className="btn quiet" onClick={() => set({ pathIds: [] })}>
                    {T.clearPath}
                  </button>
                )}
              </div>
            </>
          ) : (
            <p className="muted">{T.noPath}</p>
          )}
        </section>

        <section className="ins-card">
          <header>
            <h3>{T.explain}</h3>
          </header>
          {sel ? (
            <div className="ins-explain">
              <p className="ex-title">
                <Icon name={iconOf(sel)} size={15} />
                {sel.label}
              </p>
              {lines.map((l, i) => (
                <p key={i}>{l}</p>
              ))}
            </div>
          ) : (
            <p className="muted">{T.explainEmpty}</p>
          )}
        </section>
      </div>
    </div>
  );
}
