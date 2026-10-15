import { useEffect, useMemo, useRef, useState } from 'react';
import { signedInWithToken, signOut } from '../api/client';
import { copy } from '../copy';
import { tools } from '../copy/tools';
import { graphBus } from '../graph/bus';
import { go } from '../landing/hooks';
import { startAutopilot, stopAutopilot } from '../state/autopilot';
import { runStep } from '../state/endpoints';
import { navMove, useStore, type Tab, type View } from '../state/store';
import { viewKey, visibleViews } from '../state/views';
import { goToTime } from '../state/timetravel';
import { restoreFocus } from './focus';
import { Icon } from './Icon';

interface Item {
  id: string;
  group: keyof typeof tools.palette.groups;
  label: string;
  hint?: string;
  icon: string;
  run: () => void;
}

/** Subsequence match with a bonus for contiguous and early hits. Returns -1 when there is no match. */
export function fuzzy(query: string, text: string): number {
  const q = query.toLowerCase().trim();
  if (!q) return 0;
  const t = text.toLowerCase();
  const at = t.indexOf(q);
  if (at >= 0) return 1000 - at * 4 - (t.length - q.length);
  let ti = 0;
  let score = 0;
  let last = -2;
  for (const ch of q) {
    const found = t.indexOf(ch, ti);
    if (found < 0) return -1;
    score += found === last + 1 ? 8 : 2;
    last = found;
    ti = found + 1;
  }
  return score;
}

const KIND_ICON: Record<string, string> = { source: 'doc', fact: 'fact', conclusion: 'conclusion', action: 'bolt' };

const VIEW_LABEL: Record<View, string> = { overview: 'Overview', console: 'Graph console', memory: 'Memory', brain: 'Company brain', tenants: 'Tenants', benchmark: 'Benchmark', context: 'Context and token savings', connect: 'Connect an AI (MCP)', team: 'Team and workspace', activity: 'Activity log (what agents did)' };
const VIEW_ICON: Record<View, string> = { overview: 'chart', console: 'console', memory: 'memory', brain: 'brain', tenants: 'tenants', benchmark: 'bench', context: 'sparkle', connect: 'link', team: 'user', activity: 'list' };

export function CommandPalette() {
  const open = useStore((s) => s.paletteOpen);
  return open ? <PaletteBody /> : null;
}

function PaletteBody() {
  const nodes = useStore((s) => s.nodes);
  const autopilot = useStore((s) => s.autopilot);
  const story = useStore((s) => s.story);
  const asOf = useStore((s) => s.asOf);
  const selected = useStore((s) => s.selectedId);
  const set = useStore((s) => s.set);
  const [q, setQ] = useState('');
  const [idx, setIdx] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLUListElement>(null);

  const items = useMemo<Item[]>(() => {
    const st = useStore.getState;
    const view = (v: View, tab?: Tab) => () => st().set({ view: v, ...(tab ? { tab } : {}) });
    const list: Item[] = [
      ...visibleViews(story).map((v) => ({
        id: `v-${v}`,
        group: 'go' as const,
        label: VIEW_LABEL[v],
        hint: viewKey(v, story),
        icon: VIEW_ICON[v],
        run: view(v, v === 'console' ? 'graph' : undefined),
      })),
      { id: 't-mind', group: 'go', label: 'Mind map', icon: 'map', run: view('console', 'mindmap') },
      { id: 't-diff', group: 'go', label: 'Prompt diff', icon: 'doc', run: view('console', 'diff') },
      { id: 't-ledger', group: 'go', label: 'Actions and handoffs', icon: 'bolt', run: view('console', 'ledger') },
      { id: 't-insights', group: 'go', label: 'Graph insights', icon: 'chart', run: view('console', 'insights') },
      ...(signedInWithToken() ? [{ id: 'signout', group: 'go' as const, label: 'Sign out of this hub', icon: 'lock', run: () => { signOut(); window.location.reload(); } }] : []),
      { id: 'landing', group: 'go', label: 'Back to the landing page', icon: 'logo', run: () => go('landing') },
      ...(['inject_poison', 'run_agents', 'audit', 'supersede', 'restore', 'reset'] as const).map((step) => ({
        id: `s-${step}`,
        group: 'story' as const,
        label: copy.scenario.steps[step] ?? step,
        hint: copy.scenario.hints[step],
        icon: step === 'reset' ? 'refresh' : step === 'restore' ? 'rollback' : 'play',
        run: () => void runStep(step).catch(() => st().pushToast(copy.errors.generic('ERROR', 'That step could not run.'))),
      })),
      {
        id: 's-auto',
        group: 'story',
        label: autopilot ? copy.scenario.autopilotStop : copy.scenario.autopilot,
        hint: copy.scenario.autopilotHint,
        icon: autopilot ? 'pause' : 'play',
        run: () => (autopilot ? stopAutopilot() : void startAutopilot()),
      },
      { id: 'c-fit', group: 'canvas', label: copy.canvas.fit, hint: 'F', icon: 'fit', run: () => graphBus.fit.emit() },
      { id: 'c-layout', group: 'canvas', label: copy.canvas.relayout, icon: 'refresh', run: () => graphBus.relayout.emit() },
      { id: 'c-list', group: 'canvas', label: `Toggle ${copy.canvas.list.toLowerCase()}`, hint: 'L', icon: 'list', run: () => st().set({ listView: !st().listView }) },
      { id: 'c-present', group: 'canvas', label: copy.presentationMode, hint: 'P', icon: 'present', run: () => st().set({ presentation: !st().presentation }) },
      {
        id: 'c-gods',
        group: 'canvas',
        label: tools.filters.gods,
        icon: 'star',
        run: () => st().set({ filters: { ...st().filters, gods: !st().filters.gods } }),
      },
      {
        id: 'c-hide',
        group: 'canvas',
        label: tools.filters.hideHealthy,
        icon: 'sliders',
        run: () => st().set({ filters: { ...st().filters, hideHealthy: !st().filters.hideHealthy } }),
      },
      ...(selected
        ? [
            {
              id: 'c-local',
              group: 'canvas' as const,
              label: tools.filters.local,
              icon: 'layers',
              run: () => st().set({ filters: { ...st().filters, local: !st().filters.local } }),
            },
          ]
        : []),
      ...(asOf ? [{ id: 'c-now', group: 'canvas' as const, label: copy.canvas.backToNow, icon: 'back', run: () => void goToTime(null) }] : []),
      { id: 'n-back', group: 'nav', label: tools.crumbs.back, hint: 'Alt+Left', icon: 'back', run: () => navMove(-1) },
      { id: 'n-fwd', group: 'nav', label: tools.crumbs.forward, hint: 'Alt+Right', icon: 'forward', run: () => navMove(1) },
      ...Object.values(nodes).map((n) => ({
        id: `n-${n.id}`,
        group: 'nodes' as const,
        label: n.label,
        hint: `${n.is_decision ? copy.kind.decision : copy.kind[n.kind]} · ${n.id}`,
        icon: n.is_decision ? 'flag' : (KIND_ICON[n.kind] ?? 'fact'),
        run: () => {
          st().set({ view: 'console', tab: 'graph', selectedId: n.id });
          setTimeout(() => graphBus.focus.emit(n.id), 60);
        },
      })),
    ];
    return story ? list : list.filter((i) => i.group !== 'story');
  }, [nodes, autopilot, asOf, selected, story]);

  const results = useMemo(() => {
    const scored = items
      .map((it) => ({ it, s: Math.max(fuzzy(q, it.label), fuzzy(q, it.hint ?? '') - 40, fuzzy(q, it.id) - 60) }))
      .filter((r) => r.s >= 0 || !q.trim());
    const sorted = q.trim() ? scored.sort((a, b) => b.s - a.s) : scored.filter((r) => r.it.group !== 'nodes');
    return sorted.slice(0, 40).map((r) => r.it);
  }, [items, q]);

  useEffect(() => () => restoreFocus(), []);
  useEffect(() => {
    list.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: 'nearest' });
  }, [idx]);

  const close = () => set({ paletteOpen: false });
  const run = (it: Item | undefined) => {
    if (!it) return;
    close();
    setTimeout(it.run, 0);
  };

  return (
    <div className="palette-back" onMouseDown={close}>
      <div
        className="palette"
        role="dialog"
        aria-modal="true"
        aria-label={tools.palette.open}
        onMouseDown={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown') {
            e.preventDefault();
            setIdx((i) => Math.min(results.length - 1, i + 1));
          } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            setIdx((i) => Math.max(0, i - 1));
          } else if (e.key === 'Enter') {
            e.preventDefault();
            run(results[idx]);
          } else if (e.key === 'Escape') {
            e.preventDefault();
            e.stopPropagation();
            close();
          } else if (e.key === 'Tab') {
            e.preventDefault();
          }
        }}
      >
        <div className="palette-input">
          <Icon name="search" size={18} />
          <input
            ref={input}
            autoFocus
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setIdx(0);
            }}
            placeholder={tools.palette.placeholder}
            aria-label={tools.palette.placeholder}
            aria-controls="palette-list"
            aria-activedescendant={results[idx] ? `pal-${results[idx].id}` : undefined}
            role="combobox"
            aria-expanded="true"
          />
          <kbd>Esc</kbd>
        </div>
        <ul className="palette-list" id="palette-list" role="listbox" ref={list}>
          {results.length === 0 && <li className="palette-empty">{tools.palette.empty}</li>}
          {results.map((it, i) => {
            const head = i === 0 || results[i - 1]?.group !== it.group;
            return (
              <li key={it.id} role="presentation">
                {head && <p className="palette-group">{tools.palette.groups[it.group]}</p>}
                <button
                  id={`pal-${it.id}`}
                  role="option"
                  aria-selected={i === idx}
                  className={`palette-item${i === idx ? ' on' : ''}`}
                  onMouseMove={() => setIdx(i)}
                  onClick={() => run(it)}
                >
                  <Icon name={it.icon} size={16} />
                  <span className="pl-label">{it.label}</span>
                  {it.hint && <span className="pl-hint">{it.hint}</span>}
                </button>
              </li>
            );
          })}
        </ul>
        <p className="palette-foot mono">{tools.palette.hint}</p>
      </div>
    </div>
  );
}
