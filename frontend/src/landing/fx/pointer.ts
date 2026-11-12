/** One shared pointer tracker for every cursor-driven effect on the landing page. */
export const pointer = { x: -9999, y: -9999, down: false, seen: false, over: null as Element | null };

const subs = new Set<() => void>();
let started = false;

export const reducedMotion = () => typeof matchMedia !== 'undefined' && matchMedia('(prefers-reduced-motion: reduce)').matches;
export const fineHover = () => typeof matchMedia !== 'undefined' && matchMedia('(hover: hover) and (pointer: fine)').matches;

const CURSOR_KEY = 'quorum:cursor';

/** The visitor's choice to keep the normal cursor. Stored in this browser only; every storage call is guarded. */
export function cursorDisabled(): boolean {
  try {
    return localStorage.getItem(CURSOR_KEY) === 'off';
  } catch {
    return false;
  }
}

export function setCursorDisabled(off: boolean): void {
  try {
    if (off) localStorage.setItem(CURSOR_KEY, 'off');
    else localStorage.removeItem(CURSOR_KEY);
  } catch {
    /* the choice then lasts until reload */
  }
  document.documentElement.classList.toggle('cr-disabled', off);
}

export function startPointer() {
  if (started || typeof window === 'undefined') return;
  started = true;
  window.addEventListener(
    'pointermove',
    (e) => {
      pointer.x = e.clientX;
      pointer.y = e.clientY;
      pointer.seen = true;
      pointer.over = e.target instanceof Element ? e.target : null;
      subs.forEach((f) => f());
    },
    { passive: true },
  );
  window.addEventListener('pointerdown', () => {
    pointer.down = true;
    subs.forEach((f) => f());
  });
  window.addEventListener('pointerup', () => {
    pointer.down = false;
    subs.forEach((f) => f());
  });
  document.addEventListener('pointerleave', () => {
    pointer.seen = false;
    subs.forEach((f) => f());
  });
}

/** Content can move under a still pointer (scrolling, reveals, layout). Re-ask the page what is there. */
export function refreshOver(): void {
  if (!pointer.seen || typeof document === 'undefined') return;
  pointer.over = document.elementFromPoint(pointer.x, pointer.y);
}

export function onPointer(f: () => void): () => void {
  subs.add(f);
  return () => {
    subs.delete(f);
  };
}
