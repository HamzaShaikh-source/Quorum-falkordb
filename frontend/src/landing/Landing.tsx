import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { Icon } from '../components/Icon';
import { landing as L } from '../copy/landing';
import { go, prefersReduced, scrollToId } from './hooks';
import { LandingGraph, useCascade, type Cascade } from './LandingGraph';
import { Loader, useLoaderNeeded } from './Loader';
import { prefetchConsole, prefetchOnIdle } from './prefetch';
import { LazyMount, ScrollChrome, useMagnetic } from './fx/Chrome';
import { CursorRing } from './fx/CursorRing';
import { NetworkCanvas } from './fx/NetworkCanvas';
import { Parallax } from './fx/Parallax';
import { SplitText } from './fx/SplitText';

const Below = lazy(() => import('./Below'));
const BelowFooter = lazy(() => import('./Below').then((m) => ({ default: m.BelowFooter })));
const BELOW = 'lp:below';

function jump(id: string) {
  window.dispatchEvent(new Event(BELOW));
  let tries = 0;
  const t = setInterval(() => {
    if (document.getElementById(id) || ++tries > 30) {
      clearInterval(t);
      scrollToId(id);
    }
  }, 60);
}

function Nav() {
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => {
    const on = () => setScrolled(window.scrollY > 24);
    on();
    window.addEventListener('scroll', on, { passive: true });
    return () => window.removeEventListener('scroll', on);
  }, []);
  return (
    <header className={`lp-nav${scrolled ? ' solid' : ''}`}>
      <button className="lp-brand" onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })} aria-label="Quorum, back to top">
        <span className="lp-mark">
          <Icon name="logo" size={20} />
        </span>
        Quorum
      </button>
      <nav className="lp-links" aria-label="Sections">
        {L.nav.links.map(([id, label]) => (
          <button key={id} onClick={() => jump(id)}>
            {label}
          </button>
        ))}
      </nav>
      <button className="lp-pill dark" onClick={() => go('app')} onPointerEnter={prefetchConsole} onFocus={prefetchConsole}>
        {L.nav.cta}
        <Icon name="conclusion" size={16} />
      </button>
    </header>
  );
}

function useHeroLoop(c: Cascade) {
  const [step, setStep] = useState(() => (prefersReduced() ? 2 : 0));
  const cRef = useRef(c);
  useEffect(() => {
    cRef.current = c;
  });
  useEffect(() => {
    if (prefersReduced()) {
      cRef.current.quarantine('f2');
      return;
    }
    let alive = true;
    const timers: ReturnType<typeof setTimeout>[] = [];
    const at = (ms: number, fn: () => void) => timers.push(setTimeout(() => alive && fn(), ms));
    const cycle = () => {
      cRef.current.restore();
      setStep(0);
      at(2200, () => setStep(1));
      at(4000, () => {
        cRef.current.quarantine('f2');
        setStep(2);
      });
      at(9600, cycle);
    };
    cycle();
    return () => {
      alive = false;
      timers.forEach(clearTimeout);
    };
  }, []);
  return step;
}

function HeroWords({ words }: { words: [string, boolean][] }) {
  let i = 0;
  return (
    <>
      {words.flatMap(([t, ghost], k) =>
        t === '\n'
          ? [<br key={`br${k}`} />]
          : t.split(' ').map((w) => (
              <span key={`${k}-${w}-${i}`} className={`split-w${ghost ? ' ghost' : ''}`} aria-hidden="true">
                <span className="split-i" style={{ transitionDelay: `${180 + i++ * 70}ms` }}>
                  {w}
                </span>{' '}
              </span>
            )),
      )}
    </>
  );
}

function Hero() {
  const cascade = useCascade();
  const step = useHeroLoop(cascade);
  const caption = [L.hero.caption.healthy, L.hero.caption.poisoned, L.hero.caption.contained][step];
  const { held, rolled, rolling, cancelled } = cascade.stats;
  return (
    <section className="lp-hero" id="top" data-spot>
      <div className="lp-spot" aria-hidden="true" />
      <NetworkCanvas />
      <Parallax speed={0.1} className="lp-float h1" innerClassName="lp-shape ci" />
      <Parallax speed={-0.12} className="lp-float h2" innerClassName="lp-shape sq" />
      <Parallax speed={0.16} className="lp-float h3" innerClassName="lp-shape tr" />
      <p className="lp-eyebrow">
        <span className="lp-dot" aria-hidden="true" />
        {L.hero.eyebrow}
      </p>
      <Parallax speed={0.05}>
        <h1 className="lp-h1 split on" aria-label={`${L.hero.before} ${L.hero.ghost} ${L.hero.after}`}>
          <HeroWords words={[[L.hero.before, false], [L.hero.ghost, true], ['\n', false], [L.hero.after, false]]} />
        </h1>
      </Parallax>
      <p className="lp-lede">{L.hero.sub}</p>
      <div className="lp-cta-row">
        <button className="lp-pill dark xl" onClick={() => go('app')} onPointerEnter={prefetchConsole} onFocus={prefetchConsole}>
          {L.hero.primary}
          <Icon name="conclusion" size={18} />
        </button>
        <button className="lp-pill ghost xl" onClick={() => go('app', true)} onPointerEnter={prefetchConsole} onFocus={prefetchConsole}>
          <Icon name="play" size={15} />
          {L.hero.secondary}
        </button>
      </div>

      <Parallax speed={-0.04}>
      <div className="lp-window" data-cursor="Live" role="img" aria-label={L.hero.windowLabel}>
        <div className="lp-window-bar">
          <i />
          <i />
          <i />
          <span className="mono">quorum / acme / commons</span>
          <span className={`lp-window-state s${step}`}>
            <b />
            {caption}
          </span>
        </div>
        <div className="lp-window-body" tabIndex={0} role="region" aria-label={L.hero.windowLabel}>
          <LandingGraph cascade={cascade} theme="dark" suspect={step === 1 ? 'f2' : undefined} label={L.hero.windowLabel} />
        </div>
        <div className="lp-window-foot">
          <span>
            <b className="num">{held}</b> held
          </span>
          <span>
            <b className="num">{rolling + rolled}</b> rolling back
          </span>
          <span>
            <b className="num">{cancelled}</b> cancelled
          </span>
          <span className="grow">No later retrieval returns them.</span>
        </div>
      </div>
      </Parallax>
    </section>
  );
}

function Marquee() {
  const items = [...L.marquee, ...L.marquee];
  return (
    <div className="lp-marquee" role="region" tabIndex={0} aria-label="Built with">
      <div className="lp-marquee-track">
        {items.map((m, i) => (
          <span key={i} aria-hidden={i >= L.marquee.length}>
            <i />
            {m}
          </span>
        ))}
      </div>
    </div>
  );
}

function Problem() {
  const [active, setActive] = useState(0);
  const rows = useRef<(HTMLLIElement | null)[]>([]);

  useEffect(() => {
    if (typeof IntersectionObserver === 'undefined') return;
    const io = new IntersectionObserver(
      (es) => {
        es.forEach((e) => {
          if (e.isIntersecting) setActive(Number((e.target as HTMLElement).dataset.i));
        });
      },
      { rootMargin: '-42% 0px -42% 0px' },
    );
    rows.current.forEach((r) => r && io.observe(r));
    return () => io.disconnect();
  }, []);

  const stages = [new Set(['s2', 'f2']), new Set(['s2', 'f2', 'c1', 'd1']), new Set(['s2', 'f2', 'c1', 'd1', 'a1', 'a2', 'a3'])];
  const lit = stages[active] ?? stages[0]!;
  const spread = {
    bad: lit,
    phase: {},
    pulse: new Set<string>(),
    notice: null,
    stats: { held: 0, rolling: 0, rolled: 0, cancelled: 0 },
    quarantine: () => undefined,
    restore: () => undefined,
  } as Cascade;

  return (
    <section className="lp-section lp-problem" id="problem">
      <div className="lp-head">
        <p className="lp-tag">
          <span>[{L.problem.tag}]</span> {L.problem.kicker}
        </p>
        <SplitText text={L.problem.title} className="lp-h2" />
      </div>
      <div className="lp-problem-grid">
        <ol className="lp-spy">
          {L.problem.rows.map((r, i) => (
            <li key={r.h} data-i={i} ref={(el) => (rows.current[i] = el)} className={active === i ? 'on' : ''}>
              <span className="lp-spy-no mono">0{i + 1}</span>
              <h3>{r.h}</h3>
              <p>{r.p}</p>
            </li>
          ))}
        </ol>
        <div className="lp-sticky">
          <div className="lp-card-dark">
            <LandingGraph cascade={spread} theme="dark" lit={lit} label={L.problem.visualLabel} />
          </div>
        </div>
      </div>
    </section>
  );
}

export function Landing({ onEnter }: { onEnter?: () => void }) {
  const [loading, setLoading] = useState(useLoaderNeeded());
  void onEnter;
  useMagnetic();
  useEffect(() => prefetchOnIdle(), []);
  return (
    <div className="lp">
      <a className="lp-skip" href="#lp-main" onClick={(e) => (e.preventDefault(), scrollToId('lp-main'))}>
        {L.skip}
      </a>
      {loading && <Loader onDone={() => setLoading(false)} />}
      <div className="lp-frame" aria-hidden="true">
        <i className="crop tl" />
        <i className="crop tr" />
        <i className="crop bl" />
        <i className="crop br" />
      </div>
      <CursorRing />
      <ScrollChrome sections={L.nav.links as [string, string][]} />
      <Nav />
      <main id="lp-main">
        <Hero />
        <Marquee />
        <Problem />
        <LazyMount event={BELOW} minHeight={2400}>
          <Suspense fallback={<div style={{ minHeight: 2400 }} aria-hidden="true" />}>
            <Below />
          </Suspense>
        </LazyMount>
      </main>
      <LazyMount event={BELOW} minHeight={420}>
        <Suspense fallback={<div style={{ minHeight: 420 }} aria-hidden="true" />}>
          <BelowFooter />
        </Suspense>
      </LazyMount>
    </div>
  );
}
