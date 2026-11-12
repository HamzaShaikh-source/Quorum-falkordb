let remembered: HTMLElement | null = null;

/** Call before opening a dialog, so focus can go back where it was. */
export function rememberFocus() {
  const a = document.activeElement;
  remembered = a instanceof HTMLElement && a !== document.body ? a : null;
}

export function restoreFocus() {
  const el = remembered;
  remembered = null;
  if (el && document.contains(el)) el.focus({ preventScroll: true });
}

export function focusTrigger(name: string) {
  document.querySelector<HTMLElement>(`[data-popover-trigger="${name}"]`)?.focus({ preventScroll: true });
}
