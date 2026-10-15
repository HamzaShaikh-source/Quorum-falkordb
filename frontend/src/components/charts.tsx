import type { ReactNode } from 'react';

/** Small SVG charts in the console's ink-and-mint look. No library: they are a few lines each and print crisply. */

export interface Slice {
  label: string;
  value: number;
  color: string;
}

/** A ring split into slices, with the total in the middle. */
export function Donut({ slices, size = 132, centre, sub }: { slices: Slice[]; size?: number; centre: ReactNode; sub: string }) {
  const total = slices.reduce((n, s) => n + s.value, 0);
  const r = size / 2 - 12;
  const c = 2 * Math.PI * r;
  let offset = 0;
  return (
    <figure className="ch-donut" style={{ width: size }}>
      <svg viewBox={`0 0 ${size} ${size}`} width={size} height={size} role="img" aria-label={slices.map((s) => `${s.value} ${s.label}`).join(', ') || 'nothing yet'}>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--line-2)" strokeWidth="14" />
        {total > 0 &&
          slices
            .filter((s) => s.value > 0)
            .map((s) => {
              const len = (s.value / total) * c;
              const el = <circle key={s.label} cx={size / 2} cy={size / 2} r={r} fill="none" stroke={s.color} strokeWidth="14" strokeDasharray={`${len} ${c - len}`} strokeDashoffset={-offset} transform={`rotate(-90 ${size / 2} ${size / 2})`} />;
              offset += len;
              return el;
            })}
        <text x="50%" y="48%" textAnchor="middle" className="ch-big">
          {centre}
        </text>
        <text x="50%" y="63%" textAnchor="middle" className="ch-sub">
          {sub}
        </text>
      </svg>
    </figure>
  );
}

export function Legend({ items }: { items: Slice[] }) {
  return (
    <ul className="ch-legend">
      {items.map((s) => (
        <li key={s.label}>
          <i style={{ background: s.color }} aria-hidden="true" />
          <span>{s.label}</span>
          <b className="num">{s.value}</b>
        </li>
      ))}
    </ul>
  );
}

/** Vertical bars over time. `labels` sit under the left edge, the middle and the right edge. */
export function Bars({ values, labels, height = 120, title }: { values: number[]; labels: [string, string, string]; height?: number; title: string }) {
  const max = Math.max(1, ...values);
  const w = 100 / values.length;
  return (
    <figure className="ch-bars">
      <svg viewBox={`0 0 100 ${height}`} preserveAspectRatio="none" width="100%" height={height} role="img" aria-label={title}>
        {[0.25, 0.5, 0.75].map((g) => (
          <line key={g} x1="0" x2="100" y1={height * g} y2={height * g} stroke="var(--line)" strokeWidth="0.4" vectorEffect="non-scaling-stroke" />
        ))}
        {values.map((v, i) => {
          const h = (v / max) * (height - 6);
          return <rect key={i} x={i * w + w * 0.14} y={height - h} width={w * 0.72} height={Math.max(v > 0 ? 2 : 0, h)} rx="0.8" fill={v > 0 ? 'var(--mint)' : 'transparent'} stroke={v > 0 ? 'var(--text)' : 'none'} strokeWidth="0.8" vectorEffect="non-scaling-stroke" />;
        })}
      </svg>
      <figcaption className="ch-axis">
        <span>{labels[0]}</span>
        <span>{labels[1]}</span>
        <span>{labels[2]}</span>
      </figcaption>
    </figure>
  );
}

/** One horizontal bar split into parts, to show the shape of the memory at a glance. */
export function StackBar({ parts }: { parts: Slice[] }) {
  const total = parts.reduce((n, p) => n + p.value, 0);
  return (
    <div className="ch-stack" role="img" aria-label={parts.map((p) => `${p.value} ${p.label}`).join(', ')}>
      {total === 0 ? (
        <i style={{ width: '100%', background: 'var(--line-2)' }} />
      ) : (
        parts
          .filter((p) => p.value > 0)
          .map((p) => <i key={p.label} title={`${p.label}: ${p.value}`} style={{ width: `${(p.value / total) * 100}%`, background: p.color }} />)
      )}
    </div>
  );
}

/** The empty-state picture: beliefs about to be linked. */
export function EmptyGraphArt({ size = 200 }: { size?: number }) {
  return (
    <svg viewBox="0 0 200 130" width={size} height={(size * 130) / 200} aria-hidden="true" className="ch-art">
      <g fill="none" stroke="var(--text)" strokeWidth="2" strokeLinecap="round">
        <path d="M54 40 L100 70" strokeDasharray="5 6" />
        <path d="M146 36 L100 70" strokeDasharray="5 6" />
        <path d="M100 70 L100 106" strokeDasharray="5 6" />
      </g>
      <rect x="22" y="22" width="64" height="30" rx="8" fill="#fff" stroke="var(--text)" strokeWidth="2" />
      <rect x="118" y="20" width="64" height="30" rx="15" fill="var(--mint)" stroke="var(--text)" strokeWidth="2" />
      <rect x="68" y="56" width="64" height="30" rx="8" fill="#fff3d6" stroke="var(--text)" strokeWidth="2" />
      <rect x="68" y="98" width="64" height="24" rx="8" fill="#fff" stroke="var(--text)" strokeWidth="2" strokeDasharray="4 4" />
      <path d="M36 37h36M132 35h36M82 71h36" stroke="var(--text)" strokeWidth="2" strokeLinecap="round" opacity=".35" />
    </svg>
  );
}
