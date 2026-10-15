import { useEffect, useRef, useState } from 'react';

export const prefersReduced = () => typeof matchMedia !== 'undefined' && matchMedia('(prefers-reduced-motion: reduce)').matches;

export function useRoute(): 'landing' | 'app' {
  const [hash, setHash] = useState(typeof location !== 'undefined' ? location.hash : '');
  useEffect(() => {
    const on = () => setHash(location.hash);
    window.addEventListener('hashchange', on);
    return () => window.removeEventListener('hashchange', on);
  }, []);
  return hash.startsWith('#/app') ? 'app' : 'landing';
}

export function go(to: 'landing' | 'app', autoplay = false) {
  try {
    if (autoplay) sessionStorage.setItem('quorum:autoplay', '1');
  } catch {
    /* storage can be unavailable */
  }
  location.hash = to === 'app' ? '#/app' : '#/';
  if (to === 'landing') window.scrollTo({ top: 0 });
}

export function useInView<T extends HTMLElement>(threshold = 0.25): [React.RefObject<T>, boolean] {
  const ref = useRef<T>(null);
  const [seen, setSeen] = useState(() => typeof IntersectionObserver === 'undefined' || prefersReduced());
  useEffect(() => {
    const el = ref.current;
    if (!el || seen) return;
    const io = new IntersectionObserver(
      (es) => {
        if (es.some((e) => e.isIntersecting)) {
          setSeen(true);
          io.disconnect();
        }
      },
      { threshold },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [threshold, seen]);
  return [ref, seen];
}

export function useCounter(target: number, run: boolean, decimals = 0, ms = 1400): string {
  const [v, setV] = useState(0);
  const reduce = prefersReduced();
  useEffect(() => {
    if (!run || reduce) return;
    const start = performance.now();
    let raf = 0;
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / ms);
      setV(target * (1 - Math.pow(1 - t, 3)));
      if (t < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, run, ms, reduce]);
  return (reduce && run ? target : v).toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

export function scrollToId(id: string) {
  document.getElementById(id)?.scrollIntoView({ behavior: prefersReduced() ? 'auto' : 'smooth', block: 'start' });
}
