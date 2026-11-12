import { copy } from '../copy';
import { Icon } from '../components/Icon';
import { Chip } from '../components/ui';
import { actionDisplayState, actionTone } from '../state/actionState';
import { useStore, visibleGraph } from '../state/store';

export function ListView() {
  const nodes = useStore((s) => visibleGraph(s).nodes);
  const selected = useStore((s) => s.selectedId);
  const set = useStore((s) => s.set);
  const order = ['source', 'fact', 'conclusion', 'action'];
  const sorted = [...nodes].sort((a, b) => order.indexOf(a.kind) - order.indexOf(b.kind));

  return (
    <div className="listview" role="region" aria-label={copy.canvas.list}>
      {sorted.length === 0 && <p className="empty">{copy.canvas.empty}</p>}
      <ul>
        {sorted.map((n) => {
          const ds = n.kind === 'action' ? actionDisplayState(n) : null;
          return (
            <li key={n.id}>
              <button
                className={`list-row${selected === n.id ? ' selected' : ''}`}
                aria-pressed={selected === n.id}
                onClick={() => set({ selectedId: n.id })}
              >
                <span className="list-kind">
                  <Icon name={n.is_decision ? 'flag' : n.kind === 'source' ? 'doc' : n.kind === 'fact' ? 'fact' : n.kind === 'conclusion' ? 'conclusion' : 'bolt'} size={14} />
                  {n.is_decision ? copy.kind.decision : (copy.kind[n.kind] ?? n.kind)}
                </span>
                <span className="list-label">{n.label}</span>
                <Chip tone={n.status === 'QUARANTINED' ? 'bad' : n.status === 'HEALTHY' ? 'good' : 'neutral'}>
                  {copy.status[n.status] ?? n.status}
                </Chip>
                {n.stale && n.status === 'HEALTHY' && <Chip tone="warn">{copy.status.stale}</Chip>}
                {ds && <Chip tone={actionTone(ds)}>{copy.actionState[ds]}</Chip>}
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
