import { reducedMotion } from './pointer';

/**
 * One scroll loop for the whole landing page. Parallax layers register here; the loop only touches layers that are
 * on screen, publishes scroll progress and velocity as CSS variables, and goes to sleep when scrolling stops.
 */
interface Layer {
  outer: HTMLElement;
  inner: HTMLElement;
  speed: number;
  top: number;
  h: number;
  visible: boolean;
}

const layers = new Set<Layer>();
let raf = 0;
let lastY = 0;
let vel = 0;
let io: IntersectionObserver | null = null;
let ro: ResizeObserver | null = null;
let booted = false;

function measure(l: Layer) {
  const r = l.outer.getBoundingClientRect();
  l.top = r.top + window.scrollY;
  l.h = r.height;
}

function tick() {
  raf = 0;
  const y = window.scrollY;
  vel += (y - lastY - vel) * 0.18;
  lastY = y;
  const root = document.documentElement.style;
  const max = Math.max(1, document.documentElement.scrollHeight - window.innerHeight);
  root.setProperty('--sp', String(Math.min(1, Math.max(0, y / max))));
  root.setProperty('--vel', String(Math.max(-60, Math.min(60, vel)).toFixed(2)));
  const vh = window.innerHeight;
  layers.forEach((l) => {
    if (!l.visible) return;
    const centerOffset = l.top + l.h / 2 - y - vh / 2;
    l.inner.style.transform = `translate3d(0, ${(-centerOffset * l.speed).toFixed(1)}px, 0)`;
  });
  if (Math.abs(vel) > 0.05) raf = requestAnimationFrame(tick);
  else document.documentElement.style.setProperty('--vel', '0');
}

function wake() {
  if (!raf) raf = requestAnimationFrame(tick);
}

function boot() {
  if (booted) return;
  booted = true;
  lastY = window.scrollY;
  window.addEventListener('scroll', wake, { passive: true });
  window.addEventListener('resize', () => {
    layers.forEach(measure);
    wake();
  });
  io = new IntersectionObserver(
    (es) => {
      es.forEach((e) => {
        layers.forEach((l) => {
          if (l.outer === e.target) l.visible = e.isIntersecting;
        });
      });
      wake();
    },
    { rootMargin: '200px 0px' },
  );
  ro = new ResizeObserver(() => {
    layers.forEach(measure);
    wake();
  });
  ro.observe(document.body);
  wake();
}

export function registerParallax(outer: HTMLElement, inner: HTMLElement, speed: number): () => void {
  if (reducedMotion()) return () => undefined;
  boot();
  const layer: Layer = { outer, inner, speed, top: 0, h: 0, visible: false };
  measure(layer);
  layers.add(layer);
  io?.observe(outer);
  wake();
  return () => {
    layers.delete(layer);
    io?.unobserve(outer);
    inner.style.transform = '';
  };
}

export function measureAll() {
  layers.forEach(measure);
  wake();
}
