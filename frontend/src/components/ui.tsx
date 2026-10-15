import { useEffect, useRef, useState, type ReactNode } from 'react';
import { ApiError } from '../api/client';
import { copy } from '../copy';
import type { Tone } from '../state/reducer';

export function Chip({ tone = 'neutral', children, title }: { tone?: Tone | 'ultra'; children: ReactNode; title?: string }) {
  return (
    <span className={`chip chip-${tone}`} title={title}>
      {children}
    </span>
  );
}

export function Skeleton({ lines = 3 }: { lines?: number }) {
  return (
    <div className="skeleton" role="status" aria-label={copy.loading}>
      {Array.from({ length: lines }, (_, i) => (
        <span key={i} style={{ width: `${88 - ((i * 17) % 40)}%` }} />
      ))}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="empty">{children}</p>;
}

export function errorText(e: unknown): { endpoint: string; message: string } {
  if (e instanceof ApiError) {
    const endpoint = e.endpoint;
    switch (e.code) {
      case 'UNREACHABLE':
        return { endpoint, message: copy.errors.unreachable };
      case 'SCHEMA':
        return { endpoint, message: copy.errors.schema(endpoint, e.field ?? '') };
      case 'UNAUTHENTICATED':
        return { endpoint, message: copy.errors.unauthenticated };
      case 'FORBIDDEN_TENANT':
        return { endpoint, message: copy.errors.forbiddenTenant };
      case 'FORBIDDEN_ROLE':
        return { endpoint, message: copy.errors.forbiddenRole };
      default:
        return { endpoint, message: copy.errors.generic(e.code, e.message) };
    }
  }
  return { endpoint: 'request', message: copy.errors.unreachable };
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const { endpoint, message } = errorText(error);
  return (
    <div className="errorbox" role="alert">
      <p>{copy.errors.panel(endpoint)}</p>
      <p className="muted">{message}</p>
      {onRetry && (
        <button className="btn" onClick={onRetry}>
          {copy.retry}
        </button>
      )}
    </div>
  );
}

export function useCountUp(target: number, ms = 700): number {
  const reduce = typeof matchMedia !== 'undefined' && matchMedia('(prefers-reduced-motion: reduce)').matches;
  const [v, setV] = useState(0);
  const from = useRef(0);
  useEffect(() => {
    if (reduce) return;
    const start = performance.now();
    const a = from.current;
    let raf = 0;
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / ms);
      const val = a + (target - a) * (1 - Math.pow(1 - t, 3));
      setV(val);
      from.current = val;
      if (t < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, ms, reduce]);
  return reduce ? Math.round(target) : Math.round(v);
}

export function fmtTime(ts: number): string {
  return new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
}

export function fmtDateTime(ts: number): string {
  return new Date(ts).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false });
}
