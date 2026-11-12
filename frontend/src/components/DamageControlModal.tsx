import { useEffect, useState } from 'react';
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

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

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
          background: '#ffffff',
          border: '1.5px solid var(--text, #0b1218)',
          boxShadow: '8px 8px 0 var(--text, #0b1218)',
        }}
      >
        <header
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'flex-start',
            borderBottom: '1.5px solid var(--line-2, #cfd5cf)',
            paddingBottom: '12px',
          }}
        >
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px' }}>
              <span style={{ fontSize: '1.2rem' }} aria-hidden="true">⚠️</span>
              <h2
                id="dc-modal-title"
                style={{
                  margin: 0,
                  fontSize: '1.15rem',
                  fontWeight: 700,
                  color: 'var(--text, #0b1218)',
                  fontFamily: 'var(--f-display)',
                }}
              >
                DAMAGE CONTROL: Correction Drafted
              </h2>
            </div>
            <p className="muted small" style={{ margin: 0, color: 'var(--text-2, #2d3842)' }}>
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
            background: isDispatched || isApproved ? 'var(--ok-dim, #dcf8ea)' : '#fff3d6',
            padding: '9px 14px',
            borderRadius: '10px',
            border: `1.5px solid ${isDispatched || isApproved ? 'var(--ok, #0c6b49)' : 'var(--warn, #8f5a00)'}`,
          }}
        >
          <span style={{ fontSize: '0.85rem', fontWeight: 700, color: 'var(--text, #0b1218)' }}>
            {`Status: ${currentStatus.replaceAll('_', ' ')}`}
          </span>
          <span
            style={{
              fontSize: '0.75rem',
              fontWeight: 700,
              padding: '3px 10px',
              borderRadius: '999px',
              background: isDispatched || isApproved ? 'var(--mint, #2fe6a6)' : '#ffe9a8',
              border: '1.5px solid var(--text, #0b1218)',
              color: 'var(--text, #0b1218)',
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
              background: 'var(--bad-dim, #fde3df)',
              border: '1.5px solid var(--bad, #d13825)',
              borderRadius: '12px',
              padding: '14px 16px',
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px', flexWrap: 'wrap', gap: '6px' }}>
              <span
                style={{
                  fontSize: '0.75rem',
                  fontWeight: 700,
                  color: '#a52716',
                  letterSpacing: '0.04em',
                  textTransform: 'uppercase',
                }}
              >
                ⛔ Original Poisoned Email (Sent to Customer)
              </span>
              <span className="small mono" style={{ color: 'var(--text-2, #2d3842)', fontWeight: 600 }}>
                To: {notice.recipient}
              </span>
            </div>
            <div style={{ fontSize: '0.9rem', marginBottom: '8px', color: 'var(--text, #0b1218)' }}>
              <strong>Subject:</strong>{' '}
              <span style={{ textDecoration: 'line-through', color: 'var(--text-2, #2d3842)' }}>{notice.original_subject}</span>
            </div>
            <div
              style={{
                background: '#ffffff',
                padding: '10px 12px',
                borderRadius: '8px',
                border: '1px solid var(--bad-line, #e88e81)',
                borderLeft: '4px solid var(--bad, #d13825)',
                fontStyle: 'italic',
                textDecoration: 'line-through',
                color: '#8c1d10',
                fontSize: '0.88rem',
              }}
            >
              "{notice.original_statement}"
            </div>
            <div style={{ marginTop: '8px', fontSize: '0.78rem', color: '#8c1d10', fontWeight: 600 }}>
              ⚠️ Sent under invalidated 90-day partner feed. Contradicts verified root policy (30 days).
            </div>
          </section>

          {/* 2. Auto-Generated Retraction Email */}
          <section
            style={{
              background: 'var(--ok-dim, #dcf8ea)',
              border: '1.5px solid var(--ok, #0c6b49)',
              borderRadius: '12px',
              padding: '14px 16px',
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px', flexWrap: 'wrap', gap: '6px' }}>
              <span
                style={{
                  fontSize: '0.75rem',
                  fontWeight: 700,
                  color: 'var(--ok, #0c6b49)',
                  letterSpacing: '0.04em',
                  textTransform: 'uppercase',
                }}
              >
                ✅ Auto-Generated Retraction Email
              </span>
              <span
                style={{
                  fontSize: '0.75rem',
                  background: '#ffffff',
                  color: 'var(--ok, #0c6b49)',
                  padding: '2px 10px',
                  borderRadius: '999px',
                  border: '1.5px solid var(--ok, #0c6b49)',
                  fontWeight: 700,
                }}
              >
                Verified Truth: {notice.verified_policy_citation}
              </span>
            </div>
            <div style={{ fontSize: '0.9rem', marginBottom: '8px', color: 'var(--text, #0b1218)' }}>
              <strong>Subject:</strong>{' '}
              <span style={{ color: 'var(--ok, #0c6b49)', fontWeight: 700 }}>{notice.correction_subject}</span>
            </div>
            <div
              style={{
                background: '#ffffff',
                padding: '12px 14px',
                borderRadius: '8px',
                border: '1px solid var(--ok-line, #66cfa8)',
                borderLeft: '4px solid var(--ok, #0c6b49)',
                fontSize: '0.86rem',
                lineHeight: 1.55,
                whiteSpace: 'pre-wrap',
                fontFamily: 'inherit',
                color: 'var(--text, #0b1218)',
              }}
            >
              {notice.drafted_body}
            </div>
          </section>
        </div>

        {/* Modal Actions */}
        <footer
          style={{
            display: 'flex',
            justifyContent: 'flex-end',
            gap: '10px',
            marginTop: '12px',
            borderTop: '1.5px solid var(--line-2, #cfd5cf)',
            paddingTop: '12px',
          }}
        >
          <button className="btn" onClick={onClose}>
            Close
          </button>
          {!isDispatched && !isApproved ? (
            <button
              className="btn primary"
              disabled={dispatchMutation.isPending}
              onClick={() => dispatchMutation.mutate()}
              style={{
                padding: '8px 18px',
              }}
            >
              {dispatchMutation.isPending ? 'Approving...' : 'Approve Correction'}
            </button>
          ) : (
            <button
              className="btn"
              disabled
              style={{
                background: 'var(--mint, #2fe6a6)',
                color: 'var(--text, #0b1218)',
                borderColor: 'var(--text, #0b1218)',
                fontWeight: 700,
                opacity: 1,
              }}
            >
              {isDispatched ? '✓ Correction delivered' : '✓ Approved · delivery pending'}
            </button>
          )}
        </footer>
      </div>
    </div>
  );
}
