import { useState } from 'react';
import { Icon } from './Icon';
import { copyText } from '../workspace/clipboard';
import type { HealthResult } from '../api/health';
import type { Blocked } from '../api/blocked';

const FALLBACK_STEPS = [
  'Start FalkorDB: docker compose up -d falkordb',
  'Check it answers: python scripts/doctor.py',
  'Make sure QUORUM_FALKORDB_URL points at it (default redis://localhost:6379)',
  'Restart the backend if it was started before FalkorDB, then reload this page',
];

const BACKEND_STEPS = [
  'Start the backend: QUORUM_ENV=development QUORUM_EMBED_MCP=1 python -m uvicorn app.main:app --app-dir backend --port 8000',
  'Or with Docker: docker compose up -d',
  'Check everything: python scripts/doctor.py',
];

function command(step: string): string | null {
  const m = /:\s(.+)$/.exec(step);
  return m && /^(docker|python|QUORUM_|pip)/.test(m[1]!) ? m[1]! : null;
}

interface Props {
  result: HealthResult | null;
  blocked: Blocked | null;
  checking: boolean;
  onRetry: () => void;
}

/**
 * Shown instead of the product whenever FalkorDB is not running or not set up. There is no degraded mode:
 * Quorum stores every belief, handoff and action in FalkorDB, so without it there is nothing honest to show.
 */
export function BlockScreen({ result, blocked, checking, onRetry }: Props) {
  const [copied, setCopied] = useState<string | null>(null);
  const backendDown = result?.kind === 'unreachable';
  const reason = backendDown ? result.reason : (blocked?.reason ?? (result?.kind === 'blocked' ? result.health.falkordb.error : null)) ?? 'Checking the database…';
  const url = result?.kind === 'blocked' ? result.health.falkordb.url : (blocked?.url ?? '');
  const steps = backendDown ? BACKEND_STEPS : result?.kind === 'blocked' && result.health.steps.length ? result.health.steps : (blocked?.steps.length ? blocked.steps : FALLBACK_STEPS);

  const copy = (c: string) => {
    void copyText(c).then((ok) => {
      if (ok) {
        setCopied(c);
        setTimeout(() => setCopied(null), 1500);
      }
    });
  };

  return (
    <main className="block-screen" role="alertdialog" aria-modal="true" aria-labelledby="blk-title" aria-describedby="blk-why">
      <div className="block-card">
        <span className="block-ico" aria-hidden="true">
          <Icon name="lock" size={26} />
        </span>
        <p className="lp-k mono">{backendDown ? 'Backend not running' : 'Database not ready'}</p>
        <h1 id="blk-title">{backendDown ? 'The Quorum backend is not running.' : 'FalkorDB is not set up, so Quorum is locked.'}</h1>
        <p id="blk-why" className="block-why">
          {backendDown
            ? 'The console could not reach the backend. Nothing is shown until it is up, because every belief, handoff and action lives in FalkorDB behind it.'
            : 'Quorum keeps every belief, handoff and action in FalkorDB and will not run, or show anything, without it. Nothing is cached in the browser.'}
        </p>
        <p className="block-reason mono" role="status">
          {reason}
          {url && <> · {url}</>}
        </p>
        <ol className="block-steps">
          {steps.map((s, i) => {
            const c = command(s);
            return (
              <li key={s}>
                <span className="block-n num">{i + 1}</span>
                <div>
                  <p>{s.replace(/:\s.+$/, c ? ':' : '$&')}</p>
                  {c && (
                    <div className="block-cmd">
                      <code className="mono">{c}</code>
                      <button className="btn quiet" onClick={() => copy(c)} aria-label={`Copy command: ${c}`}>
                        <Icon name="copy" size={13} /> {copied === c ? 'Copied' : 'Copy'}
                      </button>
                    </div>
                  )}
                </div>
              </li>
            );
          })}
        </ol>
        <div className="block-actions">
          <button className="btn primary" onClick={onRetry} disabled={checking}>
            <Icon name="refresh" size={14} /> {checking ? 'Checking…' : 'Check again'}
          </button>
          <span className="muted small" aria-live="polite">
            Checking every few seconds. This page opens by itself when it is ready.
          </span>
        </div>
        <p className="block-doc muted small">Full guide: docs/SETUP.md in the repository.</p>
      </div>
    </main>
  );
}
