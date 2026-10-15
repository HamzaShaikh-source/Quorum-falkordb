import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import type { CorrectionNotice } from '../api/schemas';
import { copy } from '../copy';
import { graphBus } from '../graph/bus';
import { getHandoffs, getReport } from '../state/endpoints';
import { useStore } from '../state/store';
import { DamageControlModal } from './DamageControlModal';
import { Icon } from './Icon';
import { ErrorState, Skeleton, useCountUp } from './ui';

interface Seg {
  label: string;
  value: number;
  color: string;
}

function Donut({ segs, total }: { segs: Seg[]; total: number }) {
  const r = 38;
  const c = 2 * Math.PI * r;
  let acc = 0;
  const shown = useCountUp(total, 600);
  return (
    <svg className="donut" viewBox="0 0 100 100" role="img" aria-label={`${total} nodes affected`}>
      <circle cx="50" cy="50" r={r} fill="none" stroke="var(--bg-4)" strokeWidth="12" />
      {total > 0 &&
        segs
          .filter((s) => s.value > 0)
          .map((s) => {
            const len = (s.value / total) * c;
            const el = (
              <circle
                key={s.label}
                cx="50"
                cy="50"
                r={r}
                fill="none"
                stroke={s.color}
                strokeWidth="12"
                strokeDasharray={`${Math.max(0, len - 1.5)} ${c}`}
                strokeDashoffset={-acc}
                transform="rotate(-90 50 50)"
              />
            );
            acc += len;
            return el;
          })}
      <text x="50" y="52" textAnchor="middle" className="donut-n">
        {shown}
      </text>
      <text x="50" y="65" textAnchor="middle" className="donut-l">
        nodes held
      </text>
    </svg>
  );
}

export function Containment() {
  const root = useStore((s) => s.reportRoot);
  const set = useStore((s) => s.set);
  const nodes = useStore((s) => s.nodes);
  const selected = useStore((s) => s.selectedId);
  const [compact, setCompact] = useState(false);
  const [modalNotice, setModalNotice] = useState<CorrectionNotice | null>(null);
  const [prevSel, setPrevSel] = useState(selected);
  if (prevSel !== selected) {
    setPrevSel(selected);
    setCompact(!!selected);
  }
  const q = useQuery({
    queryKey: ['report', root],
    queryFn: () => getReport(root as string),
    enabled: !!root && !!nodes[root],
  });
  const loaded = useStore((st) => st.loaded);
  const handoffs = useQuery({ queryKey: ['handoffs'], queryFn: getHandoffs, enabled: loaded });
  const ms = useCountUp(q.data?.ms_to_containment ?? 0);

  const nodeNotice = Object.values(nodes).find((n) => n.action?.correction_notice)?.action?.correction_notice;
  const activeNotice =
    nodeNotice ??
    handoffs.data?.find((h) => h.correction_notice)?.correction_notice ??
    null;

  if (!root || !nodes[root]) return null;
  const d = q.data;
  const segs: Seg[] = d
    ? [
        { label: copy.containment.facts, value: d.facts, color: 'var(--bad)' },
        { label: copy.containment.conclusions, value: d.conclusions, color: '#ff9a88' },
        { label: copy.containment.cancelled, value: d.actions_cancelled, color: 'var(--ghost)' },
        { label: copy.containment.compensated, value: d.actions_compensated, color: 'var(--ok)' },
        { label: copy.containment.escalated, value: d.actions_escalated, color: 'var(--person)' },
      ]
    : [];
  const total = segs.reduce((a, s) => a + s.value, 0);

  return (
    <section className="card containment" aria-label={copy.containment.title} aria-live="polite">
      <header className="card-h">
        <h2>{copy.containment.title}</h2>
        <div className="card-actions">
          <button className="iconbtn" aria-label={compact ? 'Expand report' : 'Collapse report'} aria-expanded={!compact} onClick={() => setCompact(!compact)}>
            <Icon name={compact ? 'collapse' : 'fit'} size={14} />
          </button>
          <button className="iconbtn" aria-label={copy.containment.dismiss} onClick={() => set({ reportRoot: null })}>
            <Icon name="x" size={14} />
          </button>
        </div>
      </header>
      {q.isLoading && (
        <>
          <p className="muted">{copy.containment.loading}</p>
          <Skeleton lines={3} />
        </>
      )}
      {q.isError && <ErrorState error={q.error} onRetry={() => void q.refetch()} />}
      {d && (
        <>
          <p className="root-line">
            <Icon name="alert" size={14} />
            {nodes[d.root_id]?.label ?? d.root_id}
          </p>
          <div className="hero-stat">
            <span className="hero-n num">{ms}</span>
            <span className="hero-u">ms</span>
            <span className="hero-l">{copy.containment.time}</span>
          </div>
          {activeNotice && (
            <div
              className="damage-control-card"
              style={{
                marginTop: '12px',
                marginBottom: '12px',
                padding: '12px 14px',
                background: 'var(--warn-dim, rgba(184, 117, 0, 0.14))',
                border: '1px solid var(--warn-line, #b87500)',
                borderRadius: 'var(--r-m, 12px)',
                boxShadow: 'var(--shadow-2, 3px 3px 0 #000)',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '6px' }}>
                <span style={{ fontSize: '1.2rem' }}>⚠️</span>
                <strong style={{ fontSize: '0.88rem', color: 'var(--text)' }}>
                  DAMAGE CONTROL ACTIVE: Correction Drafted
                </strong>
              </div>
              <p className="muted small" style={{ margin: '0 0 10px 0', fontSize: '0.78rem', lineHeight: 1.4 }}>
                Quarantined irreversible action (<span className="mono">{activeNotice.target_action_id}</span>) requires human review. Check the draft, recipient and verified replacement facts before approval.
              </p>
              <button
                className="btn review"
                style={{
                  background: 'var(--warn, #8f5a00)',
                  color: '#fff',
                  fontWeight: 600,
                  width: '100%',
                  padding: '8px 12px',
                  fontSize: '0.82rem',
                }}
                onClick={() => setModalNotice(activeNotice)}
              >
                Review Correction
              </button>
            </div>
          )}
          {!compact && (
          <>
          <div className="donut-row">
            <Donut segs={segs} total={total} />
            <ul className="donut-legend">
              {segs.map((s) => (
                <li key={s.label}>
                  <i style={{ background: s.color }} />
                  <span>{s.label}</span>
                  <b className="num">{s.value}</b>
                </li>
              ))}
            </ul>
          </div>
          <h3 className="sub">{copy.containment.byDepth}</h3>
          <ol className="depths">
            {d.node_ids_by_depth.map((ids, i) => (
              <li key={i}>
                <span className="depth-label">{copy.containment.hop(i)}</span>
                <span className="depth-nodes">
                  {ids.map((id) => (
                    <button
                      key={id}
                      className="nodechip"
                      onClick={() => {
                        set({ selectedId: id, tab: 'graph' });
                        graphBus.focus.emit(id);
                      }}
                    >
                      {nodes[id]?.label ?? id}
                    </button>
                  ))}
                </span>
              </li>
            ))}
          </ol>
          </>
          )}
        </>
      )}
      {modalNotice && (
        <DamageControlModal notice={modalNotice} onClose={() => setModalNotice(null)} />
      )}
    </section>
  );
}
