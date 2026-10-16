import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { shallow } from 'zustand/shallow';
import { Icon } from '../components/Icon';
import { copy } from '../copy';
import { getPreview } from '../state/endpoints';
import { useStore, visibleGraph } from '../state/store';
import { EmptyGraphArt } from '../components/charts';
import { goToTime } from '../state/timetravel';
import { GraphAdapter } from './adapter';
import { graphBus } from './bus';
import { COL_X, NODE_W } from './layout';
import { ListView } from './ListView';
import { FiltersPanel, activeFilterCount } from './FiltersPanel';
import { Minimap, type MiniData } from './Minimap';
import { tools } from '../copy/tools';
import { adjacency, godNodes, neighborhood } from '../state/insights';

const KINDS = ['source', 'fact', 'conclusion', 'action'];

export function GraphCanvas({ active }: { active: boolean }) {
  const host = useRef<HTMLDivElement>(null);
  const adapter = useRef<GraphAdapter | null>(null);
  const rails = useRef<(HTMLDivElement | null)[]>([]);
  const [hover, setHover] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState(false);
  const [welcomeGone, setWelcomeGone] = useState(false);
  const [mini, setMini] = useState<MiniData>({ nodes: [], view: { x: 0, y: 0, w: 1, h: 1 } });
  const miniRaf = useRef(0);
  const lastFilterKey = useRef('');
  const loaded = useStore((s) => s.loaded);
  const story = useStore((s) => s.story);
  const counts = useStore((s) => {
    const nodes = visibleGraph(s).nodes;
    return KINDS.map((k) => nodes.filter((n) => n.kind === k).length);
  }, shallow);
  const selectedId = useStore((s) => s.selectedId);
  const search = useStore((s) => s.search);
  const locked = useStore((s) => s.lockLayout);
  const listView = useStore((s) => s.listView);
  const asOf = useStore((s) => s.asOf);
  const set = useStore((s) => s.set);
  const legendOpen = useStore((s) => s.legendOpen);
  const started = useStore((s) => s.eventLog.length > 0);
  const nodeCount = counts.reduce((a, b) => a + b, 0);
  const filters = useStore((s) => s.filters);
  const filtersOpen = useStore((s) => s.filtersOpen);
  const pathIds = useStore((s) => s.pathIds);
  const nodesRec = useStore((s) => s.nodes);
  const edgesRec = useStore((s) => s.edges);
  const past = useStore((s) => s.past);
  const filterCount = activeFilterCount(filters);

  useEffect(() => {
    if (!host.current) return;
    const a = new GraphAdapter(host.current, {
      onSelect: (id) => useStore.getState().set({ selectedId: id }),
      onHover: setHover,
      onContext: (id, x, y) => useStore.getState().set({ ctxMenu: { id, x, y }, selectedId: id }),
      onViewport: (pan, zoom) => {
        if (!miniRaf.current) {
          miniRaf.current = requestAnimationFrame(() => {
            miniRaf.current = 0;
            if (adapter.current) setMini(adapter.current.mini());
          });
        }
        const w = (NODE_W + 56) * zoom;
        rails.current.forEach((el, i) => {
          if (!el) return;
          el.style.width = `${w}px`;
          el.style.transform = `translateX(${pan.x + (COL_X[i] ?? 0) * zoom - w / 2}px)`;
        });
      },
    });
    adapter.current = a;
    (host.current as HTMLElement & { __cy?: unknown }).__cy = a.cy;
    const ro = new ResizeObserver(() => {
      if (host.current && host.current.clientWidth > 0) a.resized();
    });
    ro.observe(host.current);
    const sync = (fit: boolean) => {
      const g = visibleGraph(useStore.getState());
      a.syncAll(g.nodes, g.edges, fit);
    };
    if (useStore.getState().loaded) sync(true);
    const offs = [
      graphBus.full.on(() => sync(true)),
      graphBus.delta.on((d) => {
        const st = useStore.getState();
        a.applyDelta(st.nodes, st.edges, d);
        setTimeout(() => setMini(a.mini()), 60);
      }),
      graphBus.fit.on(() => a.fit(true)),
      graphBus.focus.on((id) => a.focus(id)),
      graphBus.relayout.on(() => {
        const g = visibleGraph(useStore.getState());
        a.relayout(g.nodes, g.edges);
      }),
      graphBus.collapse.on(() => {
        a.toggleCollapse(useStore.getState().selectedId);
        setCollapsed(a.isCollapsed(useStore.getState().selectedId));
      }),
    ];
    return () => {
      ro.disconnect();
      offs.forEach((o) => o());
      a.destroy();
      adapter.current = null;
    };
  }, []);

  useEffect(() => {
    if (loaded && adapter.current) {
      const g = visibleGraph(useStore.getState());
      adapter.current.syncAll(g.nodes, g.edges, true);
    }
  }, [loaded]);

  useEffect(() => {
    adapter.current?.setSelected(selectedId);
    setCollapsed(adapter.current?.isCollapsed(selectedId) ?? false);
  }, [selectedId]);
  useEffect(() => adapter.current?.setSearch(search), [search]);
  useEffect(() => adapter.current?.setLocked(locked), [locked]);
  useEffect(() => {
    if (!active || !adapter.current) return;
    adapter.current.cy.resize();
    adapter.current.fit(false);
  }, [active, listView]);

  useEffect(() => {
    const a = adapter.current;
    if (!a) return;
    const nodes = past ? past.nodes : Object.values(nodesRec);
    const edges = past ? past.edges : Object.values(edgesRec);
    const adj = adjacency(nodes, edges);
    const f = filters;
    const kindOff = Object.keys(f.kinds).filter((k) => !f.kinds[k]);
    const statusOff = Object.keys(f.statuses).filter((k) => !f.statuses[k]);
    const noFilter = !kindOff.length && !statusOff.length && !f.hideHealthy && !(f.local && selectedId);
    if (noFilter) {
      a.setVisible(null);
    } else {
      let ok = new Set(
        nodes
          .filter((n) => f.kinds[n.kind] !== false && f.statuses[n.status] !== false)
          .filter((n) => !f.hideHealthy || n.status !== 'HEALTHY' || n.stale || n.kind === 'action')
          .map((n) => n.id),
      );
      if (f.hideHealthy) {
        const keep = new Set<string>();
        for (const n of nodes) {
          const bad = n.status !== 'HEALTHY' || n.stale;
          if (bad) {
            keep.add(n.id);
            for (const nb of adj.und.get(n.id) ?? []) keep.add(nb);
          }
        }
        ok = new Set([...ok].filter((id) => keep.has(id)));
      }
      if (f.local && selectedId) {
        const near = neighborhood(adj, selectedId, f.depth);
        ok = new Set([...ok].filter((id) => near.has(id)));
        ok.add(selectedId);
      }
      a.setVisible(ok);
    }
    a.setGods(f.gods ? godNodes(nodes, adj, 3).map((g) => g.id) : []);
    const key = JSON.stringify([f.kinds, f.statuses, f.hideHealthy, f.local && selectedId ? [selectedId, f.depth] : null]);
    if (key !== lastFilterKey.current) {
      lastFilterKey.current = key;
      setTimeout(() => a.fit(true), 40);
    }
    setTimeout(() => setMini(a.mini()), 60);
  }, [filters, selectedId, nodesRec, edgesRec, past]);

  useEffect(() => {
    adapter.current?.setPath(pathIds);
  }, [pathIds]);

  const preview = useQuery({
    queryKey: ['preview', hover],
    queryFn: () => getPreview(hover as string),
    enabled: !!hover && !asOf,
    staleTime: 1000,
  });
  useEffect(() => {
    const ids = hover && preview.data && !asOf ? preview.data.node_ids : [];
    adapter.current?.setDry(ids.length > 1 ? ids : []);
    set({ previewIds: ids });
  }, [hover, preview.data, asOf, set]);

  const dry = preview.data;

  // Hover previews linger briefly so they do not flicker as the pointer crosses a gap.
  const [dryShown, setDryShown] = useState<typeof dry>(undefined);
  useEffect(() => {
    if (hover && dry && dry.node_ids.length > 1 && !asOf) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- mirrors a query result so it can linger after hover ends
      setDryShown(dry);
      return;
    }
    const t = setTimeout(() => setDryShown(undefined), 450);
    return () => clearTimeout(t);
  }, [hover, dry, asOf]);

  // The welcome strip goes away on first interaction, not on a timer.
  const hasSelected = useStore((s) => s.navStack.length > 0);
  const showWelcome = story && !started && !welcomeGone && !hasSelected && !asOf && nodeCount > 0;
  const showMini = useStore((s) => s.showMini);

  // One status line at a time, in priority order, so overlays never stack on the graph.
  const status = asOf ? (
    <div className="status-chip past" role="status">
      <Icon name="clock" size={15} />
      <strong>{copy.canvas.pastFrame}</strong>
      <span className="mono">{new Date(asOf).toLocaleTimeString([], { hour12: false })}</span>
      <button className="btn" onClick={() => void goToTime(null)}>
        <Icon name="back" size={14} />
        {copy.canvas.backToNow}
      </button>
    </div>
  ) : pathIds.length > 0 ? (
    <div className="status-chip path" role="status">
      <Icon name="path" size={14} />
      {tools.insights.hops(pathIds.length - 1)}
      <button className="iconbtn" aria-label={tools.insights.clearPath} onClick={() => set({ pathIds: [] })}>
        <Icon name="x" size={13} />
      </button>
    </div>
  ) : dryShown ? (
    <div className="status-chip dry" role="status">
      <strong>{copy.containment.dryRun}</strong>
      <span>
        {copy.containment.dryRunLine(
          dryShown.facts,
          dryShown.conclusions,
          dryShown.actions.staged,
          dryShown.actions.executed_reversible + dryShown.actions.executed_compensable,
          dryShown.actions.executed_irreversible,
        )}
      </span>
    </div>
  ) : showWelcome ? (
    <div className="status-chip hint" role="note">
      <span className="hint-ico">
        <Icon name="sparkle" size={16} />
      </span>
      <p className="hint-text">
        <strong>{copy.welcome.title}.</strong> {copy.welcome.body}
      </p>
      <button className="iconbtn" aria-label={copy.dismiss} onClick={() => setWelcomeGone(true)}>
        <Icon name="x" size={14} />
      </button>
    </div>
  ) : null;

  return (
    <div className={`canvas-wrap${asOf ? ' is-past' : ''}`} hidden={!active}>
      <div className={`canvas-stage${showMini ? ' has-mini' : ''}`}>
        <div className="grid" aria-hidden="true" />
        <div className="rails" aria-hidden="true">
          {copy.rails.map((r, i) => (
            <div key={r} className="rail" ref={(el) => (rails.current[i] = el)}>
              <span className="rail-label">
                {r}
                <b className="num">{counts[i]}</b>
              </span>
            </div>
          ))}
        </div>
        <div ref={host} id="cy" className="cy proof-room-canvas" role="img" aria-label={copy.canvas.graphAria} />

        <div className="overlay">
          <div className="ov-toolbar canvas-tools" role="toolbar" aria-label="Graph tools">
            <label className="search">
              <Icon name="search" size={15} />
              <span className="sr">{copy.canvas.search}</span>
              <input
                id="graph-search"
                type="search"
                placeholder={`${copy.canvas.search}  /`}
                value={search}
                onChange={(e) => set({ search: e.target.value })}
              />
            </label>
            <div className="seg" role="group" aria-label="View">
              <button className={`seg-btn${!listView ? ' on' : ''}`} aria-pressed={!listView} aria-label={copy.canvas.graph} onClick={() => set({ listView: false })}>
                <Icon name="graph" size={14} />
                <span className="seg-label">{copy.canvas.graph}</span>
              </button>
              <button className={`seg-btn${listView ? ' on' : ''}`} aria-pressed={listView} aria-label={copy.canvas.list} onClick={() => set({ listView: true })} title="L">
                <Icon name="list" size={14} />
                <span className="seg-label">{copy.canvas.list}</span>
              </button>
            </div>
            <div className="filters-wrap">
              <button
                className={`btn${filterCount ? ' primary' : ''}`}
                aria-expanded={filtersOpen}
                data-popover-trigger="filters"
                onClick={() => set({ filtersOpen: !filtersOpen })}
              >
                <Icon name="sliders" size={14} />
                {tools.filters.button}
                {filterCount > 0 && <b className="f-count num">{filterCount}</b>}
              </button>
              {filtersOpen && <FiltersPanel />}
            </div>
            <button
              className="iconbtn boxed"
              disabled={!selectedId}
              onClick={() => graphBus.collapse.emit()}
              aria-label={collapsed ? copy.canvas.expand : copy.canvas.collapse}
              title={collapsed ? copy.canvas.expand : copy.canvas.collapse}
            >
              <Icon name="collapse" size={15} />
            </button>
          </div>

          <div className="ov-status">{status}</div>

          <div className="ov-bl">
            {showMini && <Minimap data={mini} selected={selectedId} onPan={(x, y) => adapter.current?.panToModel(x, y)} />}
            <div className={`legend${legendOpen ? ' open' : ''}`}>
              <button className="legend-toggle" aria-expanded={legendOpen} data-popover-trigger="legend" onClick={() => set({ legendOpen: !legendOpen })}>
                <Icon name="help" size={14} />
                {copy.canvas.legendTitle}
              </button>
              {legendOpen && (
                <div className="legend-body" role="note" data-popover="legend">
                  <p>{copy.canvas.legend.shapes}</p>
                  <p>{copy.canvas.legend.state}</p>
                </div>
              )}
            </div>
          </div>

          <div className="ov-br">
            <div className="zoomdock" role="group" aria-label="Zoom">
              <button className="iconbtn" aria-label={copy.canvas.fit} title={`${copy.canvas.fit} (F)`} onClick={() => graphBus.fit.emit()}>
                <Icon name="fit" size={16} />
              </button>
              <button className="iconbtn" aria-label="Zoom in" onClick={() => adapter.current?.zoomBy(1.25)}>
                +
              </button>
              <button className="iconbtn" aria-label="Zoom out" onClick={() => adapter.current?.zoomBy(0.8)}>
                -
              </button>
              <button className="iconbtn" aria-label={copy.canvas.relayout} title={copy.canvas.relayout} onClick={() => graphBus.relayout.emit()}>
                <Icon name="refresh" size={15} />
              </button>
              <button className="iconbtn" aria-pressed={showMini} aria-label={tools.mini.toggle} title={tools.mini.toggle} onClick={() => set({ showMini: !showMini })}>
                <Icon name="map" size={15} />
              </button>
              <button className="iconbtn" aria-pressed={locked} aria-label={locked ? copy.canvas.unlock : copy.canvas.lock} title={locked ? copy.canvas.unlock : copy.canvas.lock} onClick={() => set({ lockLayout: !locked })}>
                <Icon name="lock" size={15} />
              </button>
            </div>
          </div>
        </div>

        {loaded && nodeCount === 0 && (story ? <p className="canvas-empty">{copy.canvas.empty}</p> : (
          <div className="canvas-empty-art">
            <EmptyGraphArt size={190} />
            <b>Nothing in memory yet</b>
            <p>When a connected AI saves a fact, it shows up here as a box, and arrows show what was built from what.</p>
            <button className="btn primary" onClick={() => set({ view: 'connect' })}>Connect an AI</button>
          </div>
        ))}
        {listView && <ListView />}
      </div>
    </div>
  );
}
