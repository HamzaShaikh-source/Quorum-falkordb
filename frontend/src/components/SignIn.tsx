import { useId, useRef, useState } from 'react';
import { signIn } from '../api/client';
import { Icon } from './Icon';

/**
 * Shown when the hub is up but this browser has no way to call it: no demo tokens (production rules) and no token signed
 * in for this tab. The token stays in this tab's sessionStorage and is sent only to this hub.
 */
export function SignIn({ onSignedIn, expired = false, devHub = false }: { onSignedIn: () => void; expired?: boolean; devHub?: boolean }) {
  const [token, setToken] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputId = useId();
  const errId = useId();
  const input = useRef<HTMLInputElement>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const r = await signIn(token);
    setBusy(false);
    if (r.ok) {
      setToken('');
      onSignedIn();
    } else {
      setError(r.message);
      input.current?.focus();
    }
  };

  return (
    <main className="block-screen" aria-labelledby="si-title">
      <form className="block-card signin" onSubmit={(e) => void submit(e)}>
        <span className="block-ico signin-ico" aria-hidden="true">
          <Icon name="lock" size={26} />
        </span>
        <p className="lp-k mono">{expired ? 'Signed out' : 'Sign in'}</p>
        <h1 id="si-title">{expired ? 'Your token was refused, so you are signed out.' : 'Paste your Quorum token to open the console.'}</h1>
        <p className="block-why">
          {expired ? 'It may have expired or been revoked. Ask whoever runs this hub for a new one, or rotate it on the Team page.' : 'This hub runs with production rules, so there are no built-in demo tokens. Use a human token to manage teams and review handoffs.'}
        </p>
        <label className="field" htmlFor={inputId}>
          <span>Token</span>
          <input
            id={inputId}
            ref={input}
            type="password"
            autoComplete="off"
            spellCheck={false}
            autoFocus
            value={token}
            onChange={(e) => setToken(e.target.value)}
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? errId : undefined}
            placeholder="qtk_… or qrm_…"
          />
        </label>
        {error && (
          <p id={errId} className="bad-text" role="alert">
            {error}
          </p>
        )}
        <div className="block-actions">
          <button className="btn primary" type="submit" disabled={busy || !token.trim()}>
            {busy ? 'Checking…' : 'Sign in'}
          </button>
          <span className="muted small">Kept in this tab only, and sent only to this hub.</span>
        </div>
        {devHub && (
          <p className="block-doc small" role="note">
            This is a local development hub. Its built-in admin token is <code className="mono">token_human</code>; start the stack with <code className="mono">QUORUM_DEMO=1</code> to be signed in automatically with sandbox credentials.
          </p>
        )}
        <p className="block-doc muted small">
          Operators: <code className="mono">python scripts/make_token.py --tenant acme --role human</code> makes one. See docs/SETUP.md.
        </p>
      </form>
    </main>
  );
}
