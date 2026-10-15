import { useEffect, useRef } from 'react';
import { copy } from '../copy';
import { tools } from '../copy/tools';
import { graphBus } from '../graph/bus';
import { adjacency, ancestors } from '../state/insights';
import { useStore, visibleGraph } from '../state/store';
import { getPreview } from '../state/endpoints';
import { Icon } from './Icon';

export function ContextMenu() {
  const menu = useStore((s) => s.ctxMenu);
  const nodes = useStore((s) => s.nodes);
  const set = useStore((s) => s.set);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!menu) return;
    const close = () => useStore.getState().set({ ctxMenu: null });
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && close();
    window.addEventListener('pointerdown', close);
    window.addEventListener('scroll', close, true);
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('pointerdown', close);
      window.removeEventListener('scroll', close, true);
      window.removeEventListener('keydown', onKey);
    };
  }, [menu]);

  useEffect(() => {
    if (menu) ref.current?.querySelector<HTMLButtonElement>('button[role="menuitem"]')?.focus();
  }, [menu]);

  if (!menu) return null;
  const node = nodes[menu.id];
  if (!node) return null;
  const close = () => set({ ctxMenu: null });
  const st = useStore.getState();

  const items: { icon: string; label: string; run: () => void }[] = [
    {
      icon: 'target',
      label: tools.ctx.focus,
      run: () => {
        set({ selectedId: menu.id });
        graphBus.focus.emit(menu.id);
      },
    },
    {
      icon: 'fit',
      label: tools.ctx.inspect,
      run: () => set({ selectedId: menu.id, inspectorTab: 'details' }),
    },
    {
      icon: 'path',
      label: tools.ctx.root,
      run: () => {
        const g = visibleGraph(st);
        const adj = adjacency(g.nodes, g.edges);
        const up = [...ancestors(adj, menu.id)];
        const ordered = ['source', 'fact', 'conclusion', 'action'];
        const chain = [...up, menu.id].sort(
          (a, b) => ordered.indexOf(nodes[a]?.kind ?? 'action') - ordered.indexOf(nodes[b]?.kind ?? 'action'),
        );
        set({ selectedId: menu.id, pathIds: chain, tab: 'graph' });
      },
    },
    {
      icon: 'layers',
      label: tools.ctx.local,
      run: () => set({ selectedId: menu.id, filters: { ...st.filters, local: true } }),
    },
    {
      icon: 'alert',
      label: tools.ctx.blast,
      run: () => {
        void getPreview(menu.id)
          .then((p) => {
            set({ previewIds: p.node_ids });
            st.pushToast(copy.containment.dryRunLine(p.facts, p.conclusions, p.actions.staged, p.actions.executed_reversible + p.actions.executed_compensable, p.actions.executed_irreversible));
          })
          .catch(() => undefined);
      },
    },
    {
      icon: 'collapse',
      label: tools.ctx.collapse,
      run: () => {
        set({ selectedId: menu.id });
        graphBus.collapse.emit();
      },
    },
    {
      icon: 'copy',
      label: tools.ctx.copy,
      run: () => {
        void navigator.clipboard?.writeText(menu.id).catch(() => undefined);
        st.pushToast(tools.ctx.copied(menu.id));
      },
    },
  ];

  const onKey = (e: React.KeyboardEvent) => {
    const btns = [...(ref.current?.querySelectorAll<HTMLButtonElement>('button[role="menuitem"]') ?? [])];
    const i = btns.indexOf(document.activeElement as HTMLButtonElement);
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      btns[(i + 1) % btns.length]?.focus();
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      btns[(i - 1 + btns.length) % btns.length]?.focus();
    } else if (e.key === 'Tab') {
      e.preventDefault();
    }
  };

  const x = Math.min(menu.x, window.innerWidth - 260);
  const y = Math.min(menu.y, window.innerHeight - items.length * 38 - 60);
  return (
    <div className="ctxmenu" ref={ref} style={{ left: x, top: y }} role="menu" aria-label={node.label} onPointerDown={(e) => e.stopPropagation()} onKeyDown={onKey}>
      <p className="ctx-head">
        <span className="kind">{node.is_decision ? copy.kind.decision : (copy.kind[node.kind] ?? node.kind)}</span>
        <strong>{node.label}</strong>
      </p>
      {items.map((it) => (
        <button
          key={it.label}
          role="menuitem"
          onClick={() => {
            it.run();
            close();
          }}
        >
          <Icon name={it.icon} size={15} />
          {it.label}
        </button>
      ))}
    </div>
  );
}
