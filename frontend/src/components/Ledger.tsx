import { useEffect, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { ApiError } from '../api/client';
import type { CorrectionNotice, Handoff } from '../api/schemas';
import { copy } from '../copy';
import { graphBus } from '../graph/bus';
import { actionDisplayState, actionTone } from '../state/actionState';
import { getActions, getHandoffs, reviewHandoff } from '../state/endpoints';
import { queryClient } from '../state/queries';
import { useStore } from '../state/store';
import { DamageControlModal } from './DamageControlModal';
import { Chip, Empty, ErrorState, Skeleton } from './ui';

function useNow(ms = 500) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(t);
  }, [ms]);
  return now;
}

function LeaseRing({ until }: { until: number }) {
  const now = useNow();
  const total = 60_000;
  const left = Math.max(0, until - now);
  const frac = Math.min(1, left / total);
  const r = 11;
  const c = 2 * Math.PI * r;
  return (
    <span className="lease" title={left ? copy.ledger.lease(Math.ceil(left / 1000)) : copy.ledger.leaseExpired}>
      <svg width="28" height="28" viewBox="0 0 28 28" aria-hidden="true">
        <circle cx="14" cy="14" r={r} fill="none" stroke="var(--hairline)" strokeWidth="3" />
        <circle
          cx="14"
          cy="14"
          r={r}
          fill="none"
          stroke={left ? 'var(--ultramarine)' : 'var(--stamp)'}
          strokeWidth="3"
          strokeDasharray={c}
          strokeDashoffset={c * (1 - frac)}
          transform="rotate(-90 14 14)"
        />
      </svg>
      <span className="num">{left ? Math.ceil(left / 1000) : 0}s</span>
    </span>
  );
}

function HandoffCard({ h, onOpenNotice }: { h: Handoff; onOpenNotice?: (n: CorrectionNotice) => void }) {
  const race = useStore((s) => s.claimRace);
  const pushToast = useStore((s) => s.pushToast);
  const set = useStore((s) => s.set);
  const now = useNow(250);
  const raced = race && race.handoffId === h.id && now - race.ts < 4000 ? race : null;
  const decide = useMutation({
    mutationFn: (d: 'approve' | 'reject') => reviewHandoff(h.id, d),
    onSuccess: (_r, d) => {
      pushToast(copy.toasts.reviewed(d === 'approve' ? 'approved' : 'rejected'));
      void queryClient.invalidateQueries({ queryKey: ['handoffs'] });
    },
    onError: (e) => {
      pushToast(e instanceof ApiError && e.code === 'FORBIDDEN_ROLE' ? copy.toasts.reviewForbidden : copy.errors.generic(e instanceof ApiError ? e.code : 'ERROR', 'Could not record the decision.'));
    },
  });
  const needsHuman = h.kind !== 'TASK' && h.status !== 'COMPLETED';

  return (
    <li className={`handoff kind-${h.kind}${raced ? ' racing' : ''}`}>
      <div className="handoff-head">
        <Chip tone={h.kind === 'TASK' ? 'neutral' : 'ultra'}>{copy.ledger.kinds[h.kind] ?? h.kind}</Chip>
        <Chip tone={h.status === 'COMPLETED' ? 'good' : h.status === 'TIMED_OUT' ? 'bad' : 'neutral'}>
          {copy.ledger.handoffStatus[h.status] ?? h.status}
        </Chip>
        {h.status === 'IN_PROGRESS' && h.lease_until && <LeaseRing until={h.lease_until} />}
      </div>
      <p className="handoff-task">{h.task}</p>
      {h.claimed_by && <p className="muted">{copy.ledger.claimedBy(h.claimed_by)}</p>}
      {raced && (
        <p className="race" role="status">
          <span className="winner">{copy.ledger.race(raced.winner)}</span>
          {raced.losers.map((l) => (
            <span key={l} className="loser">
              {l}: {copy.ledger.lost}
            </span>
          ))}
        </p>
      )}
      <div className="handoff-actions">
        {h.context_root_id && (
          <button
            className="btn ghost"
            onClick={() => {
              set({ selectedId: h.context_root_id ?? null, tab: 'graph' });
              if (h.context_root_id) graphBus.focus.emit(h.context_root_id);
            }}
          >
            Show in graph
          </button>
        )}
        {h.correction_notice && onOpenNotice && (
          <button
            className="btn"
            style={{
              borderColor: 'var(--warn-line, #b87500)',
              background: 'var(--warn-dim, rgba(184, 117, 0, 0.14))',
              color: 'var(--text)',
              fontWeight: 600,
            }}
            onClick={() => onOpenNotice(h.correction_notice!)}
          >
            ⚠️ Review Retraction Draft
          </button>
        )}
        {needsHuman && !h.correction_notice && (
          <>
            <button className="btn review" disabled={decide.isPending} onClick={() => decide.mutate('approve')}>
              {copy.ledger.approve}
            </button>
            <button className="btn" disabled={decide.isPending} onClick={() => decide.mutate('reject')}>
              {copy.ledger.reject}
            </button>
          </>
        )}
      </div>
    </li>
  );
}

export function Ledger() {
  const actions = useQuery({ queryKey: ['actions'], queryFn: getActions });
  const handoffs = useQuery({ queryKey: ['handoffs'], queryFn: getHandoffs });
  const set = useStore((s) => s.set);
  const [modalNotice, setModalNotice] = useState<CorrectionNotice | null>(null);

  const activeNotice =
    handoffs.data?.find((h) => h.correction_notice)?.correction_notice ??
    actions.data?.find((a) => a.action?.correction_notice)?.action?.correction_notice ??
    null;

  const reviews = (handoffs.data ?? []).filter((h) => h.kind !== 'TASK');
  const tasks = (handoffs.data ?? []).filter((h) => h.kind === 'TASK');

  return (
    <div className="ledger">
      {activeNotice && (
        <div
          className="damage-control-banner"
          style={{
            gridColumn: '1 / -1',
            padding: '14px 18px',
            background: 'var(--warn-dim, rgba(184, 117, 0, 0.14))',
            border: '1px solid var(--warn-line, #b87500)',
            borderRadius: 'var(--r-m, 12px)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: '12px',
            boxShadow: 'var(--shadow-2, 4px 4px 0 #000)',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            <span style={{ fontSize: '1.4rem' }}>⚠️</span>
            <div>
              <strong style={{ fontSize: '0.95rem', color: 'var(--text)', display: 'block' }}>
                DAMAGE CONTROL ACTIVE: Retraction Email Drafted
              </strong>
              <span className="muted small" style={{ color: 'var(--text-2)' }}>
                Executed irreversible action (<span className="mono">{activeNotice.target_action_id}</span>) hit by quarantine cascade. Retraction email auto-generated for human review.
              </span>
            </div>
          </div>
          <button
            className="btn review"
            onClick={() => setModalNotice(activeNotice)}
            style={{
              background: 'var(--warn, #8f5a00)',
              color: '#fff',
              fontWeight: 600,
              whiteSpace: 'nowrap',
            }}
          >
            Review Retraction Draft
          </button>
        </div>
      )}
      <section className="panel" aria-label={copy.ledger.actionsTitle}>
        <h2 className="panel-h">{copy.ledger.actionsTitle}</h2>
        {actions.isLoading && <Skeleton lines={4} />}
        {actions.isError && <ErrorState error={actions.error} onRetry={() => void actions.refetch()} />}
        {actions.data && actions.data.length === 0 && <Empty>{copy.ledger.noActions}</Empty>}
        <ul className="actions">
          {actions.data?.map((a) => {
            const ds = actionDisplayState(a);
            return (
              <li key={a.id}>
                <div
                  className="action-row"
                  role="button"
                  tabIndex={0}
                  onClick={() => {
                    set({ selectedId: a.id, tab: 'graph' });
                    graphBus.focus.emit(a.id);
                  }}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      set({ selectedId: a.id, tab: 'graph' });
                      graphBus.focus.emit(a.id);
                    }
                  }}
                >
                  <strong>{a.label}</strong>
                  <Chip tone={actionTone(ds)}>{copy.actionState[ds]}</Chip>
                  {a.action?.correction_notice && (
                    <button
                      className="btn ghost small"
                      style={{
                        marginLeft: 'auto',
                        borderColor: 'var(--warn-line, #b87500)',
                        background: 'var(--warn-dim, rgba(184, 117, 0, 0.14))',
                        color: 'var(--text)',
                        fontWeight: 600,
                        fontSize: '0.78rem',
                        padding: '2px 8px',
                      }}
                      onClick={(e) => {
                        e.stopPropagation();
                        setModalNotice(a.action!.correction_notice!);
                      }}
                    >
                      ⚠️ Retraction Drafted
                    </button>
                  )}
                  <span className="muted meta">
                    {a.action ? (copy.ledger.reversibility[a.action.reversibility] ?? a.action.reversibility) : ''}
                    {a.action?.compensation
                      ? `. Rollback ${copy.ledger.rollbackStatus[a.action.compensation.status] ?? 'unknown'}, ${a.action.compensation.attempts} attempt${a.action.compensation.attempts === 1 ? '' : 's'}`
                      : ''}
                  </span>
                </div>
              </li>
            );
          })}
        </ul>
      </section>
      <section className="panel" aria-label={copy.ledger.handoffsTitle}>
        <h2 className="panel-h">{copy.ledger.review}</h2>
        {handoffs.isLoading && <Skeleton lines={3} />}
        {handoffs.isError && <ErrorState error={handoffs.error} onRetry={() => void handoffs.refetch()} />}
        {handoffs.data && reviews.length === 0 && <Empty>{copy.ledger.noHandoffs}</Empty>}
        <ul className="handoffs">{reviews.map((h) => <HandoffCard key={h.id} h={h} onOpenNotice={setModalNotice} />)}</ul>
        <h2 className="panel-h second">{copy.ledger.handoffsTitle}</h2>
        {handoffs.data && tasks.length === 0 && <Empty>{copy.ledger.noHandoffs}</Empty>}
        <ul className="handoffs">{tasks.map((h) => <HandoffCard key={h.id} h={h} onOpenNotice={setModalNotice} />)}</ul>
      </section>
      {modalNotice && (
        <DamageControlModal notice={modalNotice} onClose={() => setModalNotice(null)} />
      )}
    </div>
  );
}
