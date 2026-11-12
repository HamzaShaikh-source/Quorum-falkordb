import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  byId,
  cascadeFrom,
  edgePath,
  EDGES,
  H,
  NH,
  NODES,
  NW,
  W,
  type ActionPhase,
  type CascadeStats,
  type LNode,
} from './graph';

const HOP_MS = 150;
const reduced = () => typeof matchMedia !== 'undefined' && matchMedia('(prefers-reduced-motion: reduce)').matches;

export interface Cascade {
  bad: Set<string>;
  phase: Record<string, ActionPhase>;
  pulse: Set<string>;
  notice: string | null;
  stats: CascadeStats;
  quarantine: (id: string) => void;
  restore: () => void;
}

/** The cascade rules from the product, in miniature: root facts are never quarantined; their challenger is. */
export function useCascade(): Cascade {
  const [bad, setBad] = useState<Set<string>>(new Set());
  const [phase, setPhase] = useState<Record<string, ActionPhase>>({});
  const [pulse, setPulse] = useState<Set<string>>(new Set());
  const [notice, setNotice] = useState<string | null>(null);
  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);

  const clear = () => {
    timers.current.forEach(clearTimeout);
    timers.current = [];
  };
  useEffect(() => clear, []);

  const later = (ms: number, fn: () => void) => {
    if (reduced()) {
      fn();
      return;
    }
    timers.current.push(setTimeout(fn, ms));
  };

  const restore = useCallback(() => {
    clear();
    setBad(new Set());
    setPhase({});
    setPulse(new Set());
    setNotice(null);
  }, []);

  const quarantine = useCallback((id: string) => {
    const node = byId[id];
    if (!node) return;
    let target = id;
    if (node.root) {
      setNotice(`${node.label} is a root fact. Root facts are never auto-quarantined; the challenger is.`);
      target = 'f2';
    } else {
      setNotice(null);
    }
    clear();
    setBad(new Set());
    setPhase({});
    const depths = cascadeFrom(target);
    [...depths.entries()].forEach(([nid, d]) => {
      later(d * HOP_MS, () => {
        setBad((prev) => new Set(prev).add(nid));
        setPulse((prev) => new Set(prev).add(nid));
        later(520, () =>
          setPulse((prev) => {
            const n = new Set(prev);
            n.delete(nid);
            return n;
          }),
        );
        const n = byId[nid];
        if (n?.kind === 'action') {
          if (n.executed) {
            setPhase((p) => ({ ...p, [nid]: 'rolling' }));
            later(1000 + d * 80, () => setPhase((p) => ({ ...p, [nid]: 'rolled' })));
          } else {
            setPhase((p) => ({ ...p, [nid]: 'cancelled' }));
          }
        }
      });
    });
  }, []);

  const stats = useMemo<CascadeStats>(() => {
    const vals = Object.values(phase);
    return {
      held: bad.size,
      rolling: vals.filter((v) => v === 'rolling').length,
      rolled: vals.filter((v) => v === 'rolled').length,
      cancelled: vals.filter((v) => v === 'cancelled').length,
    };
  }, [bad, phase]);

  return { bad, phase, pulse, notice, stats, quarantine, restore };
}

function actionLabel(n: LNode, bad: boolean, p: ActionPhase | undefined): string {
  if (!bad) return n.executed ? 'Ran' : n.blocked ? 'Held for review' : 'Staged';
  if (p === 'rolled') return 'Rolled back';
  if (p === 'rolling') return 'Rolling back';
  if (p === 'cancelled') return 'Cancelled';
  return n.executed ? 'Rolling back' : 'Cancelled';
}

interface Props {
  cascade: Cascade;
  interactive?: boolean;
  theme?: 'dark' | 'light';
  lit?: Set<string>;
  hint?: string;
  suspect?: string;
  label: string;
}

export function LandingGraph({ cascade, interactive = false, theme = 'dark', lit, hint, suspect, label }: Props) {
  const { bad, phase, pulse, quarantine } = cascade;
  const [hover, setHover] = useState<string | null>(null);
  const preview = useMemo(() => (hover && interactive ? cascadeFrom(byId[hover]?.root ? 'f2' : hover) : null), [hover, interactive]);

  return (
    <svg className={`lg ${theme}`} viewBox={`0 0 ${W} ${H}`} role="group" aria-label={label}>
      {['SOURCES', 'FACTS', 'CONCLUSIONS', 'ACTIONS'].map((c, i) => (
        <text key={c} className="lg-col" x={[82, 318, 556, 806][i]} y={14} textAnchor="middle">
          {c}
        </text>
      ))}
      {EDGES.map((e) => {
        const a = byId[e.from] as LNode;
        const b = byId[e.to] as LNode;
        const isBad = bad.has(e.to);
        const dim = lit && !(lit.has(e.from) && lit.has(e.to));
        const dry = preview?.has(e.from) && preview.has(e.to);
        return (
          <path
            key={`${e.from}-${e.to}`}
            d={edgePath(a, b)}
            className={`lg-edge${isBad ? ' bad' : ''}${dim ? ' dim' : ''}${dry ? ' dry' : ''}`}
            markerEnd={undefined}
          />
        );
      })}
      {NODES.map((n) => {
        const isBad = bad.has(n.id);
        const dim = lit && !lit.has(n.id);
        const dry = preview?.has(n.id);
        const state = n.kind === 'action' ? actionLabel(n, isBad, phase[n.id]) : null;
        const cls = `lg-node k-${n.kind}${isBad ? ' bad' : ''}${dim ? ' dim' : ''}${pulse.has(n.id) ? ' pulse' : ''}${dry && !isBad ? ' dry' : ''}${interactive ? ' hot' : ''}${n.root ? ' root' : ''}${suspect === n.id ? ' suspect' : ''}`;
        const w = n.kind === 'source' ? NW - 22 : NW;
        const handlers = interactive
          ? {
              onClick: () => quarantine(n.id),
              onMouseEnter: () => setHover(n.id),
              onMouseLeave: () => setHover(null),
              onFocus: () => setHover(n.id),
              onBlur: () => setHover(null),
              onKeyDown: (e: React.KeyboardEvent) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  quarantine(n.id);
                }
              },
              tabIndex: 0,
              role: 'button' as const,
              'aria-label': `${n.label}. ${n.root ? 'Root fact: quarantines the challenger.' : 'Quarantine this and everything built on it.'}`,
            }
          : {};
        return (
          <g key={n.id} className={cls} transform={`translate(${n.x - w / 2} ${n.y - NH / 2})`} {...handlers}>
            {n.kind === 'source' && <path className="lg-shape" d={`M0 0 H${w - 14} L${w} 14 V${NH} H0 Z`} />}
            {n.kind === 'fact' && <rect className="lg-shape" width={w} height={NH} rx={NH / 2} />}
            {n.kind === 'conclusion' && <rect className="lg-shape" width={w} height={NH - 8} y={4} rx={9} />}
            {n.kind === 'decision' && (
              <>
                <rect className="lg-shape" width={w} height={NH - 8} y={4} rx={3} />
                <rect className="lg-shape inner" x={4} y={8} width={w - 8} height={NH - 16} rx={1} />
              </>
            )}
            {n.kind === 'action' && <rect className="lg-shape" width={w} height={NH} rx={5} />}
            <text className="lg-label" x={n.kind === 'fact' ? w / 2 : 14} y={state ? NH / 2 - 3 : NH / 2 + 4} textAnchor={n.kind === 'fact' ? 'middle' : 'start'}>
              {n.label}
            </text>
            {state && (
              <text className="lg-state" x={14} y={NH / 2 + 13}>
                {state}
              </text>
            )}
            {n.sub && (
              <text className="lg-sub" x={14} y={NH / 2 + 15}>
                {n.sub}
              </text>
            )}
          </g>
        );
      })}
      {hint && (
        <text className="lg-hint" x={W / 2} y={H - 2} textAnchor="middle">
          {hint}
        </text>
      )}
    </svg>
  );
}
