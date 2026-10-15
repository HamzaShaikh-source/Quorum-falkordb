import { useEffect, useMemo, useState } from 'react';
import { copy } from '../copy';
import { GLOSSARY_SORTED } from '../copy/glossary';
import { useStore } from '../state/store';
import { viewKey } from '../state/views';
import { MCP_TOOLS, WHO_LABEL } from '../workspace/mcp';
import { restoreFocus } from './focus';
import { Icon } from './Icon';

type Tab = 'ai' | 'glossary' | 'keys';

const TABS: { id: Tab; label: string }[] = [
  { id: 'ai', label: 'What the AI does here' },
  { id: 'glossary', label: 'A to Z glossary' },
  { id: 'keys', label: 'Shortcuts' },
];

function WhatTheAiDoes() {
  const reads = MCP_TOOLS.filter((t) => !t.writes);
  const writes = MCP_TOOLS.filter((t) => t.writes);
  const group = (title: string, blurb: string, list: typeof MCP_TOOLS) => (
    <section className="gd-group">
      <h3>{title}</h3>
      <p className="muted">{blurb}</p>
      <ul className="gd-tools">
        {list.map((t) => (
          <li key={t.name}>
            <div>
              <b>{t.summary}</b>
              <code>{t.name}</code>
            </div>
            <span className="chip">{WHO_LABEL[t.who]}</span>
          </li>
        ))}
      </ul>
    </section>
  );
  return (
    <div>
      <p className="gd-lead">
        Quorum is shared memory for your AI assistants. They use it for exactly two things: <b>reading</b> what the team already knows, and <b>writing</b> down what they learn. People use this console to watch, and to step in when something is wrong.
      </p>
      {group('Reading (changes nothing)', 'Every one of these shows up in the activity log as "looked up" or similar.', reads)}
      {group('Writing and safety (changes memory)', 'These are the steps worth watching. Each one is attributed to a person or agent token.', writes)}
    </div>
  );
}

function Glossary() {
  const [q, setQ] = useState('');
  const set = useStore((s) => s.set);
  const story = useStore((s) => s.story);
  const rows = useMemo(() => {
    const n = q.trim().toLowerCase();
    return n ? GLOSSARY_SORTED.filter((t) => t.term.toLowerCase().includes(n) || t.plain.toLowerCase().includes(n)) : GLOSSARY_SORTED;
  }, [q]);
  const letters = useMemo(() => Array.from(new Set(rows.map((t) => t.term[0]!.toUpperCase()))), [rows]);
  const firsts = useMemo(() => new Set(rows.filter((t, i) => i === 0 || rows[i - 1]!.term[0]!.toUpperCase() !== t.term[0]!.toUpperCase()).map((t) => t.term)), [rows]);
  return (
    <div>
      <label className="field gd-search">
        <span className="sr">Search the glossary</span>
        <input type="search" placeholder="Search a word, for example quarantine" value={q} onChange={(e) => setQ(e.target.value)} />
      </label>
      <nav className="gd-az" aria-label="Jump to letter">
        {letters.map((l) => (
          <a key={l} href={`#gl-${l}`} onClick={(e) => {
              e.preventDefault();
              document.getElementById(`gl-${l}`)?.scrollIntoView({ block: 'start' });
            }}>
            {l}
          </a>
        ))}
      </nav>
      {rows.length === 0 && <p className="muted">No word matches that. Try a shorter search.</p>}
      <dl className="gd-terms">
        {rows.map((t) => {
          const l = t.term[0]!.toUpperCase();
          const head = firsts.has(t.term);
          return (
            <div key={t.term} className="gd-term">
              {head && (
                <div id={`gl-${l}`} className="gd-letter" aria-hidden="true">
                  {l}
                </div>
              )}
              <dt>{t.term}</dt>
              <dd>
                {t.plain}
                {t.see && (
                  <button
                    className="btn quiet gd-see"
                    onClick={() => set({ view: t.see!, helpOpen: false })}
                    title={viewKey(t.see, story) ? `Shortcut: ${viewKey(t.see, story)}` : undefined}
                  >
                    See it in {copy.nav[t.see]}
                  </button>
                )}
              </dd>
            </div>
          );
        })}
      </dl>
    </div>
  );
}

function Keys() {
  return (
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
  );
}

/** The always-available help drawer (button in the header, or press G). Three things a new user needs, in one place. */
export function Guide() {
  const open = useStore((s) => s.helpOpen);
  const set = useStore((s) => s.set);
  const [tab, setTab] = useState<Tab>('ai');
  useEffect(() => {
    if (!open) return;
    return () => restoreFocus();
  }, [open]);
  if (!open) return null;
  return (
    <div className="modal-back guide-back" onClick={() => set({ helpOpen: false })}>
      <aside
        className="guide"
        role="dialog"
        aria-modal="true"
        aria-label="Guide"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          if (e.key === 'Escape') set({ helpOpen: false });
        }}
      >
        <header className="gd-head">
          <h2>Guide</h2>
          <button className="iconbtn" aria-label="Close the guide" onClick={() => set({ helpOpen: false })} autoFocus>
            <Icon name="x" size={16} />
          </button>
        </header>
        <div className="seg gd-tabs" role="tablist" aria-label="Guide sections">
          {TABS.map((t) => (
            <button key={t.id} role="tab" aria-selected={tab === t.id} className={`seg-btn${tab === t.id ? ' on' : ''}`} onClick={() => setTab(t.id)}>
              {t.label}
            </button>
          ))}
        </div>
        <div className="gd-body" role="tabpanel">
          {tab === 'ai' && <WhatTheAiDoes />}
          {tab === 'glossary' && <Glossary />}
          {tab === 'keys' && <Keys />}
        </div>
      </aside>
    </div>
  );
}
