import { useEffect, useRef } from 'react';
import { reducedMotion } from './pointer';

interface P {
  x: number;
  y: number;
  vx: number;
  vy: number;
  bad: number;
}
interface Ring {
  x: number;
  y: number;
  t: number;
}

/**
 * Belief-network backdrop for the hero. Facts drift and link up; the cursor parts them like water. A click
 * quarantines whatever is nearby (turns it red) and sends a ring out, then the facts heal. Pauses off screen.
 */
export function NetworkCanvas() {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = ref.current;
    const ctx = canvas?.getContext('2d');
    if (!canvas || !ctx) return;
    const reduce = reducedMotion();
    const css = getComputedStyle(canvas);
    const ink = css.getPropertyValue('--lp-ink').trim() || '#0b1218';
    const mint = '#16a37f';
    const red = '#e0412b';
    let w = 0;
    let h = 0;
    let dpr = 1;
    let pts: P[] = [];
    const rings: Ring[] = [];
    const mouse = { x: -999, y: -999 };
    let raf = 0;
    let visible = true;

    const seed = () => {
      const n = Math.round(Math.min(70, Math.max(26, (w * h) / 22000)));
      pts = Array.from({ length: n }, () => ({
        x: Math.random() * w,
        y: Math.random() * h,
        vx: (Math.random() - 0.5) * 0.28,
        vy: (Math.random() - 0.5) * 0.28,
        bad: 0,
      }));
    };
    const size = () => {
      const r = canvas.getBoundingClientRect();
      dpr = Math.min(2, window.devicePixelRatio || 1);
      w = r.width;
      h = r.height;
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      seed();
      draw();
    };

    const draw = () => {
      ctx.clearRect(0, 0, w, h);
      const L = 128;
      for (let i = 0; i < pts.length; i++) {
        const a = pts[i]!;
        for (let j = i + 1; j < pts.length; j++) {
          const b = pts[j]!;
          const d = Math.hypot(a.x - b.x, a.y - b.y);
          if (d > L) continue;
          const bad = Math.max(a.bad, b.bad);
          ctx.globalAlpha = (1 - d / L) * (0.32 + bad * 0.4);
          ctx.strokeStyle = bad > 0.2 ? red : ink;
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.moveTo(a.x, a.y);
          ctx.lineTo(b.x, b.y);
          ctx.stroke();
        }
      }
      for (const p of pts) {
        ctx.globalAlpha = 0.9;
        ctx.fillStyle = p.bad > 0.2 ? red : mint;
        ctx.strokeStyle = ink;
        ctx.lineWidth = 1.2;
        const s = 3 + p.bad * 2.5;
        ctx.beginPath();
        ctx.rect(p.x - s, p.y - s, s * 2, s * 2);
        ctx.fill();
        ctx.stroke();
      }
      for (const r of rings) {
        ctx.globalAlpha = Math.max(0, 1 - r.t) * 0.7;
        ctx.strokeStyle = red;
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(r.x, r.y, r.t * 220, 0, Math.PI * 2);
        ctx.stroke();
      }
      ctx.globalAlpha = 1;
    };

    const step = () => {
      raf = 0;
      for (const p of pts) {
        const dx = p.x - mouse.x;
        const dy = p.y - mouse.y;
        const d = Math.hypot(dx, dy);
        if (d < 140 && d > 0.1) {
          const f = (1 - d / 140) * 0.9;
          p.vx += (dx / d) * f * 0.35;
          p.vy += (dy / d) * f * 0.35;
        }
        p.vx *= 0.985;
        p.vy *= 0.985;
        p.x += p.vx;
        p.y += p.vy;
        if (p.x < -10) p.x = w + 10;
        if (p.x > w + 10) p.x = -10;
        if (p.y < -10) p.y = h + 10;
        if (p.y > h + 10) p.y = -10;
        p.bad = Math.max(0, p.bad - 0.006);
      }
      for (let i = rings.length - 1; i >= 0; i--) {
        rings[i]!.t += 0.018;
        if (rings[i]!.t >= 1) rings.splice(i, 1);
      }
      draw();
      if (visible) raf = requestAnimationFrame(step);
    };

    const onMove = (e: PointerEvent) => {
      const r = canvas.getBoundingClientRect();
      mouse.x = e.clientX - r.left;
      mouse.y = e.clientY - r.top;
    };
    const onLeave = () => {
      mouse.x = mouse.y = -999;
    };
    const onDown = (e: PointerEvent) => {
      const r = canvas.getBoundingClientRect();
      const x = e.clientX - r.left;
      const y = e.clientY - r.top;
      if (x < 0 || y < 0 || x > w || y > h) return;
      if ((e.target as Element).closest('a,button,.lp-window,input')) return;
      rings.push({ x, y, t: 0 });
      for (const p of pts) if (Math.hypot(p.x - x, p.y - y) < 120) p.bad = 1;
      if (!raf && !reduce) raf = requestAnimationFrame(step);
    };

    size();
    const ro = new ResizeObserver(size);
    ro.observe(canvas);
    const io = new IntersectionObserver(([e]) => {
      visible = !!e?.isIntersecting && !document.hidden;
      if (visible && !raf && !reduce) raf = requestAnimationFrame(step);
    });
    io.observe(canvas);
    const vis = () => {
      visible = !document.hidden;
      if (visible && !raf && !reduce) raf = requestAnimationFrame(step);
    };
    document.addEventListener('visibilitychange', vis);
    window.addEventListener('pointermove', onMove, { passive: true });
    window.addEventListener('pointerdown', onDown);
    canvas.addEventListener('pointerleave', onLeave);
    if (!reduce) raf = requestAnimationFrame(step);
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      io.disconnect();
      document.removeEventListener('visibilitychange', vis);
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerdown', onDown);
      canvas.removeEventListener('pointerleave', onLeave);
    };
  }, []);

  return <canvas ref={ref} className="lp-net" aria-hidden="true" />;
}
