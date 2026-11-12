import { useEffect, useState } from 'react';
import { landing } from '../copy/landing';
import { prefersReduced } from './hooks';

const KEY = 'quorum:loaded';

export function useLoaderNeeded(): boolean {
  try {
    return !prefersReduced() && !sessionStorage.getItem(KEY);
  } catch {
    return false;
  }
}

/** Percent counter with a hairline progress rule and crop marks, shown once per session. */
export function Loader({ onDone }: { onDone: () => void }) {
  const [pct, setPct] = useState(0);
  const [leaving, setLeaving] = useState(false);

  useEffect(() => {
    const start = performance.now();
    const dur = 1500;
    let raf = 0;
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / dur);
      const eased = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
      setPct(Math.round(eased * 100));
      if (t < 1) raf = requestAnimationFrame(tick);
      else {
        setLeaving(true);
        try {
          sessionStorage.setItem(KEY, '1');
        } catch {
          /* ignore */
        }
        setTimeout(onDone, 520);
      }
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [onDone]);

  return (
    <div className={`lp-loader${leaving ? ' leaving' : ''}`} role="status" aria-live="polite" aria-label={`${landing.loader.label} ${pct} percent`}>
      <i className="crop tl" />
      <i className="crop tr" />
      <i className="crop bl" />
      <i className="crop br" />
      <div className="lp-loader-core">
        <span className="lp-loader-pct" aria-hidden="true">
          {pct}
        </span>
        <span className="lp-loader-rule" aria-hidden="true">
          <b style={{ transform: `scaleX(${pct / 100})` }} />
        </span>
        <span className="lp-loader-label">{pct >= 100 ? landing.loader.done : landing.loader.label}</span>
      </div>
      <span className="lp-loader-brand">QUORUM</span>
    </div>
  );
}
