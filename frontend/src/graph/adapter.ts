import cytoscape, { type Core, type EdgeSingular, type NodeSingular } from 'cytoscape';
import type { GraphEdge, GraphNode } from '../api/schemas';
import { copy } from '../copy';
import { iconPath } from '../components/Icon';
import { actionDisplayState } from '../state/actionState';
import { colOf, COL_X, freeSlot, GAP_Y, layoutAll, NODE_H, NODE_W } from './layout';
import { P } from './palette';

export interface AdapterHandlers {
  onSelect: (id: string | null) => void;
  onHover: (id: string | null) => void;
  onViewport: (pan: { x: number; y: number }, zoom: number) => void;
  onContext: (id: string, x: number, y: number) => void;
}

const reduced = () => typeof matchMedia !== 'undefined' && matchMedia('(prefers-reduced-motion: reduce)').matches;
const uri = (svg: string) => `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`;
export function wrapNodeLabel(str: string, maxLineLen = 14, maxLines = 3): string {
  if (!str) return '';
  const words = str.trim().split(/\s+/);
  const lines: string[] = [];
  let currentLine = '';

  const pushChunk = (chunk: string) => {
    if (!currentLine) {
      currentLine = chunk;
    } else if (currentLine.length + 1 + chunk.length <= maxLineLen) {
      currentLine += ' ' + chunk;
    } else {
      lines.push(currentLine);
      currentLine = chunk;
    }
  };

  for (const word of words) {
    if (word.length <= maxLineLen) {
      pushChunk(word);
    } else {
      let remaining = word;
      while (remaining.length > maxLineLen) {
        let splitIdx = -1;
        for (let i = maxLineLen; i >= 4; i--) {
          const ch = remaining.charAt(i - 1);
          if (ch && '_.-/@:()[]'.includes(ch)) {
            splitIdx = i;
            break;
          }
        }
        if (splitIdx === -1) {
          splitIdx = maxLineLen - 1;
          pushChunk(remaining.slice(0, splitIdx) + '-');
          remaining = remaining.slice(splitIdx);
        } else {
          pushChunk(remaining.slice(0, splitIdx));
          remaining = remaining.slice(splitIdx);
        }
      }
      if (remaining) {
        pushChunk(remaining);
      }
    }
  }
  if (currentLine) {
    lines.push(currentLine);
  }

  if (lines.length > maxLines) {
    const kept = lines.slice(0, maxLines);
    let last = kept[maxLines - 1] ?? '';
    if (last.length > maxLineLen - 3) {
      last = last.slice(0, Math.max(1, maxLineLen - 3)).trimEnd() + '...';
    } else {
      last = last.trimEnd() + '...';
    }
    kept[maxLines - 1] = last;
    return kept.join('\n');
  }
  return lines.join('\n');
}

function nodeImage(n: GraphNode): string {
  const tone =
    n.status === 'QUARANTINED' ? P.bad : n.status === 'SUPERSEDED' ? P.ghost : n.stale ? P.warn : n.kind === 'source' ? P.text2 : P.ok;
  const key = n.kind === 'conclusion' && n.is_decision ? 'decision' : n.kind;
  const path = iconPath[key] ?? iconPath.fact ?? '';
  const icon = `<g transform="translate(11 ${NODE_H / 2 - 9}) scale(0.75)" fill="none" stroke="${tone}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="${path}"/></g>`;
  let badge = '';
  const a = n.action;
  if (a?.gate === 'BLOCKED' && !a.executed) {
    badge = `<g transform="translate(${NODE_W - 20} 6)" fill="none" stroke="${P.person}" stroke-width="2" stroke-linecap="round"><rect x="1" y="6" width="10" height="7" rx="1.5" fill="${P.person}" fill-opacity="0.25"/><path d="M3 6V4a3 3 0 0 1 6 0v2"/></g>`;
  } else if (a?.confirmed) {
    badge = `<g transform="translate(${NODE_W - 20} 6)" fill="none" stroke="${n.status === 'QUARANTINED' ? P.bad : P.ok}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="m1 7 3.5 3.5L11 3"/></g>`;
  }
  return uri(`<svg xmlns="http://www.w3.org/2000/svg" width="${NODE_W}" height="${NODE_H}" viewBox="0 0 ${NODE_W} ${NODE_H}">${icon}${badge}</svg>`);
}

function toData(n: GraphNode): cytoscape.NodeDataDefinition {
  const isAction = n.kind === 'action';
  const display = isAction ? copy.actionState[actionDisplayState(n)] : '';
  const labelText = isAction ? `${wrapNodeLabel(n.label, 14, 1)}\n${display}` : wrapNodeLabel(n.label, 14, 3);
  return {
    id: n.id,
    label: n.label,
    label2: labelText,
    kind: n.kind,
    status: n.status,
    stale: n.stale && n.status === 'HEALTHY' ? 1 : 0,
    decision: n.is_decision ? 1 : 0,
    img: nodeImage(n),
  };
}

const style = [
  {
    selector: 'node',
    style: {
      shape: 'round-rectangle',
      width: NODE_W,
      height: NODE_H,
      'corner-radius': 10,
      'font-family': '"Schibsted Grotesk", system-ui, sans-serif',
      'font-size': 12,
      'font-weight': 500,
      color: P.text,
      label: 'data(label2)',
      'text-valign': 'center',
      'text-halign': 'center',
      'text-margin-x': 14,
      'text-wrap': 'wrap',
      'text-max-width': `${NODE_W - 46}px`,
      'line-height': 1.18,
      'min-zoomed-font-size': 6,
      'background-color': P.bg3,
      'border-width': 2,
      'border-color': P.nodeBorder,
      'background-image': 'data(img)',
      'background-fit': 'none',
      'background-width': '100%',
      'background-height': '100%',
      'background-clip': 'none',
      'overlay-opacity': 0,
      'overlay-padding': 6,
      'transition-property': 'opacity',
      'transition-duration': 160,
    },
  },
  { selector: 'node[kind="source"]', style: { shape: 'cut-rectangle', 'cut-rectangle-corner-radius': 9, 'background-color': P.bg2 } },
  { selector: 'node[kind="fact"]', style: { 'corner-radius': 26 } },
  { selector: 'node[kind="conclusion"]', style: { 'corner-radius': 8 } },
  {
    selector: 'node[kind="conclusion"][decision > 0]',
    style: { shape: 'rectangle', 'border-style': 'double', 'border-width': 5 },
  },
  { selector: 'node[kind="action"]', style: { 'corner-radius': 4 } },
  { selector: 'node[status="HEALTHY"]', style: { 'border-color': P.ink, 'background-color': P.okFill } },
  { selector: 'node[kind="source"][status="HEALTHY"]', style: { 'border-color': P.ink, 'background-color': P.bg2 } },
  {
    selector: 'node[status="QUARANTINED"]',
    style: { 'border-color': P.bad, 'border-style': 'dashed', 'background-color': P.badFill, color: P.badText },
  },
  {
    selector: 'node[status="SUPERSEDED"]',
    style: { 'border-color': P.ghost, 'background-color': P.ghostFill, color: P.text2, opacity: 0.62 },
  },
  { selector: 'node[status="UNKNOWN"]', style: { 'border-color': P.ghost, 'border-style': 'dotted' } },
  { selector: 'node[stale > 0]', style: { 'border-color': P.warn, 'border-style': 'dotted', 'border-width': 2.5 } },
  { selector: 'node:selected', style: { 'overlay-color': '#0b1218', 'overlay-opacity': 0.09, 'overlay-padding': 8 } },
  { selector: 'node.dry', style: { 'overlay-color': P.bad, 'overlay-opacity': 0.22, 'overlay-padding': 9 } },
  { selector: 'node.pulse', style: { 'overlay-color': P.bad, 'overlay-opacity': 0.26, 'overlay-padding': 10 } },
  { selector: '.faded', style: { opacity: 0.14 } },
  { selector: '.hidden', style: { display: 'none' } },
  { selector: '.filtered', style: { display: 'none' } },
  { selector: 'node.god', style: { 'overlay-color': '#f2b705', 'overlay-opacity': 0.4, 'overlay-padding': 7 } },
  { selector: 'node.onpath', style: { 'overlay-color': P.person, 'overlay-opacity': 0.28, 'overlay-padding': 9 } },
  { selector: 'edge.onpath', style: { 'line-color': P.person, 'target-arrow-color': P.person, width: 3.4, 'line-style': 'solid' } },
  { selector: '.pathfade', style: { opacity: 0.16 } },
  {
    selector: 'edge',
    style: {
      width: 1.3,
      'line-color': P.edge,
      'target-arrow-color': P.edge,
      'target-arrow-shape': 'triangle',
      'arrow-scale': 0.85,
      'curve-style': 'taxi',
      'taxi-direction': 'rightward',
      'taxi-turn': '50%',
      'taxi-radius': 14,
    },
  },
  { selector: 'edge[type="SUPPORTS"]', style: { 'line-style': 'dashed', 'line-dash-pattern': [6, 4] } },
  { selector: 'edge[type="FROM_SOURCE"]', style: { width: 1, 'target-arrow-shape': 'none', 'line-color': 'rgba(11,18,24,0.28)' } },
  {
    selector: 'edge[type="CONTRADICTS"]',
    style: {
      'line-color': P.bad,
      'target-arrow-color': P.bad,
      'target-arrow-shape': 'tee',
      width: 2.2,
      'curve-style': 'unbundled-bezier',
      'control-point-distances': [-60],
      'control-point-weights': [0.5],
    },
  },
  {
    selector: 'edge[type="SUPERSEDES"]',
    style: {
      'line-color': P.ghost,
      'target-arrow-color': P.ghost,
      'line-style': 'dashed',
      'line-dash-pattern': [14, 7],
      'curve-style': 'unbundled-bezier',
      'control-point-distances': [60],
      'control-point-weights': [0.5],
    },
  },
  {
    selector: 'edge[tq > 0][type!="CONTRADICTS"]',
    style: { 'line-color': P.bad, 'target-arrow-color': P.bad, 'line-style': 'dashed', 'line-dash-pattern': [5, 4] },
  },
  { selector: 'edge.dry', style: { 'line-color': P.bad, 'target-arrow-color': P.bad, width: 2.4 } },
] as unknown as cytoscape.StylesheetJson;

export class GraphAdapter {
  readonly cy: Core;
  private timers = new Set<ReturnType<typeof setTimeout>>();
  private collapsedRoots = new Set<string>();
  private fresh = new Set<string>();
  private search = '';
  private autoFit = true;
  private fitTimer: ReturnType<typeof setTimeout> | null = null;
  private containerEl?: HTMLElement;
  private manualHandler?: () => void;

  constructor(
    container: HTMLElement,
    private h: AdapterHandlers,
  ) {
    this.containerEl = container;
    this.cy = cytoscape({
      container,
      style,
      elements: [],
      minZoom: 0.25,
      maxZoom: 2.5,
      boxSelectionEnabled: false,
    });
    this.cy.on('tap', 'node', (e) => h.onSelect((e.target as NodeSingular).id()));
    this.cy.on('tap', (e) => {
      if (e.target === this.cy) h.onSelect(null);
    });
    this.cy.on('mouseover', 'node', (e) => {
      container.style.cursor = 'pointer';
      h.onHover((e.target as NodeSingular).id());
    });
    this.cy.on('mouseout', 'node', () => {
      container.style.cursor = '';
      h.onHover(null);
    });
    this.cy.on('cxttap', 'node', (e) => {
      const r = container.getBoundingClientRect();
      const p = (e.target as NodeSingular).renderedPosition();
      h.onContext((e.target as NodeSingular).id(), r.left + p.x, r.top + p.y);
    });
    this.cy.on('viewport', () => h.onViewport(this.cy.pan(), this.cy.zoom()));
    const manual = () => {
      this.autoFit = false;
    };
    this.manualHandler = manual;
    container.addEventListener('wheel', manual, { passive: true });
    container.addEventListener('pointerdown', manual);
    h.onViewport(this.cy.pan(), this.cy.zoom());
  }

  destroy() {
    if (this.containerEl && this.manualHandler) {
      this.containerEl.removeEventListener('wheel', this.manualHandler);
      this.containerEl.removeEventListener('pointerdown', this.manualHandler);
    }
    if (this.fitTimer) clearTimeout(this.fitTimer);
    if (this.resizeRaf) cancelAnimationFrame(this.resizeRaf);
    this.timers.forEach(clearTimeout);
    this.timers.clear();
    this.cy.destroy();
  }

  private later(ms: number, fn: () => void) {
    const t = setTimeout(() => {
      this.timers.delete(t);
      fn();
    }, ms);
    this.timers.add(t);
  }

  syncAll(nodes: GraphNode[], edges: GraphEdge[], fit = true) {
    this.timers.forEach(clearTimeout);
    this.timers.clear();
    this.fresh.clear();
    const pos = layoutAll(nodes, edges);
    const sel = this.cy.$(':selected').map((n) => n.id());
    const statusMap = new Map<string, string>();
    for (const n of nodes) statusMap.set(n.id, n.status);
    this.cy.batch(() => {
      this.cy.elements().remove();
      this.cy.add(nodes.map((n) => ({ group: 'nodes' as const, data: toData(n), position: pos[n.id] ?? { x: 0, y: 0 } })));
      const ids = new Set(nodes.map((n) => n.id));
      this.cy.add(
        edges
          .filter((e) => ids.has(e.source) && ids.has(e.target))
          .map((e) => ({ group: 'edges' as const, data: this.edgeData(e, undefined, statusMap) })),
      );
    });
    sel.forEach((id) => this.cy.getElementById(id).select());
    this.applyCollapse();
    this.applySearch();
    if (fit) this.fit(false);
  }

  private edgeData(e: GraphEdge, nodes?: GraphNode[], statusMap?: Map<string, string>) {
    const status = statusMap ? statusMap.get(e.target) : (nodes ? nodes.find((n) => n.id === e.target)?.status : (this.cy.getElementById(e.target).data('status') as string | undefined));
    return { id: e.id, source: e.source, target: e.target, type: e.type, tq: status === 'QUARANTINED' ? 1 : 0 };
  }

  private columnYs(col: number, except?: string): number[] {
    return this.cy
      .nodes()
      .filter((n: NodeSingular) => n.id() !== except && COL_X.indexOf(Math.round(n.position('x'))) === col)
      .map((n: NodeSingular) => n.position('y'));
  }

  /** Adds new elements and updates changed ones. Existing positions never move. */
  applyDelta(
    nodes: Record<string, GraphNode>,
    edges: Record<string, GraphEdge>,
    d: { ids: string[]; edgeIds: string[]; delays: Record<string, number> },
  ) {
    for (const id of d.ids) {
      const n = nodes[id];
      if (!n) continue;
      const existing = this.cy.getElementById(id);
      if (existing.empty()) {
        const col = colOf(n);
        const ys = this.columnYs(col);
        const want = ys.length ? Math.max(...ys) + GAP_Y : 0;
        this.cy.add({ group: 'nodes', data: toData(n), position: { x: COL_X[col] ?? 0, y: want } });
        this.fresh.add(id);
        this.applySearch();
        this.scheduleFit();
      } else {
        const delay = d.delays[id] ?? 0;
        if (delay > 0) this.later(delay, () => this.updateNode(n, true));
        else this.updateNode(n, id in d.delays);
      }
    }
    for (const eid of d.edgeIds) {
      const e = edges[eid];
      if (!e || this.cy.getElementById(eid).nonempty()) continue;
      if (this.cy.getElementById(e.source).empty() || this.cy.getElementById(e.target).empty()) continue;
      this.cy.add({ group: 'edges', data: this.edgeData(e) });
      this.adopt(e);
    }
  }

  /** A freshly added node moves once, next to its first parent, then stays put. */
  private adopt(e: GraphEdge) {
    if (e.type === 'CONTRADICTS' || e.type === 'SUPERSEDES') return;
    const child = this.cy.getElementById(e.target);
    if (!this.fresh.has(e.target) || child.empty()) return;
    const parent = this.cy.getElementById(e.source);
    if (parent.empty()) return;
    const col = COL_X.indexOf(Math.round(child.position('x')));
    const y = freeSlot(this.columnYs(col, e.target), parent.position('y'));
    child.position({ x: child.position('x'), y });
    this.fresh.delete(e.target);
  }

  private updateNode(n: GraphNode, pulse: boolean) {
    const el = this.cy.getElementById(n.id);
    if (el.empty()) return;
    el.data(toData(n));
    el.connectedEdges().forEach((edge: EdgeSingular) => {
      const t = this.cy.getElementById(edge.data('target') as string);
      edge.data('tq', t.data('status') === 'QUARANTINED' ? 1 : 0);
    });
    if (pulse) this.pulse(el);
  }

  private pulse(el: NodeSingular) {
    el.addClass('pulse');
    this.later(reduced() ? 260 : 560, () => el.removeClass('pulse'));
  }

  private resizeRaf = 0;

  /** One instant refit after a layout change, so the whole graph stays visible without animating or flickering. */
  resized() {
    this.cy.resize();
    if (!this.autoFit) {
      this.h.onViewport(this.cy.pan(), this.cy.zoom());
      return;
    }
    if (this.resizeRaf) return;
    this.resizeRaf = requestAnimationFrame(() => {
      this.resizeRaf = 0;
      if (this.autoFit) this.fitView(false);
    });
  }

  private scheduleFit() {
    if (!this.autoFit) return;
    if (this.fitTimer) clearTimeout(this.fitTimer);
    this.fitTimer = setTimeout(() => {
      if (this.autoFit) this.fitView(true);
    }, 220);
  }

  fit(animate = true) {
    this.autoFit = true;
    this.fitView(animate);
  }

  zoomBy(factor: number) {
    this.autoFit = false;
    const z = Math.min(2.5, Math.max(0.25, this.cy.zoom() * factor));
    this.cy.zoom({ level: z, renderedPosition: { x: this.cy.width() / 2, y: this.cy.height() / 2 } });
  }

  private fitView(animate: boolean) {
    const els = this.cy.elements(':visible');
    if (els.empty()) return;
    if (animate && !reduced()) this.cy.animate({ fit: { eles: els, padding: 30 } }, { duration: 300 });
    else this.cy.fit(els, 30);
    this.h.onViewport(this.cy.pan(), this.cy.zoom());
  }

  focus(id: string) {
    const el = this.cy.getElementById(id);
    if (el.empty()) return;
    this.cy.$(':selected').unselect();
    el.select();
    if (reduced()) this.cy.center(el);
    else this.cy.animate({ center: { eles: el } }, { duration: 260 });
  }

  setSelected(id: string | null) {
    this.cy.$(':selected').unselect();
    if (id) this.cy.getElementById(id).select();
  }

  setDry(ids: string[]) {
    this.cy.elements('.dry').removeClass('dry');
    if (!ids.length) return;
    const set = new Set(ids);
    this.cy.nodes().forEach((n) => {
      if (set.has(n.id())) n.addClass('dry');
    });
    this.cy.edges().forEach((e) => {
      if (set.has(e.data('source') as string) && set.has(e.data('target') as string)) e.addClass('dry');
    });
  }

  setSearch(q: string) {
    this.search = q.trim().toLowerCase();
    this.applySearch();
  }

  private applySearch() {
    this.cy.batch(() => {
      this.cy.elements().removeClass('faded');
      if (!this.search) return;
      this.cy.nodes().forEach((n) => {
        if (!String(n.data('label')).toLowerCase().includes(this.search)) n.addClass('faded');
      });
      this.cy.edges().addClass('faded');
    });
  }

  setLocked(locked: boolean) {
    this.cy.nodes().ungrabify();
    if (!locked) this.cy.nodes().grabify();
  }

  relayout(nodes: GraphNode[], edges: GraphEdge[]) {
    const pos = layoutAll(nodes, edges);
    this.cy.batch(() => {
      this.cy.nodes().forEach((n) => {
        const p = pos[n.id()];
        if (p) n.position(p);
      });
    });
    this.fit(true);
  }

  /** Hides everything outside `visible` (null shows all). Used by filters and the local graph. */
  setVisible(visible: Set<string> | null) {
    this.cy.batch(() => {
      this.cy.elements().removeClass('filtered');
      if (!visible) return;
      this.cy.nodes().forEach((n) => {
        if (!visible.has(n.id())) n.addClass('filtered');
      });
      this.cy.edges().forEach((e) => {
        if (!visible.has(e.data('source') as string) || !visible.has(e.data('target') as string)) e.addClass('filtered');
      });
    });
  }

  setGods(ids: string[]) {
    this.cy.nodes('.god').removeClass('god');
    ids.forEach((id) => this.cy.getElementById(id).addClass('god'));
  }

  setPath(ids: string[]) {
    this.cy.elements('.onpath').removeClass('onpath');
    this.cy.elements('.pathfade').removeClass('pathfade');
    if (!ids.length) return;
    const on = new Set(ids);
    this.cy.nodes().forEach((n) => {
      if (on.has(n.id())) n.addClass('onpath');
      else n.addClass('pathfade');
    });
    this.cy.edges().forEach((e) => {
      const a = e.data('source') as string;
      const b = e.data('target') as string;
      const i = ids.indexOf(a);
      const j = ids.indexOf(b);
      if (i >= 0 && j >= 0 && Math.abs(i - j) === 1) e.addClass('onpath');
      else e.addClass('pathfade');
    });
  }

  /** Node positions and the visible rectangle, for the minimap. */
  mini() {
    const z = this.cy.zoom();
    const pan = this.cy.pan();
    const nodes: { id: string; x: number; y: number; status: string; kind: string }[] = [];
    this.cy.nodes(':visible').forEach((n: NodeSingular) => {
      nodes.push({ id: n.id(), x: n.position('x'), y: n.position('y'), status: String(n.data('status')), kind: String(n.data('kind')) });
    });
    return { nodes, view: { x: -pan.x / z, y: -pan.y / z, w: this.cy.width() / z, h: this.cy.height() / z } };
  }

  panToModel(x: number, y: number) {
    this.autoFit = false;
    const z = this.cy.zoom();
    this.cy.pan({ x: this.cy.width() / 2 - x * z, y: this.cy.height() / 2 - y * z });
  }

  toggleCollapse(id: string | null) {
    if (!id) return;
    if (this.collapsedRoots.has(id)) this.collapsedRoots.delete(id);
    else this.collapsedRoots.add(id);
    this.applyCollapse();
  }

  isCollapsed(id: string | null) {
    return !!id && this.collapsedRoots.has(id);
  }

  private applyCollapse() {
    this.cy.batch(() => {
      this.cy.elements().removeClass('hidden');
      for (const root of this.collapsedRoots) {
        if (this.cy.getElementById(root).empty()) continue;
        const seen = new Set<string>();
        const queue = [root];
        while (queue.length) {
          const cur = queue.shift() as string;
          this.cy.edges().forEach((e) => {
            const type = e.data('type') as string;
            if (e.data('source') !== cur || (type !== 'LEADS_TO' && type !== 'SUPPORTS')) return;
            const t = e.data('target') as string;
            if (!seen.has(t)) {
              seen.add(t);
              queue.push(t);
            }
          });
        }
        seen.forEach((id) => {
          const el = this.cy.getElementById(id);
          el.addClass('hidden');
          el.connectedEdges().addClass('hidden');
        });
      }
    });
  }
}
