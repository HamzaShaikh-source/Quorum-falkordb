import { useState } from 'react';
import guide from '../../../backend/app/services/guide.json';
import { useStore } from '../state/store';
import { copyText } from '../workspace/clipboard';
import { Icon } from './Icon';
import './network.css';

type Topic = { id: string; title: string; summary: string; steps: string[]; say: string[]; tools: string[] };
const TOPICS = guide.topics as Topic[];
const FIRST = TOPICS[0] as Topic;

/** "How to use Quorum": the same text the quorum_guide chat tool returns, read from the one guide.json, so the console and the chat cannot disagree. */
export function UseGuide() {
  const toast = useStore((s) => s.pushToast);
  const [id, setId] = useState(FIRST.id);
  const topic = TOPICS.find((t) => t.id === id) ?? FIRST;
  const ask = (phrase: string) => void copyText(phrase).then((ok) => toast(ok ? 'Copied. Say it to your AI.' : 'Copy was blocked by the browser', ok ? 'good' : 'warn'));
  return (
    <section className="card wide use-guide" aria-label="How to use Quorum">
      <header className="card-h">
        <h2>How to use Quorum</h2>
        <span className="card-sub">Ask your AI for any of this; it can read the same text with quorum_guide</span>
      </header>
      <p className="cn-help">{guide.intro}</p>
      <div className="seg ug-tabs" role="tablist" aria-label="Guide topics">
        {TOPICS.map((t) => (
          <button key={t.id} role="tab" aria-selected={t.id === topic.id} className={`seg-btn${t.id === topic.id ? ' on' : ''}`} onClick={() => setId(t.id)}>
            {t.title}
          </button>
        ))}
      </div>
      <div className="ug-body" role="tabpanel" aria-label={topic.title}>
        <p className="ug-sum">{topic.summary}</p>
        <ol className="ug-steps">
          {topic.steps.map((s) => (
            <li key={s}>{s}</li>
          ))}
        </ol>
        {topic.say.length > 0 && (
          <div className="ug-say">
            <b>Things to say to your AI</b>
            <ul>
              {topic.say.map((s) => (
                <li key={s}>
                  <button className="btn quiet" onClick={() => ask(s)} title="Copy this phrase">
                    <Icon name="copy" size={12} /> {s}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
        {topic.tools.length > 0 && (
          <p className="muted small">
            Tools behind this: {topic.tools.map((t) => (
              <code key={t} className="mono">{t} </code>
            ))}
          </p>
        )}
      </div>
      <p className="cn-help">
        To make your AI follow the working agreement in every future session, ask it: <code className="mono">call quorum_guide with topic install and tell it which AI you are</code>. It writes the right file for Claude Code, Cursor, VS Code, Windsurf, Antigravity, Claude Desktop or ChatGPT.
      </p>
    </section>
  );
}
