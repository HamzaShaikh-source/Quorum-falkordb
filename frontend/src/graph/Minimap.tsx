import { tools } from '../copy/tools';

export interface MiniData {
  nodes: { id: string; x: number; y: number; status: string; kind: string }[];
  view: { x: number; y: number; w: number; h: number };
}

const W = 176;
const H = 112;

export function Minimap({ data, selected, onPan }: { data: MiniData; selected: string | null; onPan: (x: number, y: number) => void }) {
  if (data.nodes.length === 0) return null;
  const xs = data.nodes.map((n) => n.x);
  const ys = data.nodes.map((n) => n.y);
  const pad = 90;
  const minX = Math.min(...xs) - pad;
  const maxX = Math.max(...xs) + pad;
  const minY = Math.min(...ys) - pad;
  const maxY = Math.max(...ys) + pad;
  const sc = Math.min(W / (maxX - minX), H / (maxY - minY));
  const ox = (W - (maxX - minX) * sc) / 2;
  const oy = (H - (maxY - minY) * sc) / 2;
  const px = (x: number) => ox + (x - minX) * sc;
  const py = (y: number) => oy + (y - minY) * sc;
  const v = data.view;

  const click = (e: React.MouseEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const mx = ((e.clientX - r.left) / r.width) * W;
    const my = ((e.clientY - r.top) / r.height) * H;
    onPan((mx - ox) / sc + minX, (my - oy) / sc + minY);
  };

  return (
    <div className="minimap">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={tools.mini.aria} onClick={click}>
        {data.nodes.map((n) => (
          <rect
            key={n.id}
            className={`mm-node s-${n.status}${selected === n.id ? ' sel' : ''}`}
            x={px(n.x) - 5}
            y={py(n.y) - 2.6}
            width={10}
            height={5.2}
            rx={n.kind === 'fact' ? 2.6 : 1}
          />
        ))}
        <rect className="mm-view" x={px(v.x)} y={py(v.y)} width={Math.max(8, v.w * sc)} height={Math.max(6, v.h * sc)} />
      </svg>
      <span className="mm-title mono">{tools.mini.title}</span>
    </div>
  );
}
