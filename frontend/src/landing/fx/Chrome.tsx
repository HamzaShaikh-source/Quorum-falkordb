import { useEffect, useState } from 'react';
import { fineHover, reducedMotion } from './pointer';

/** Reading-progress bar plus a dot rail for the main sections. Progress comes from the shared --sp variable. */
export function ScrollChrome({ sections }: { sections: [string, string][] }) {
  const [active, setActive] = useState('top');
  useEffect(() => {
    const ids = ['top', ...sections.map(([id]) => id)];
    const io = new IntersectionObserver(
      (es) => {
        es.forEach((e) => {
          if (e.isIntersecting) setActive(e.target.id);
        });
      },
      { rootMargin: '-45% 0px -50% 0px' },
    );
    const seen = new Set<string>();
    const attach = () => {
      ids.forEach((id) => {
        const el = document.getElementById(id);
        if (el && !seen.has(id)) {
          seen.add(id);
          io.observe(el);
        }
      });
    };
    attach();
    const t = setInterval(attach, 800); // lazy sections appear later
    return () => {
      clearInterval(t);
      io.disconnect();
    };
  }, [sections]);
  return (
    <>
      <div className="lp-progress" aria-hidden="true">
        <i />
      </div>
      <nav className="lp-rail" aria-label="Page position">
        {['top', ...sections.map(([id]) => id)].map((id, i) => (
          <button
            key={id}
            className={active === id ? 'on' : ''}
            aria-label={id === 'top' ? 'Top' : (sections.find(([s]) => s === id)?.[1] ?? id)}
            title={id === 'top' ? 'Top' : (sections.find(([s]) => s === id)?.[1] ?? id)}
            aria-current={active === id ? 'true' : undefined}
            onClick={() => document.getElementById(id)?.scrollIntoView({ behavior: reducedMotion() ? 'auto' : 'smooth' })}
          >
            <span className="lp-rail-t mono">{i === 0 ? 'Top' : (sections.find(([s]) => s === id)?.[1] ?? id)}</span>
            <i />
          </button>
        ))}
      </nav>
    </>
  );
}

/** Pills lean toward the cursor. One delegated listener; nothing per button. */
export function useMagnetic() {
  useEffect(() => {
    if (!fineHover() || reducedMotion()) return;
    let cur: HTMLElement | null = null;
    const reset = () => {
      if (cur) {
        cur.style.transform = '';
        cur = null;
      }
    };
    const move = (e: PointerEvent) => {
      const el = (e.target as Element | null)?.closest<HTMLElement>('.lp-pill');
      if (cur && cur !== el) reset();
      if (!el) return;
      cur = el;
      const r = el.getBoundingClientRect();
      const dx = (e.clientX - (r.left + r.width / 2)) / r.width;
      const dy = (e.clientY - (r.top + r.height / 2)) / r.height;
      el.style.transform = `translate(${(dx * 10).toFixed(1)}px, ${(dy * 8).toFixed(1)}px)`;
    };
    window.addEventListener('pointermove', move, { passive: true });
    document.addEventListener('pointerleave', reset);
    return () => {
      window.removeEventListener('pointermove', move);
      document.removeEventListener('pointerleave', reset);
      reset();
    };
  }, []);
}

/** Mounts children when the placeholder nears the viewport, or when asked (nav clicks, idle time). */
export function LazyMount({ children, minHeight, event }: { children: React.ReactNode; minHeight: number; event: string }) {
  const [on, setOn] = useState(typeof IntersectionObserver === 'undefined');
  const [el, setEl] = useState<HTMLDivElement | null>(null);
  useEffect(() => {
    if (on) return;
    const wake = () => setOn(true);
    window.addEventListener(event, wake);
    const idle = setTimeout(wake, 2500);
    let io: IntersectionObserver | undefined;
    if (el) {
      io = new IntersectionObserver((es) => es.some((e) => e.isIntersecting) && wake(), { rootMargin: '900px 0px' });
      io.observe(el);
    }
    return () => {
      window.removeEventListener(event, wake);
      clearTimeout(idle);
      io?.disconnect();
    };
  }, [on, el, event]);
  return on ? <>{children}</> : <div ref={setEl} style={{ minHeight }} aria-hidden="true" />;
}
