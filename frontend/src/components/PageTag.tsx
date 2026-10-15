import type { View } from '../state/store';
import { useStore } from '../state/store';
import { viewKey } from '../state/views';

/** The "[06] Context" tag over each page title. The number is the key that opens the screen, so it always matches the nav. */
export function PageTag({ view, children }: { view: View; children: React.ReactNode }) {
  const story = useStore((s) => s.story);
  const key = viewKey(view, story);
  return (
    <p className="page-tag">
      {key !== undefined && <span>[{key.padStart(2, '0')}]</span>} {children}
    </p>
  );
}
