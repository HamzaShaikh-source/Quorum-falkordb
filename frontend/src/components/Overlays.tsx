import { memo, useEffect, useMemo, useRef, useState } from 'react';
import { API_MODE, session, type Role } from '../api/client';
import { subscribeDevLog, type DevLogEntry } from '../api/devlog';
import { CONTRACT_VERSION } from '../api/schemas';
import { copy } from '../copy';
import { setRole, startSession } from '../state/pipeline';
import { useStore, type Toast } from '../state/store';
import { restoreFocus } from './focus';
import { Icon } from './Icon';
import { fmtTime } from './ui';

function ToastItem({ t }: { t: Toast }) {
  const drop = useStore((s) => s.dropToast);
  const [paused, setPaused] = useState(false);
  const left = useRef(t.ttl);
  useEffect(() => {
    if (paused) return;
    const started = Date.now();
    const h = setTimeout(() => drop(t.id), left.current);
    return () => {
      clearTimeout(h);
      left.current -= Date.now() - started;
    };
  }, [paused, drop, t.id]);
  const icon = t.tone === 'bad' ? 'alert' : t.tone === 'warn' ? 'clock' : t.tone === 'good' ? 'check' : 'sparkle';
  return (
    <div
      className={`toast tone-${t.tone}`}
      role="status"
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      onFocus={() => setPaused(true)}
      onBlur={() => setPaused(false)}
    >
      <Icon name={icon} size={16} />
      <span className="toast-text">
        {t.text}
        {t.count > 1 && <b className="toast-count num">x{t.count}</b>}
      </span>
      <button className="iconbtn" aria-label={copy.dismiss} onClick={() => drop(t.id)}>
        <Icon name="x" size={14} />
      </button>
      <i className={`toast-life${paused ? ' paused' : ''}`} style={{ animationDuration: `${t.ttl}ms` }} aria-hidden="true" />
    </div>
  );
}

export function Toasts() {
  const toasts = useStore((s) => s.toasts);
  return (
    <div className="toasts" role="region" aria-label="Notifications" aria-live="polite">
      {toasts.map((t) => (
        <ToastItem key={`${t.id}-${t.count}`} t={t} />
      ))}
    </div>
  );
}

export function Banners() {
  const conn = useStore((s) => s.conn);
  const stale = useStore((s) => s.graphStale);
  const resyncing = useStore((s) => s.resyncing);
  const vs = useStore((s) => s.versionState);
  const meta = useStore((s) => s.meta);
  const dismissed = useStore((s) => s.versionBannerDismissed);
  const set = useStore((s) => s.set);

  return (
    <div className="banners">
      {(stale || conn === 'offline') && (
        <div className="banner warn" role="alert">
          {copy.staleBanner}
        </div>
      )}
      {resyncing && (
        <div className="banner" role="status">
          {copy.resyncing}
        </div>
      )}
      {vs === 'minor' && !dismissed && meta && (
        <div className="banner" role="status">
          <span>{copy.versionMinor(CONTRACT_VERSION, meta.contract_version)}</span>
          <button className="btn ghost" onClick={() => set({ versionBannerDismissed: true })}>
            {copy.dismiss}
          </button>
        </div>
      )}
    </div>
  );
}

export function VersionBlock() {
  const meta = useStore((s) => s.meta);
  return (
    <div className="blocking" role="alertdialog" aria-labelledby="vb-title">
      <h1 id="vb-title">{copy.versionBlock.title}</h1>
      <p>{copy.versionBlock.body(CONTRACT_VERSION, meta?.contract_version ?? 'unknown')}</p>
    </div>
  );
}

export function LoadError() {
  const e = useStore((s) => s.loadError);
  if (!e) return null;
  return (
    <div className="blocking soft" role="alert">
      <h1>{copy.errors.panel(e.endpoint)}</h1>
      <p>{e.message}</p>
      <button className="btn primary" onClick={() => void startSession()}>
        {copy.retry}
      </button>
    </div>
  );
}

export function Shortcuts() {
  const open = useStore((s) => s.shortcutsOpen);
  const set = useStore((s) => s.set);
  useEffect(() => {
    if (!open) return;
    return () => restoreFocus();
  }, [open]);
  if (!open) return null;
  return (
    <div className="modal-back" onClick={() => set({ shortcutsOpen: false })}>
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-label={copy.shortcuts.title}
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          if (e.key === 'Tab') e.preventDefault();
          if (e.key === 'Escape') set({ shortcutsOpen: false });
        }}
      >
        <h2>{copy.shortcuts.title}</h2>
        <dl className="keys">
          {copy.shortcuts.items.map(([k, d]) => (
            <div key={k}>
              <dt>
                <kbd>{k}</kbd>
              </dt>
              <dd>{d}</dd>
            </div>
          ))}
        </dl>
        <button className="btn" onClick={() => set({ shortcutsOpen: false })} autoFocus>
          {copy.dismiss}
        </button>
      </div>
    </div>
  );
}

const DEV_MIN = 72;
const DEV_KEY = 'quorum:devh';

function readDevHeight(): number {
  try {
    const v = Number(localStorage.getItem(DEV_KEY));
    return v >= DEV_MIN ? v : 128;
  } catch {
    return 128;
  }
}

const DevRow = memo(function DevRow({ e }: { e: DevLogEntry }) {
  return (
    <li className={e.level}>
      {fmtTime(e.ts)} {e.message}
    </li>
  );
});

export function DevConsole() {
  const open = useStore((s) => s.devOpen);
  const demo = useStore((s) => s.demo);
  const set = useStore((s) => s.set);
  const role = useStore((s) => s.role);
  const [entries, setEntries] = useState<DevLogEntry[]>([]);
  const [height, setHeight] = useState(readDevHeight);
  useEffect(() => subscribeDevLog(setEntries), []);
  useEffect(() => {
    document.documentElement.style.setProperty('--dock-h', open && !demo ? `${height}px` : '0px');
    return () => document.documentElement.style.setProperty('--dock-h', '0px');
  }, [open, demo, height]);
  const rows = useMemo(() => [...entries].reverse(), [entries]);

  const startDrag = (e: React.PointerEvent) => {
    e.preventDefault();
    const startY = e.clientY;
    const startH = height;
    const move = (ev: PointerEvent) => {
      const h = Math.round(Math.min(window.innerHeight * 0.5, Math.max(DEV_MIN, startH + (startY - ev.clientY))));
      setHeight(h);
    };
    const up = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      setHeight((h) => {
        try {
          localStorage.setItem(DEV_KEY, String(h));
        } catch {
          /* storage can be unavailable */
        }
        return h;
      });
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  };

  if (demo) return null;
  const errs = entries.filter((e) => e.level !== 'info').length;
  if (!open) {
    return (
      <button className="dev-toggle" aria-expanded={false} onClick={() => set({ devOpen: true })}>
        {copy.dev.title}
        {errs > 0 && <span className="dev-count num">{errs}</span>}
      </button>
    );
  }
  return (
    <section className="dev-drawer" aria-label={copy.dev.title} style={{ height }}>
      <div
        className="dev-resize"
        role="separator"
        aria-orientation="horizontal"
        aria-label="Resize the dev console"
        tabIndex={0}
        onPointerDown={startDrag}
        onKeyDown={(e) => {
          if (e.key === 'ArrowUp') setHeight((h) => Math.min(Math.round(window.innerHeight * 0.5), h + 24));
          if (e.key === 'ArrowDown') setHeight((h) => Math.max(DEV_MIN, h - 24));
        }}
      />
      <header>
        <strong>{copy.dev.title}</strong>
        <label className="dev-role">
          {copy.dev.actAs}
          <select value={role} onChange={(e) => setRole(e.target.value as Role)}>
            <option value="human">human</option>
            <option value="auditor">auditor</option>
            <option value="agent">agent</option>
          </select>
        </label>
        <span className="muted mono">
          tenant {session.tenant}, contract {CONTRACT_VERSION}, {API_MODE}
        </span>
        <button className="btn quiet grow" aria-expanded onClick={() => set({ devOpen: false })}>
          Close {copy.dev.title.toLowerCase()}
        </button>
      </header>
      {rows.length === 0 && <p className="muted dev-empty">{copy.dev.empty}</p>}
      <ol className="dev-log mono">
        {rows.map((e) => (
          <DevRow key={e.id} e={e} />
        ))}
      </ol>
    </section>
  );
}
