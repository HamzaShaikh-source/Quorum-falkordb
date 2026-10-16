import { useQuery } from '@tanstack/react-query';
import { copy } from '../copy';
import type { GraphNode } from '../api/schemas';
import { graphBus } from '../graph/bus';
import { actionDisplayState, actionTone } from '../state/actionState';
import { getCypher, getNode } from '../state/endpoints';
import { queryTiming } from './queryTiming';
import { useStore, type InspectorTab } from '../state/store';
import { Icon } from './Icon';
import { tools } from '../copy/tools';
import { adjacency, degree, explain } from '../state/insights';
import { Chip, Empty, ErrorState, fmtDateTime, fmtTime, Skeleton } from './ui';

const TABS: InspectorTab[] = ['path', 'details', 'links', 'cypher'];

function kindIcon(n: GraphNode) {
  if (n.kind === 'conclusion' && n.is_decision) return 'flag';
  return n.kind === 'source' ? 'doc' : n.kind === 'fact' ? 'fact' : n.kind === 'conclusion' ? 'conclusion' : 'bolt';
}

function statusTone(n: GraphNode) {
  return n.status === 'QUARANTINED' ? 'bad' : n.status === 'HEALTHY' ? 'good' : 'neutral';
}

function PathTab({ nodes }: { nodes: GraphNode[] }) {
  const select = useStore((s) => s.set);
  if (nodes.length <= 1) return <Empty>{copy.inspector.pathEmpty}</Empty>;
  return (
    <ol className="path">
      {nodes.map((n) => (
        <li key={n.id} className={`path-step st-${n.status}`}>
          <span className="path-dot">
            <Icon name={kindIcon(n)} size={13} />
          </span>
          <button
            className="path-btn"
            onClick={() => {
              select({ selectedId: n.id });
              graphBus.focus.emit(n.id);
            }}
          >
            <span className="kind">{n.is_decision ? copy.kind.decision : (copy.kind[n.kind] ?? n.kind)}</span>
            <strong>{n.label}</strong>
          </button>
          {n.quote && <blockquote>{n.quote}</blockquote>}
          {n.source && (
            <p className="muted small">
              {copy.inspector.source}: {n.source.title}, {copy.inspector.version.toLowerCase()} {n.source.version}
            </p>
          )}
          {n.origin && (
            <p className="muted small">
              {copy.inspector.origin}: <span className="mono">{n.origin}</span>
              {n.origin.startsWith('observer:') && (
                <em className="cn-tag" title="Quorum read this sentence in text the agent produced and saved it. No one called remember_fact.">
                  captured automatically
                </em>
              )}
            </p>
          )}
        </li>
      ))}
    </ol>
  );
}

function DetailsTab({
  node,
  descendants,
  history,
}: {
  node: GraphNode;
  descendants: number;
  history: { ts: number; status: string; reason: string }[];
}) {
  const ds = node.kind === 'action' ? actionDisplayState(node) : null;
  return (
    <div className="details">
      <h3 className="node-title">{node.label}</h3>
      <div className="chips">
        <Chip tone={statusTone(node)}>{copy.status[node.status] ?? node.status}</Chip>
        {node.stale && node.status === 'HEALTHY' && <Chip tone="warn">{copy.status.stale}</Chip>}
        {ds && <Chip tone={actionTone(ds)}>{copy.actionState[ds]}</Chip>}
      </div>
      {node.text && <p className="node-text">{node.text}</p>}
      <dl className="kv">
        <dt>ID</dt>
        <dd className="mono">{node.id}</dd>
        {node.asserted_by && (
          <>
            <dt>{copy.inspector.assertedBy}</dt>
            <dd>
              {node.asserted_by.name} ({copy.memory.tier[node.asserted_by.tier] ?? node.asserted_by.tier})
            </dd>
          </>
        )}
        {node.owner_team && (
          <>
            <dt>{copy.inspector.ownerTeam}</dt>
            <dd>{node.owner_team}</dd>
          </>
        )}
        {node.valid_from && (
          <>
            <dt>{copy.inspector.validFrom}</dt>
            <dd>{fmtDateTime(node.valid_from)}</dd>
          </>
        )}
        {node.action && (
          <>
            <dt>Reversibility</dt>
            <dd>{copy.ledger.reversibility[node.action.reversibility] ?? node.action.reversibility}</dd>
          </>
        )}
        <dt>Built on this</dt>
        <dd>{copy.inspector.descendants(descendants)}</dd>
      </dl>
      <h4 className="sub">{copy.inspector.history}</h4>
      <ul className="hist">
        {history.map((h, i) => (
          <li key={i}>
            <span className="num">{fmtTime(h.ts)}</span>
            <span>
              {copy.status[h.status] ?? h.status}. {h.reason}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

const KEYWORDS = /\b(MATCH|OPTIONAL MATCH|WHERE|WITH|SET|RETURN|CREATE|MERGE|UNWIND|ORDER BY|ON CREATE SET|AND|ALL|IN|NOT|DISTINCT|AS)\b/g;

function Cypher({ text }: { text: string }) {
  const parts = text.split(KEYWORDS);
  return (
    <pre className="mono">
      {parts.map((p, i) =>
        i % 2 === 1 ? (
          <span key={i} className="kw">
            {p}
          </span>
        ) : (
          <span key={i}>{p}</span>
        ),
      )}
    </pre>
  );
}

function LinksTab({ node }: { node: GraphNode }) {
  const nodesRec = useStore((s) => s.nodes);
  const edgesRec = useStore((s) => s.edges);
  const set = useStore((s) => s.set);
  const all = Object.values(nodesRec);
  const adj = adjacency(all, Object.values(edgesRec));
  const list = (ids: string[]) =>
    ids.length === 0 ? (
      <p className="muted small">{tools.links.none}</p>
    ) : (
      <ul className="links-list">
        {ids.map((i) => {
          const n = nodesRec[i];
          return n ? (
            <li key={i}>
              <button
                onClick={() => {
                  set({ selectedId: i });
                  graphBus.focus.emit(i);
                }}
              >
                <Icon name={kindIcon(n)} size={14} />
                <span>{n.label}</span>
                <Chip tone={statusTone(n)}>{copy.status[n.status] ?? n.status}</Chip>
              </button>
            </li>
          ) : null;
        })}
      </ul>
    );
  return (
    <div className="links">
      <p className="card-sub">{tools.links.degree(degree(adj, node.id))}</p>
      <h4 className="sub">{tools.links.builtFrom}</h4>
      {list(adj.inn.get(node.id) ?? [])}
      <h4 className="sub">{tools.links.feeds}</h4>
      {list(adj.out.get(node.id) ?? [])}
      <h4 className="sub">{tools.links.explain}</h4>
      <div className="ins-explain">
        {explain(node, adj, all).map((l, i) => (
          <p key={i}>{l}</p>
        ))}
      </div>
    </div>
  );
}

function CypherTab({ nodeId }: { nodeId: string }) {
  const rid = useStore((s) => s.nodeRequest[nodeId]);
  const q = useQuery({ queryKey: ['cypher', rid], queryFn: () => getCypher(rid as string), enabled: !!rid });
  if (!rid) return <Empty>{copy.inspector.cypherEmpty}</Empty>;
  if (q.isLoading) return <Skeleton lines={4} />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />;
  if (!q.data?.length) return <Empty>{copy.inspector.cypherEmpty}</Empty>;
  return (
    <div className="cypher">
      <p className="muted small">
        {copy.inspector.cypherNote} <span className="mono">{rid}</span>
      </p>
      {q.data.map((c, i) => (
        <article key={i} className="cypher-entry">
          <header>
            <strong className="mono">{c.name}</strong>
            <span className="num timing-badge" title={`${c.ms} ms; ${c.timing_boundary ?? 'measurement boundary unknown'}; ${copy.inspector.rows(c.rows)}`}>
              {queryTiming(c)}
            </span>
          </header>
          <Cypher text={c.cypher} />
        </article>
      ))}
    </div>
  );
}

export function Inspector() {
  const id = useStore((s) => s.selectedId);
  const tab = useStore((s) => s.inspectorTab);
  const asOf = useStore((s) => s.asOf);
  const set = useStore((s) => s.set);
  const q = useQuery({ queryKey: ['node', id], queryFn: () => getNode(id as string), enabled: !!id && !asOf });
  const live = useStore((s) => (id ? s.nodes[id] : undefined));
  const past = useStore((s) => (id && s.past ? s.past.nodes.find((n) => n.id === id) : undefined));
  const node = asOf ? past : (live ?? q.data?.node);

  return (
    <section className="card inspector" aria-label={copy.inspector.title}>
      <header className="card-h">
        <h2>{copy.inspector.title}</h2>
        <button
          className="btn quiet small cypher-drawer-toggle"
          title="Inspect FalkorDB Cypher queries"
          aria-label="Inspect Cypher queries"
          onClick={() => {
            const st = useStore.getState();
            if (!st.selectedId) {
              const first = Object.keys(st.nodes)[0];
              if (first) st.set({ selectedId: first, inspectorTab: 'cypher' });
            } else {
              st.set({ inspectorTab: 'cypher' });
            }
          }}
          style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: '4px', fontSize: '0.78rem', padding: '3px 8px' }}
        >
          <Icon name="console" size={13} />
          <span>Cypher</span>
        </button>
        {id && (
          <button className="iconbtn" aria-label={copy.dismiss} onClick={() => set({ selectedId: null })}>
            <Icon name="x" size={14} />
          </button>
        )}
      </header>
      {!id && (
        <div className="inspector-empty">
          <Icon name="graph" size={26} />
          <p>{copy.inspector.empty}</p>
        </div>
      )}
      {id && (
        <>
          <div className="seg wide" role="tablist">
            {TABS.map((t) => (
              <button
                key={t}
                role="tab"
                aria-selected={tab === t}
                className={`seg-btn${tab === t ? ' on' : ''}${t === 'cypher' ? ' cypher-drawer-toggle' : ''}`}
                onClick={() => set({ inspectorTab: t })}
              >
                {copy.inspector.tabs[t]}
              </button>
            ))}
          </div>
          <div className="tabbody" role="tabpanel">
            {q.isLoading && !node && <Skeleton lines={5} />}
            {q.isError && !node && <ErrorState error={q.error} onRetry={() => void q.refetch()} />}
            {tab === 'path' && q.data && <PathTab nodes={q.data.path.map((p) => (asOf ? p : (useStore.getState().nodes[p.id] ?? p)))} />}
            {tab === 'path' && asOf && node && <PathTab nodes={[node]} />}
            {tab === 'details' && node && (
              <DetailsTab node={node} descendants={q.data?.descendant_count ?? 0} history={q.data?.status_history ?? []} />
            )}
            {tab === 'links' && node && <LinksTab node={node} />}
            {tab === 'cypher' && <CypherTab nodeId={id} />}
          </div>
        </>
      )}
    </section>
  );
}
