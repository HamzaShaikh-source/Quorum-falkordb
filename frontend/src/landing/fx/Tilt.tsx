import { useRef, type ReactNode } from 'react';
import { fineHover, reducedMotion } from './pointer';

/** Cards lean toward the cursor and catch a moving highlight. Off for touch and reduced motion. */
export function Tilt({ children, max = 7, className }: { children: ReactNode; max?: number; className?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const enabled = () => fineHover() && !reducedMotion();
  const move = (e: React.PointerEvent) => {
    const el = ref.current;
    if (!el || !enabled()) return;
    const r = el.getBoundingClientRect();
    const px = (e.clientX - r.left) / r.width;
    const py = (e.clientY - r.top) / r.height;
    el.style.setProperty('--rx', `${((0.5 - py) * max * 2).toFixed(2)}deg`);
    el.style.setProperty('--ry', `${((px - 0.5) * max * 2).toFixed(2)}deg`);
    el.style.setProperty('--gx', `${(px * 100).toFixed(1)}%`);
    el.style.setProperty('--gy', `${(py * 100).toFixed(1)}%`);
  };
  const leave = () => {
    const el = ref.current;
    if (!el) return;
    el.style.setProperty('--rx', '0deg');
    el.style.setProperty('--ry', '0deg');
  };
  return (
    <div ref={ref} className={`tilt${className ? ` ${className}` : ''}`} onPointerMove={move} onPointerLeave={leave}>
      {children}
      <i className="tilt-glare" aria-hidden="true" />
    </div>
  );
}
