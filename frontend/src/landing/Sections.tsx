import { useEffect, useState } from 'react';
import { Icon } from '../components/Icon';
import { landing as L } from '../copy/landing';
import { go, prefersReduced, useCounter, useInView } from './hooks';
import { LandingGraph, useCascade } from './LandingGraph';
import { LatencyChart } from './fx/LatencyChart';
import { Parallax } from './fx/Parallax';
import { cursorDisabled, fineHover, setCursorDisabled } from './fx/pointer';
import { SplitText } from './fx/SplitText';

function Head({ tag, kicker, title, sub, dark = false }: { tag: string; kicker: string; title: string; sub?: string; dark?: boolean }) {
  const [ref, seen] = useInView<HTMLDivElement>(0.2);
  return (
    <div className={`lp-head${dark ? ' dark' : ''}`}>
      <p className="lp-tag">
        <span>[{tag}]</span> {kicker}
      </p>
      <SplitText text={title} className="lp-h2" />
      {sub && (
        <div ref={ref} className={`reveal${seen ? ' in' : ''}`}>
          <p className="lp-sub">{sub}</p>
        </div>
      )}
    </div>
  );
}

export function Playground() {
  const c = useCascade();
  const { stats, notice, bad } = c;
  const [ref, seen] = useInView<HTMLDivElement>(0.15);
  return (
    <section className="lp-section" id="playground">
      <Head tag={L.play.tag} kicker={L.play.kicker} title={L.play.title} sub={L.play.sub} />
      <div ref={ref} className={`lp-play reveal${seen ? ' in' : ''}`}>
        <div className="lp-play-top">
          <span className="lp-pixel">{L.play.pixel}</span>
          <div className="lp-play-actions">
            <button className="lp-pill red" onClick={() => c.quarantine('f2')}>
              <Icon name="alert" size={15} />
              {L.play.poison}
            </button>
            <button className="lp-pill ghost" onClick={c.restore} disabled={bad.size === 0}>
              <Icon name="rollback" size={15} />
              {L.play.reset}
            </button>
          </div>
        </div>
        <div className="lp-play-stage" data-cursor="Click a node">
          <LandingGraph cascade={c} interactive theme="light" label={L.play.graphLabel} />
        </div>
        <div className="lp-play-foot">
          <dl className="lp-meters">
            {(['held', 'rolling', 'rolled', 'cancelled'] as const).map((k) => (
              <div key={k} className={stats[k] > 0 ? 'on' : ''}>
                <dd className="lp-pixel big num">{String(stats[k]).padStart(2, '0')}</dd>
                <dt>{L.play.stats[k]}</dt>
              </div>
            ))}
          </dl>
          <p className="lp-play-note" role="status" aria-live="polite">
            {notice ?? (bad.size ? L.play.done(stats.held) : L.play.idle)}
          </p>
        </div>
      </div>
    </section>
  );
}

function VisualPrevent() {
  return (
    <svg viewBox="0 0 420 260" className="lp-vis" aria-hidden="true">
      <g className="v-src">
        <rect x="24" y="44" width="92" height="44" rx="6" />
        <text x="70" y="71">source A</text>
        <rect x="24" y="170" width="92" height="44" rx="6" />
        <text x="70" y="197">source B</text>
      </g>
      <path className="v-line" d="M116 66 H190 V118 M116 192 H190 V142" />
      <g className="v-gate">
        <circle cx="210" cy="130" r="34" />
        <path d="M198 132 h24 v16 h-24 z M202 132 v-8 a8 8 0 0 1 16 0 v8" />
      </g>
      <path className="v-line flow" d="M244 130 H312" />
      <g className="v-act">
        <rect x="300" y="106" width="116" height="48" rx="6" />
        <text x="358" y="128">refund</text>
        <text x="358" y="144" className="s">needs 2 sources</text>
      </g>
    </svg>
  );
}

function VisualGuard() {
  return (
    <svg viewBox="0 0 420 260" className="lp-vis" aria-hidden="true">
      <g className="v-chain">
        <circle cx="48" cy="130" r="22" className="ok" />
        <circle cx="140" cy="130" r="22" className="ok" />
        <circle cx="232" cy="130" r="22" className="bad" />
      </g>
      <path className="v-line" d="M70 130 H118 M162 130 H210" />
      <path className="v-line dashed" d="M254 130 H318" />
      <g className="v-act blocked">
        <rect x="318" y="104" width="86" height="52" rx="6" />
        <text x="361" y="126">execute</text>
        <text x="361" y="144" className="s">0 rows</text>
      </g>
      <path className="v-x" d="M300 112 l22 36 M322 112 l-22 36" />
      <text x="232" y="176" className="v-note" textAnchor="middle">ancestor quarantined</text>
    </svg>
  );
}

function VisualRepair() {
  return (
    <svg viewBox="0 0 420 260" className="lp-vis" aria-hidden="true">
      <circle className="v-ring r1" cx="210" cy="130" r="30" />
      <circle className="v-ring r2" cx="210" cy="130" r="30" />
      <circle className="v-ring r3" cx="210" cy="130" r="30" />
      <circle className="v-core" cx="210" cy="130" r="14" />
      <g className="v-sats">
        <circle cx="120" cy="80" r="8" />
        <circle cx="300" cy="90" r="8" />
        <circle cx="90" cy="170" r="8" />
        <circle cx="330" cy="176" r="8" />
        <circle cx="210" cy="224" r="8" />
      </g>
      <text x="210" y="252" className="v-note" textAnchor="middle">epoch + 1</text>
    </svg>
  );
}

export function Defense() {
  const [i, setI] = useState(0);
  const [hold, setHold] = useState(false);
  const n = L.defense.slides.length;
  useEffect(() => {
    if (hold || prefersReduced()) return;
    const t = setTimeout(() => setI((x) => (x + 1) % n), 7000);
    return () => clearTimeout(t);
  }, [i, hold, n]);
  const s = L.defense.slides[i]!;
  const Vis = [VisualPrevent, VisualGuard, VisualRepair][i]!;
  return (
    <section className="lp-section" id="defense">
      <Head tag={L.defense.tag} kicker={L.defense.kicker} title={L.defense.title} />
      <div className="lp-defense" onMouseEnter={() => setHold(true)} onMouseLeave={() => setHold(false)} onFocus={() => setHold(true)} onBlur={() => setHold(false)}>
        <div className="lp-defense-copy">
          <div className="lp-counter" aria-live="polite">
            <b className="num">{String(i + 1).padStart(2, '0')}</b>
            <span>/ {String(n).padStart(2, '0')}</span>
          </div>
          <p className="lp-k">{s.k}</p>
          <h3 key={`h${i}`} className="lp-h3 swap">
            {s.h}
          </h3>
          <p key={`p${i}`} className="lp-body swap">
            {s.p}
          </p>
          <div className="lp-arrows">
            <button className="lp-sq" aria-label={L.defense.prev} onClick={() => setI((x) => (x + n - 1) % n)}>
              <Icon name="back" size={18} />
            </button>
            <button className="lp-sq" aria-label={L.defense.next} onClick={() => setI((x) => (x + 1) % n)}>
              <Icon name="conclusion" size={18} />
            </button>
            <span className="lp-progress" aria-hidden="true">
              <i key={`${i}-${hold}`} className={hold ? 'paused' : ''} />
            </span>
          </div>
        </div>
        <div className="lp-defense-vis" key={i}>
          <Vis />
        </div>
      </div>
    </section>
  );
}

export function Gallery() {
  const [i, setI] = useState(0);
  const [hold, setHold] = useState(false);
  const items = L.gallery.items;
  const n = items.length;
  useEffect(() => {
    if (hold || prefersReduced()) return;
    const t = setTimeout(() => setI((x) => (x + 1) % n), 5500);
    return () => clearTimeout(t);
  }, [i, hold, n]);
  const [file, name, line] = items[i]!;
  return (
    <section className="lp-section" id="console">
      <Head tag={L.gallery.tag} kicker={L.gallery.kicker} title={L.gallery.title} />
      <div className="lp-gallery" onMouseEnter={() => setHold(true)} onMouseLeave={() => setHold(false)}>
        <div className="lp-shot">
          <div className="lp-window-bar">
            <i />
            <i />
            <i />
            <span className="mono">{name}</span>
          </div>
          <img key={file} src={`/screens/${file}.png`} alt={`${name} view of the Quorum console`} loading="lazy" />
        </div>
        <div className="lp-gallery-side">
          <div className="lp-counter">
            <b className="num">{String(i + 1).padStart(2, '0')}</b>
            <span>/ {String(n).padStart(2, '0')}</span>
          </div>
          <h3 className="lp-h3">{name}</h3>
          <p className="lp-body">{line}</p>
          <ul className="lp-thumbs">
            {items.map(([f, nm], k) => (
              <li key={f}>
                <button className={k === i ? 'on' : ''} onClick={() => setI(k)} aria-current={k === i}>
                  <span className="mono">0{k + 1}</span>
                  {nm}
                </button>
              </li>
            ))}
          </ul>
          <div className="lp-arrows">
            <button className="lp-sq" aria-label={L.gallery.prev} onClick={() => setI((x) => (x + n - 1) % n)}>
              <Icon name="back" size={18} />
            </button>
            <button className="lp-sq" aria-label={L.gallery.next} onClick={() => setI((x) => (x + 1) % n)}>
              <Icon name="conclusion" size={18} />
            </button>
            <button className="lp-pill dark" onClick={() => go('app')}>
              {L.gallery.cta}
              <Icon name="conclusion" size={16} />
            </button>
          </div>
        </div>
      </div>
    </section>
  );
}

function Stat({ v, u, l, d = 0, run }: { v: number; u: string; l: string; d?: number; run: boolean }) {
  const shown = useCounter(v, run, d);
  return (
    <div className="lp-stat">
      <span className="lp-stat-v num">
        {shown}
        <small>{u}</small>
      </span>
      <span className="lp-stat-l">{l}</span>
    </div>
  );
}

export function Proof() {
  const [ref, seen] = useInView<HTMLDivElement>(0.3);
  return (
    <section className="lp-section lp-dark" id="proof" data-spot>
      <div className="lp-spot dark" aria-hidden="true" />
      <Head dark tag={L.proof.tag} kicker={L.proof.kicker} title={L.proof.title} />
      <div className="lp-stats" ref={ref}>
        {L.proof.items.map((it) => (
          <Stat key={it.l} v={it.v} u={it.u} l={it.l} d={'d' in it ? it.d : 0} run={seen} />
        ))}
      </div>
      <LatencyChart />
      <p className="lp-note">{L.proof.note}</p>
    </section>
  );
}

export function Tracks() {
  const col = (c: typeof L.tracks.t2) => (
    <div className="lp-track">
      <h3>{c.h}</h3>
      <ul>
        {c.items.map(([t, where]) => (
          <li key={t}>
            <span className="lp-check">
              <Icon name="check" size={14} />
            </span>
            <span>{t}</span>
            <em className="mono">
              {L.tracks.verify} {where}
            </em>
          </li>
        ))}
      </ul>
    </div>
  );
  return (
    <section className="lp-section" id="tracks">
      <Head tag={L.tracks.tag} kicker={L.tracks.kicker} title={L.tracks.title} />
      <div className="lp-tracks">
        {col(L.tracks.t2)}
        {col(L.tracks.t3)}
      </div>
    </section>
  );
}

export function Cta() {
  const [ref, seen] = useInView<HTMLDivElement>(0.3);
  return (
    <section className="lp-cta" id="start" data-spot>
      <div className="lp-spot" aria-hidden="true" />
      <Parallax speed={-0.14} className="lp-float f1" innerClassName="lp-shape sq" />
      <Parallax speed={0.2} className="lp-float f2" innerClassName="lp-shape ci" />
      <Parallax speed={-0.08} className="lp-float f3" innerClassName="lp-shape tr" />
      <div ref={ref} className={`reveal${seen ? ' in' : ''}`}>
        <h2 className="lp-giant">{L.cta.title}</h2>
        <p className="lp-lede">{L.cta.sub}</p>
        <div className="lp-cta-row">
          <button className="lp-pill dark xl" onClick={() => go('app')}>
            {L.cta.primary}
            <Icon name="conclusion" size={18} />
          </button>
          <button className="lp-pill ghost xl" onClick={() => go('app', true)}>
            <Icon name="play" size={15} />
            {L.cta.secondary}
          </button>
        </div>
      </div>
    </section>
  );
}

/** The custom cursor is a flourish: anyone can switch it off, and it is off for reduced motion and for keyboard use. */
function CursorSwitch() {
  const [off, setOff] = useState(cursorDisabled());
  if (!fineHover()) return null;
  return (
    <button
      className="lp-link-btn"
      aria-pressed={off}
      onClick={() => {
        setCursorDisabled(!off);
        setOff(!off);
      }}
    >
      {off ? 'Use the custom cursor' : 'Use the normal cursor'}
    </button>
  );
}

export function Footer() {
  return (
    <footer className="lp-footer">
      <div className="lp-footer-grid">
        <div>
          <p className="lp-k">Team</p>
          <ul>
            {L.footer.team.map(([n, r]) => (
              <li key={n}>
                <b>{n}</b> <span>{r}</span>
              </li>
            ))}
          </ul>
        </div>
        <div>
          <p className="lp-k">{L.footer.limits}</p>
          <p className="lp-small">{L.footer.limitsText}</p>
        </div>
        <div>
          <p className="lp-k">AI use</p>
          <p className="lp-small">{L.footer.ai}</p>
        </div>
      </div>
      <p className="lp-rights mono">
        {L.footer.rights} <CursorSwitch />
      </p>
    </footer>
  );
}
