import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { API_MODE, demoCredentials, tenants } from '../api/client';
import { copy } from '../copy';
import { getInvariants } from '../state/endpoints';
import { switchTenant } from '../state/pipeline';
import { useStore, type View } from '../state/store';
import { visibleViews } from '../state/views';
import { go } from '../landing/hooks';
import { tools } from '../copy/tools';
import { rememberFocus } from './focus';
import { Icon } from './Icon';
import { Skeleton } from './ui';
import { fmt } from '../context/tokens';
import { getTokenStats } from '../workspace/server';
import { useLive } from '../workspace/live';

const VIEWS: { id: View; icon: string }[] = [
  { id: 'overview', icon: 'chart' },
  { id: 'console', icon: 'console' },
  { id: 'memory', icon: 'memory' },
  { id: 'brain', icon: 'brain' },
  { id: 'tenants', icon: 'tenants' },
  { id: 'benchmark', icon: 'bench' },
  { id: 'context', icon: 'sparkle' },
  { id: 'connect', icon: 'link' },
  { id: 'team', icon: 'user' },
  { id: 'activity', icon: 'list' },
];

/** Tab-bar labels on a phone, where nine tabs share the width. */
const SHORT: Partial<Record<View, string>> = {};

function SavedPill() {
  const naive = useLive((s) => s.totals.naive);
  const used = useLive((s) => s.totals.used);
  const set = useStore((s) => s.set);
  const loaded = useStore((s) => s.loaded);
  const stats = useQuery({ queryKey: ['token-stats'], queryFn: getTokenStats, enabled: API_MODE === 'live' && loaded, refetchInterval: 10_000 });
  const saved = API_MODE === 'live' ? (stats.data?.saved ?? 0) : Math.max(0, naive - used);
  const title =
    API_MODE === 'live'
      ? `Tokens not sent to agents, measured on ${stats.data?.measured_calls ?? 0} real read calls against a typical top-8 retriever. Open Context for the breakdown.`
      : 'Estimated tokens not sent to agents in this session (simulated). Open Context for the breakdown.';
  return (
    <button className="pill saved" onClick={() => set({ view: 'context' })} title={title}>
      <Icon name="sparkle" size={13} />
      <span className="num">{fmt(saved)}</span> <span className="saved-w">saved</span>
    </button>
  );
}

function EpochBadge() {
  const epoch = useStore((s) => s.epoch);
  const [pulse, setPulse] = useState(false);
  const prev = useRef(epoch);
  useEffect(() => {
    if (prev.current !== epoch && prev.current !== 0) {
      setPulse(true);
      const t = setTimeout(() => setPulse(false), 1500);
      prev.current = epoch;
      return () => clearTimeout(t);
    }
    prev.current = epoch;
  }, [epoch]);
  return (
    <span className={`pill epoch${pulse ? ' pulse' : ''}`} title="The memory version goes up by one every time a fact is quarantined, restored or replaced. Agents rebuild their context when it changes.">
      <Icon name="refresh" size={13} />
      {copy.epoch(epoch)}
    </span>
  );
}

function InvariantLight() {
  const inv = useStore((s) => s.invariant);
  const open = useStore((s) => s.invariantsOpen);
  const set = useStore((s) => s.set);
  // Ask for the result as soon as the console loads, so the pill says pass or fail instead of waiting to be opened.
  const q = useQuery({ queryKey: ['invariants'], queryFn: getInvariants, refetchInterval: 30_000 });
  const failing = q.data ? q.data.checks.filter((c) => !c.ok).length : 0;
  const result = inv ?? (q.data ? { ok: failing === 0, violations: failing } : null);
  const state = result ? (result.ok ? 'ok' : 'bad') : 'unknown';
  const text = result ? (result.ok ? copy.invariants.ok : copy.invariants.bad(result.violations)) : copy.invariants.unknown;
  return (
    <div className="pop-wrap">
      <button className={`pill light ${state}`} aria-expanded={open} data-popover-trigger="invariants" onClick={() => set({ invariantsOpen: !open })}>
        <Icon name="shield" size={13} />
        {text}
      </button>
      {open && (
        <div className="popover" role="dialog" aria-label="Invariant checks" data-popover="invariants">
          <h3>Invariant checks</h3>
          {q.isLoading && <Skeleton lines={3} />}
          {q.isError && <p className="muted">{copy.errors.panel('GET /invariants')}</p>}
          {q.data && (
            <ul className="checks">
              {q.data.checks.map((c) => (
                <li key={c.name}>
                  <span className={`tick ${c.ok ? 'ok' : 'bad'}`}>
                    <Icon name={c.ok ? 'check' : 'x'} size={12} />
                  </span>
                  <span>{c.name}</span>
                  <span className="num">{c.value}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

export function Header() {
  const story = useStore((s) => s.story);
  const shown = visibleViews(story);
  const view = useStore((s) => s.view);
  const set = useStore((s) => s.set);
  const meta = useStore((s) => s.meta);
  const conn = useStore((s) => s.conn);
  const tenant = useStore((s) => s.tenant);
  const presentation = useStore((s) => s.presentation);
  const mode = meta?.mode ?? 'UNKNOWN';

  return (
    <header className="header" aria-label="Quorum console">
      <button className="brand" onClick={() => go('landing')} aria-label="Quorum home" title="Back to the landing page">
        <span className="brand-mark">
          <Icon name="logo" size={20} />
        </span>
        <span className="brand-name">{copy.appName}</span>
      </button>
      <nav aria-label={copy.header.menu} className="nav" data-count={shown.length} style={{ ["--tabs" as string]: shown.length }}>
        {VIEWS.filter((v) => shown.includes(v.id)).map((v) => (
          <button key={v.id} className={`navbtn${view === v.id ? ' on' : ''}`} aria-current={view === v.id ? 'page' : undefined} aria-label={copy.nav[v.id]} title={copy.nav[v.id]} onFocus={(e) => e.currentTarget.scrollIntoView({ block: 'nearest', inline: 'nearest' })} onClick={() => set({ view: v.id })}>
            <Icon name={v.icon} size={16} />
            <span className="nav-tip" aria-hidden="true">{copy.nav[v.id]}</span>
            <span className="nav-label">
              <span className="nl-full">{copy.nav[v.id]}</span>
              <span className="nl-short" aria-hidden="true">{SHORT[v.id] ?? copy.nav[v.id]}</span>
            </span>
          </button>
        ))}
      </nav>
      <div className="status">
        <label className="tenant" title={demoCredentials() ? 'You are signed in with demo sandbox credentials. Nothing here is a real company.' : 'The company whose memory you are looking at'}>
          <span className="sr">{copy.header.tenant}</span>
          <span className="tenant-dot" aria-hidden="true" />
          <select value={tenant} onChange={(e) => void switchTenant(e.target.value)} aria-label={copy.header.tenant}>
            {(tenants().length ? tenants() : [tenant]).map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </label>
        {API_MODE === 'mock' ? (
          <span className="pill sample" title={copy.sampleHint}>
            <Icon name="sparkle" size={13} />
            <span className="sample-w">{copy.sampleData}</span>
          </span>
        ) : (
          demoCredentials() && (
            <span className="pill sample" title="Demo sandbox credentials active">
              <Icon name="sparkle" size={13} />
              <span className="sample-w">Demo</span>
            </span>
          )
        )}
        {story && (
          <span className={`pill mode ${mode}`} title={copy.modeHint[mode] ?? ''}>
            {copy.mode[mode] ?? mode}
          </span>
        )}
        <SavedPill />
        {story && <EpochBadge />}
        <InvariantLight />
        <span className={`pill conn ${conn}`} role="status">
          <span className="dot" aria-hidden="true" />
          <span className="conn-label">{copy.connection[conn]}</span>
        </span>
        <button className="palette-btn" onClick={() => {
            rememberFocus();
            set({ paletteOpen: true });
          }}
          aria-label={tools.palette.open} title={tools.palette.open}>
          <Icon name="search" size={15} />
          <span className="pb-label">Search</span>
          <kbd>Ctrl K</kbd>
        </button>
        <button className="iconbtn" aria-pressed={presentation} aria-label={copy.presentationMode} title={`${copy.presentationMode} (P)`} onClick={() => set({ presentation: !presentation })}>
          <Icon name="present" size={17} />
        </button>
        <button className="btn guide-btn" aria-label="Open the guide: what the AI does here, and an A to Z glossary" title="Guide and A to Z glossary (G)" onClick={() => {
            rememberFocus();
            set({ helpOpen: true });
          }}
        >
          <Icon name="help" size={15} />
          <span>Guide</span>
        </button>
        <button className="iconbtn" aria-label={copy.shortcuts.title} title={copy.shortcuts.title} onClick={() => {
            rememberFocus();
            set({ shortcutsOpen: true });
          }}
        >
          <Icon name="help" size={17} />
        </button>
      </div>
    </header>
  );
}
