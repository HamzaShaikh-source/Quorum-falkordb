import { Icon } from '../components/Icon';
import { copy } from '../copy';
import { tools } from '../copy/tools';
import { defaultFilters, useStore, type GraphFilters } from '../state/store';

export function activeFilterCount(f: GraphFilters): number {
  const d = defaultFilters();
  let n = 0;
  for (const k of Object.keys(f.kinds)) if (f.kinds[k] !== d.kinds[k]) n += 1;
  for (const k of Object.keys(f.statuses)) if (f.statuses[k] !== d.statuses[k]) n += 1;
  if (f.hideHealthy) n += 1;
  if (f.local) n += 1;
  if (f.gods) n += 1;
  return n;
}

const KINDS = ['source', 'fact', 'conclusion', 'action'] as const;
const STATUSES = ['HEALTHY', 'QUARANTINED', 'SUPERSEDED'] as const;

export function FiltersPanel() {
  const f = useStore((s) => s.filters);
  const selected = useStore((s) => s.selectedId);
  const set = useStore((s) => s.set);
  const upd = (p: Partial<GraphFilters>) => set({ filters: { ...f, ...p } });

  return (
    <div className="filters" role="dialog" aria-label={tools.filters.title} data-popover="filters">
      <header>
        <h3>{tools.filters.title}</h3>
        <button className="btn quiet" onClick={() => set({ filters: defaultFilters() })}>
          {tools.filters.reset}
        </button>
      </header>
      <p className="f-label">{tools.filters.kinds}</p>
      <div className="f-chips">
        {KINDS.map((k) => (
          <button key={k} className={`f-chip${f.kinds[k] ? ' on' : ''}`} aria-pressed={!!f.kinds[k]} onClick={() => upd({ kinds: { ...f.kinds, [k]: !f.kinds[k] } })}>
            {copy.kind[k]}
          </button>
        ))}
      </div>
      <p className="f-label">{tools.filters.statuses}</p>
      <div className="f-chips">
        {STATUSES.map((k) => (
          <button key={k} className={`f-chip${f.statuses[k] ? ' on' : ''}`} aria-pressed={!!f.statuses[k]} onClick={() => upd({ statuses: { ...f.statuses, [k]: !f.statuses[k] } })}>
            {copy.status[k]}
          </button>
        ))}
      </div>
      <label className="f-row">
        <input type="checkbox" checked={f.hideHealthy} onChange={(e) => upd({ hideHealthy: e.target.checked })} />
        <span>{tools.filters.hideHealthy}</span>
      </label>
      <label className="f-row">
        <input type="checkbox" checked={f.gods} onChange={(e) => upd({ gods: e.target.checked })} />
        <span>
          <Icon name="star" size={13} /> {tools.filters.gods}
        </span>
      </label>
      <label className={`f-row${selected ? '' : ' off'}`}>
        <input type="checkbox" checked={f.local && !!selected} disabled={!selected} onChange={(e) => upd({ local: e.target.checked })} />
        <span>{selected ? tools.filters.local : `${tools.filters.local}. ${tools.filters.needSelection}.`}</span>
      </label>
      {f.local && selected && (
        <label className="f-range">
          <input type="range" min={1} max={4} value={f.depth} onChange={(e) => upd({ depth: Number(e.target.value) })} aria-label="Local graph depth" />
          <span className="num">{tools.filters.depth(f.depth)}</span>
        </label>
      )}
    </div>
  );
}
