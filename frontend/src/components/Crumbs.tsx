import { copy } from '../copy';
import { tools } from '../copy/tools';
import { graphBus } from '../graph/bus';
import { navMove, useStore } from '../state/store';
import { Icon } from './Icon';

/** Back and forward through selections, plus where the selected node sits in the causal chain. */
export function Crumbs() {
  const idx = useStore((s) => s.navIdx);
  const stack = useStore((s) => s.navStack);
  const selected = useStore((s) => s.selectedId);
  const nodes = useStore((s) => s.nodes);
  const set = useStore((s) => s.set);
  const node = selected ? nodes[selected] : undefined;
  const go = (d: -1 | 1) => {
    const id = navMove(d);
    if (id) graphBus.focus.emit(id);
  };
  return (
    <div className="crumbs">
      <button className="iconbtn" disabled={idx <= 0} onClick={() => go(-1)} aria-label={tools.crumbs.back} title={`${tools.crumbs.back} (Alt+Left)`}>
        <Icon name="back" size={16} />
      </button>
      <button className="iconbtn" disabled={idx >= stack.length - 1} onClick={() => go(1)} aria-label={tools.crumbs.forward} title={`${tools.crumbs.forward} (Alt+Right)`}>
        <Icon name="forward" size={16} />
      </button>
      <nav className="crumb-trail" aria-label="Location">
        <button onClick={() => set({ selectedId: null })}>{copy.nav.console}</button>
        <span aria-hidden="true">/</span>
        <span>{copy.tabs.graph}</span>
        {node ? (
          <>
            <span aria-hidden="true">/</span>
            <span className="crumb-kind">{node.is_decision ? copy.kind.decision : (copy.kind[node.kind] ?? node.kind)}</span>
            <strong title={node.label}>{node.label}</strong>
          </>
        ) : (
          <em>{tools.crumbs.none}</em>
        )}
      </nav>
    </div>
  );
}
