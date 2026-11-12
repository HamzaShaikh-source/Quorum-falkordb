import { useMemo, useState } from 'react';
import type { GraphEdge, GraphNode } from '../api/schemas';
import { adjacency } from '../state/insights';
import { mapAreas, shortIds } from '../context/packer';
import { Icon } from './Icon';

const ROW = 30;
const COL = [0, 150, 330, 490];
const W = [124, 160, 140, 220];
const KIND_NAME: Record<string, string> = { S: 'Sources', F: 'Facts', C: 'Conclusions', A: 'Actions' };
const KIND_FULL: Record<string, string> = { source: 'S', fact: 'F', conclusion: 'C', action: 'A' };
const LEAF_CAP = 14;

interface Row {
  key: string;
  depth: number;
  label: string;
  sub?: string;
  tone: 'root' | 'area' | 'kind' | 'ok' | 'bad' | 'old';
  parent: number | null;
  node?: string;
  expandable: boolean;
  open: boolean;
}

interface Props {
  nodes: GraphNode[];
  edges: GraphEdge[];
  tenant: string;
  selected?: string | null;
  onPick?: (id: string) => void;
  /** show every leaf instead of capping long lists; used for the small live graph */
  compact?: boolean;
}

/**
 * The human view of the memory: a tree you can fold, organized the way the AI map is (area → kind → node),
 * so what you see is what the agent is told. Click a node to select it; click a branch to fold or unfold.
 */
export function MindMap({ nodes, edges, tenant, selected, onPick, compact }: Props) {
  const [closed, setClosed] = useState<Set<string>>(new Set());
  const [more, setMore] = useState<Set<string>>(new Set());
  const [q, setQ] = useState('');

  const tree = useMemo(() => {
    const adj = adjacency(nodes, edges);
    const ids = shortIds(nodes);
    const byId = new Map(nodes.map((n) => [n.id, n]));
    return { areas: mapAreas({ nodes, edges }, adj), ids, byId };
  }, [nodes, edges]);

  const needle = q.trim().toLowerCase();
  const rows = useMemo(() => {
    const out: Row[] = [];
    const push = (r: Omit<Row, 'open'> & { open?: boolean }) => (out.push({ open: false, ...r }), out.length - 1);
    const root = push({ key: 'root', depth: 0, label: tenant, sub: `${nodes.length} nodes`, tone: 'root', parent: null, expandable: false });
    tree.areas.forEach((area, ai) => {
      const aKey = `a${ai}`;
      const aOpen = !closed.has(aKey);
      const ar = push({
        key: aKey,
        depth: 1,
        label: area.name,
        sub: `${area.size}${area.quarantined ? ` · ${area.quarantined} bad` : ''}`,
        tone: area.quarantined ? 'bad' : 'area',
        parent: root,
        expandable: true,
        open: aOpen,
      });
      if (!aOpen) return;
      for (const k of ['S', 'F', 'C', 'A']) {
        const members = area.ids.map((id) => tree.byId.get(id)!).filter((n) => n && KIND_FULL[n.kind] === k);
        if (!members.length) continue;
        const kKey = `${aKey}${k}`;
        const kOpen = !closed.has(kKey);
        const kr = push({ key: kKey, depth: 2, label: KIND_NAME[k]!, sub: String(members.length), tone: 'kind', parent: ar, expandable: true, open: kOpen });
        if (!kOpen) continue;
        const cap = compact || more.has(kKey) ? members.length : LEAF_CAP;
        const shown = members.filter((n) => !needle || `${n.label} ${n.text ?? ''}`.toLowerCase().includes(needle)).slice(0, cap);
        for (const n of shown) {
          push({
            key: n.id,
            depth: 3,
            label: n.label,
            sub: tree.ids.get(n.id),
            tone: n.status === 'QUARANTINED' ? 'bad' : n.status === 'SUPERSEDED' || n.stale ? 'old' : 'ok',
            parent: kr,
            node: n.id,
            expandable: false,
          });
        }
        if (members.length > shown.length && !needle) {
          push({ key: `${kKey}-more`, depth: 3, label: `Show ${members.length - shown.length} more`, tone: 'kind', parent: kr, expandable: false, sub: '+' });
        }
      }
    });
    return out;
  }, [tree, closed, more, needle, nodes.length, tenant, compact]);

  const toggle = (key: string) =>
    setClosed((s) => {
      const n = new Set(s);
      if (n.has(key)) n.delete(key);
      else n.add(key);
      return n;
    });

  const height = rows.length * ROW + 12;
  const width = COL[3]! + W[3]! + 8;
  const mid = (i: number) => i * ROW + ROW / 2 + 4;

  return (
    <div className="mm">
      <div className="mm-tools">
        <label className="mm-search">
          <Icon name="search" size={14} />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Find in the map" aria-label="Find in the mind map" />
        </label>
        <button className="btn quiet" onClick={() => setClosed(new Set(tree.areas.flatMap((_, i) => [`a${i}`])))}>
          Fold all
        </button>
        <button className="btn quiet" onClick={() => setClosed(new Set())}>
          Unfold all
        </button>
      </div>
      <div className="mm-scroll" role="tree" aria-label="Memory mind map">
        <div className="mm-canvas" style={{ width, height }}>
          <svg width={width} height={height} aria-hidden="true" className="mm-lines">
            {rows.map((r, i) => {
              if (r.parent === null) return null;
              const p = rows[r.parent]!;
              const x1 = COL[p.depth]! + W[p.depth]!;
              const x2 = COL[r.depth]!;
              const y1 = mid(r.parent);
              const y2 = mid(i);
              const c = (x2 - x1) / 2;
              return <path key={r.key} d={`M${x1} ${y1} C${x1 + c} ${y1} ${x2 - c} ${y2} ${x2} ${y2}`} className={`mm-link ${r.tone}`} />;
            })}
          </svg>
          {rows.map((r, i) => {
            const isMore = r.key.endsWith('-more');
            const sel = r.node && r.node === selected;
            const hit = needle && r.node && r.label.toLowerCase().includes(needle);
            return (
              <button
                key={r.key}
                role="treeitem"
                aria-level={r.depth + 1}
                aria-expanded={r.expandable ? r.open : undefined}
                aria-selected={sel || undefined}
                className={`mm-node ${r.tone}${sel ? ' sel' : ''}${hit ? ' hit' : ''}${isMore ? ' more' : ''}`}
                style={{ left: COL[r.depth], top: i * ROW + 4, width: W[r.depth] }}
                title={r.label}
                onClick={() => {
                  if (isMore) setMore((s) => new Set(s).add(r.key.replace('-more', '')));
                  else if (r.expandable) toggle(r.key);
                  else if (r.node) onPick?.(r.node);
                }}
              >
                {r.expandable && <span className={`mm-caret${r.open ? ' open' : ''}`} aria-hidden="true" />}
                <span className="mm-label">{r.label}</span>
                {r.sub && <span className="mm-sub mono">{r.sub}</span>}
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
