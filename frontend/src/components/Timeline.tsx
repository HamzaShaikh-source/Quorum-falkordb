import { useEffect, useMemo, useRef, useState } from 'react';
import { copy } from '../copy';
import { diffGraphs } from '../state/diff';
import { useStore, visibleGraph } from '../state/store';
import { goToTime } from '../state/timetravel';
import { Icon } from './Icon';
import { fmtTime } from './ui';

interface Marker {
  ts: number;
  kind: 'poison' | 'quarantine' | 'supersede' | 'restore';
}

export function Timeline() {
  const eventLog = useStore((s) => s.eventLog);
  const asOf = useStore((s) => s.asOf);
  const compareBase = useStore((s) => s.compareBase);
  const set = useStore((s) => s.set);
  const graph = useStore((s) => visibleGraph(s));
  const mounted = useRef(Date.now());
  const [playing, setPlaying] = useState(false);
  const root = useRef<HTMLElement>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  const debounce = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const el = root.current;
    if (!el) return;
    const apply = () => document.documentElement.style.setProperty('--timeline-h', `${el.getBoundingClientRect().height + 34}px`);
    apply();
    const ro = new ResizeObserver(apply);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(
    () => () => {
      if (timer.current) clearInterval(timer.current);
      if (debounce.current) clearTimeout(debounce.current);
    },
    [],
  );

  const start = eventLog[0] ? eventLog[0].ts - 800 : mounted.current - 1000;
  const end = Math.max(mounted.current, eventLog[eventLog.length - 1]?.ts ?? mounted.current);
  const span = Math.max(1, end - start);
  const markers = useMemo<Marker[]>(() => {
    const out: Marker[] = [];
    let poisonSeen = false;
    for (const e of eventLog) {
      if (e.type === 'scenario_progress' && e.step === 'inject_poison' && !poisonSeen) {
        poisonSeen = true;
        out.push({ ts: e.ts, kind: 'poison' });
      } else if (e.type === 'epoch_changed' && e.reason && ['quarantine', 'supersede', 'restore'].includes(e.reason)) {
        out.push({ ts: e.ts, kind: e.reason as Marker['kind'] });
      }
    }
    return out;
  }, [eventLog]);

  const empty = eventLog.length === 0;
  const hist = useMemo(() => {
    const bins = 60;
    const counts = new Array<number>(bins).fill(0);
    for (const e of eventLog) if (e.type !== 'heartbeat') counts[Math.min(bins - 1, Math.max(0, Math.floor(((e.ts - start) / span) * bins)))]! += 1;
    const max = Math.max(1, ...counts);
    return counts.map((c) => c / max);
  }, [eventLog, start, span]);
  const value = asOf ?? end;

  function seek(v: number) {
    if (debounce.current) clearTimeout(debounce.current);
    debounce.current = setTimeout(() => void goToTime(v >= end - 400 ? null : v), 90);
  }

  function togglePlay() {
    if (playing) {
      if (timer.current) clearInterval(timer.current);
      setPlaying(false);
      return;
    }
    setPlaying(true);
    let v = start;
    const step = span / 40;
    timer.current = setInterval(() => {
      v += step;
      if (v >= end) {
        if (timer.current) clearInterval(timer.current);
        setPlaying(false);
        void goToTime(null);
        return;
      }
      void goToTime(v);
    }, 220);
  }

  const diff = compareBase ? diffGraphs(compareBase.nodes, graph.nodes) : null;

  return (
    <section className="timeline as-of-slider" ref={root} aria-label={copy.timeline.title}>
      <h2 className="sr">{copy.timeline.title}</h2>
      <div className="tl-controls">
        <button className="btn" disabled={empty} onClick={togglePlay}>
          <Icon name={playing ? 'pause' : 'play'} size={14} />
          {playing ? copy.timeline.pause : copy.timeline.play}
        </button>
        <label className="tl-slider as-of-slider">
          <span className="sr">{copy.timeline.asOf}</span>
          <div className="tl-track">
            <svg className="tl-hist" viewBox="0 0 60 10" preserveAspectRatio="none" aria-hidden="true">
              {hist.map((h, i) => (
                <rect key={i} x={i + 0.12} y={10 - h * 10} width={0.76} height={h * 10} />
              ))}
            </svg>
            {markers.map((m, i) => (
              <span
                key={i}
                className={`tl-marker m-${m.kind}`}
                style={{ left: `${((m.ts - start) / span) * 100}%` }}
                title={`${copy.timeline.markers[m.kind]} ${fmtTime(m.ts)}`}
              />
            ))}
            <input
              type="range"
              min={start}
              max={end}
              step={1}
              value={value}
              disabled={empty}
              onChange={(e) => seek(Number(e.target.value))}
              aria-label={copy.timeline.asOf}
            />
          </div>
        </label>
        <span className="tl-label num">{asOf ? fmtTime(asOf) : copy.timeline.now}</span>
        {!compareBase ? (
          <button
            className="btn quiet"
            disabled={empty}
            onClick={() => set({ compareBase: { ts: asOf ?? Date.now(), nodes: graph.nodes, edges: graph.edges } })}
          >
            {copy.timeline.compareFrom}
          </button>
        ) : (
          <button className="btn quiet" onClick={() => set({ compareBase: null })}>
            {copy.timeline.clearCompare}
          </button>
        )}
      </div>
      <div className="tl-legend" aria-hidden="true">
        {(['poison', 'quarantine', 'supersede', 'restore'] as const).map((k) => (
          <span key={k} className="tl-key">
            <i className={`m-${k}`} />
            {copy.timeline.markers[k]}
          </span>
        ))}
      </div>
      {diff && compareBase && (
        <div className="belief-diff" role="status">
          <strong>{copy.timeline.diffTitle}</strong>
          <span className="num">
            {fmtTime(compareBase.ts)} to {asOf ? fmtTime(asOf) : copy.timeline.now}:{' '}
            {copy.timeline.diffLine(diff.added.length, diff.removed.length, diff.quarantined.length, diff.replaced.length)}
          </span>
          <span className="diff-names">
            {[...diff.added.map((n) => `+ ${n.label}`), ...diff.quarantined.map((n) => `! ${n.label}`), ...diff.replaced.map((n) => `~ ${n.label}`)]
              .slice(0, 6)
              .join('   ')}
          </span>
        </div>
      )}
    </section>
  );
}
