import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import type { CorrectionNotice } from '../api/schemas';
import { approveCorrection } from '../state/endpoints';
import { queryClient } from '../state/queries';
import { useStore } from '../state/store';
import { Icon } from './Icon';

interface DamageControlModalProps {
  notice: CorrectionNotice;
  onClose: () => void;
}

export function DamageControlModal({ notice, onClose }: DamageControlModalProps) {
  const pushToast = useStore((s) => s.pushToast);
  const [currentStatus, setCurrentStatus] = useState(notice.status);

  const dispatchMutation = useMutation({
    mutationFn: () => approveCorrection(notice.target_action_id),
    onSuccess: (result) => {
      setCurrentStatus(result.status);
      pushToast('Correction approved. Delivery is pending.');
      void queryClient.invalidateQueries({ queryKey: ['actions'] });
      void queryClient.invalidateQueries({ queryKey: ['handoffs'] });
      void queryClient.invalidateQueries({ queryKey: ['graph'] });
    },
    onError: (err) => {
      pushToast(`Failed to approve correction: ${err instanceof Error ? err.message : 'Unknown error'}`);
    },
  });

  const isDispatched = currentStatus === 'DISPATCHED';
  const isApproved = currentStatus === 'APPROVED_PENDING_DELIVERY';

  return (
    <div className="modal-back" onClick={onClose}>
      <div
        className="modal damage-control-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="dc-modal-title"
        onClick={(e) => e.stopPropagation()}
        style={{
          maxWidth: '680px',
          width: '92vw',
          maxHeight: '90vh',
          overflowY: 'auto',
          border: '1px solid var(--warn, #e5a00d)',
          boxShadow: '0 12px 36px rgba(0,0,0,0.4)',
        }}
      >
        <header style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', borderBottom: '1px solid var(--line-3, #333)', paddingBottom: '12px' }}>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px' }}>
              <span style={{ fontSize: '1.2rem' }}>⚠️</span>
              <h2 id="dc-modal-title" style={{ margin: 0, fontSize: '1.15rem', fontWeight: 700, color: 'var(--warn, #e5a00d)' }}>
                DAMAGE CONTROL: Correction Drafted
              </h2>
            </div>
            <p className="muted small" style={{ margin: 0 }}>
              Action ID: <span className="mono">{notice.target_action_id}</span> • Harm mitigation for quarantined irreversible action
            </p>
          </div>
          <button className="iconbtn" aria-label="Close" onClick={onClose}>
            <Icon name="x" size={16} />
          </button>
        </header>

        {/* Status indicator bar */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            background: isDispatched ? 'rgba(76, 175, 80, 0.15)' : 'rgba(229, 160, 13, 0.12)',
            padding: '8px 12px',
            borderRadius: '6px',
            border: `1px solid ${isDispatched ? 'var(--good, #4caf50)' : 'var(--warn, #e5a00d)'}`,
          }}
        >
          <span style={{ fontSize: '0.85rem', fontWeight: 600 }}>
            {`Status: ${currentStatus.replaceAll('_', ' ')}`}
          </span>
          <span
            style={{
              fontSize: '0.75rem',
              fontWeight: 700,
              padding: '2px 8px',
              borderRadius: '12px',
              background: isDispatched ? 'var(--good, #4caf50)' : 'var(--warn, #e5a00d)',
              color: '#000',
            }}
          >
            {isDispatched ? '✓ SENT' : isApproved ? 'DELIVERY PENDING' : 'ACTION REQUIRED'}
          </span>
        </div>

        {/* Side-by-side / Comparison View */}
        <div style={{ display: 'grid', gap: '16px' }}>
          {/* 1. Poisoned Email */}
          <section
            style={{
              background: 'rgba(244, 67, 54, 0.08)',
              border: '1px solid rgba(244, 67, 54, 0.4)',
              borderRadius: '8px',
              padding: '12px 16px',
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
              <span
                style={{
                  fontSize: '0.75rem',
                  fontWeight: 700,
                  color: '#ff6b6b',
                  letterSpacing: '0.5px',
                  textTransform: 'uppercase',
                }}
              >
                ⛔ Original Poisoned Email (Sent to Customer)
              </span>
              <span className="muted small mono">To: {notice.recipient}</span>
            </div>
            <div style={{ fontSize: '0.9rem', marginBottom: '6px' }}>
              <strong>Subject:</strong> <span style={{ textDecoration: 'line-through', opacity: 0.85 }}>{notice.original_subject}</span>
            </div>
            <div
              style={{
                background: 'rgba(0,0,0,0.2)',
                padding: '10px 12px',
                borderRadius: '6px',
                borderLeft: '3px solid #ff6b6b',
                fontStyle: 'italic',
                textDecoration: 'line-through',
                color: '#ff9999',
                fontSize: '0.88rem',
              }}
            >
              "{notice.original_statement}"
            </div>
            <div style={{ marginTop: '8px', fontSize: '0.75rem', color: '#ff6b6b', opacity: 0.9 }}>
              ⚠️ Sent under invalidated 90-day partner feed. Contradicts verified root policy (30 days).
            </div>
          </section>

          {/* 2. Auto-Generated Retraction Email */}
          <section
            style={{
              background: 'rgba(46, 125, 50, 0.1)',
              border: '1px solid rgba(76, 175, 80, 0.4)',
              borderRadius: '8px',
              padding: '12px 16px',
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px', flexWrap: 'wrap', gap: '6px' }}>
              <span
                style={{
                  fontSize: '0.75rem',
                  fontWeight: 700,
                  color: '#81c784',
                  letterSpacing: '0.5px',
                  textTransform: 'uppercase',
                }}
              >
                ✅ Auto-Generated Retraction Email
              </span>
              <span
                style={{
                  fontSize: '0.75rem',
                  background: 'rgba(76, 175, 80, 0.25)',
                  color: '#a5d6a7',
                  padding: '2px 8px',
                  borderRadius: '10px',
                  border: '1px solid rgba(76, 175, 80, 0.4)',
                  fontWeight: 600,
                }}
              >
                Verified Truth: {notice.verified_policy_citation}
              </span>
            </div>
            <div style={{ fontSize: '0.9rem', marginBottom: '6px' }}>
              <strong>Subject:</strong> <span style={{ color: '#a5d6a7', fontWeight: 600 }}>{notice.correction_subject}</span>
            </div>
            <div
              style={{
                background: 'rgba(0,0,0,0.25)',
                padding: '12px 14px',
                borderRadius: '6px',
                borderLeft: '3px solid #81c784',
                fontSize: '0.85rem',
                lineHeight: 1.55,
                whiteSpace: 'pre-wrap',
                fontFamily: 'inherit',
                color: 'var(--fg, #e0e0e0)',
              }}
            >
              {notice.drafted_body}
            </div>
          </section>
        </div>

        {/* Modal Actions */}
        <footer style={{ display: 'flex', justifyContent: 'flex-end', gap: '10px', marginTop: '12px', borderTop: '1px solid var(--line-3, #333)', paddingTop: '12px' }}>
          <button className="btn" onClick={onClose}>
            Close
          </button>
          {!isDispatched && !isApproved ? (
            <button
              className="btn review"
              disabled={dispatchMutation.isPending}
              onClick={() => dispatchMutation.mutate()}
              style={{
                background: 'var(--warn, #e5a00d)',
                color: '#000',
                fontWeight: 700,
                border: 'none',
                padding: '8px 18px',
              }}
            >
              {dispatchMutation.isPending ? 'Approving...' : 'Approve Correction'}
            </button>
          ) : (
            <button className="btn" disabled style={{ background: 'var(--good, #4caf50)', color: '#fff', border: 'none' }}>
              {isDispatched ? 'Correction delivered' : 'Approved · delivery pending'}
            </button>
          )}
        </footer>
      </div>
    </div>
  );
}
