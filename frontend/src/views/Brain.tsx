import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { copy } from '../copy';
import { Icon } from '../components/Icon';
import { Chip, Empty, ErrorState, fmtDateTime, Skeleton } from '../components/ui';
import { safeUrl } from '../api/safeUrl';
import { askBrain, getTrace, getWhoKnows } from '../state/endpoints';
import { useStore } from '../state/store';
import { Avatar, PageHead } from './Memory';

function Ask({ prefill }: { prefill: string | null }) {
  const [q, setQ] = useState(prefill ?? '');
  const ask = useMutation({ mutationFn: (question: string) => askBrain(question) });

  const asked = useRef(false);
  useEffect(() => {
    if (prefill && !asked.current) {
      asked.current = true;
      ask.mutate(prefill);
    }
  }, [prefill, ask]);

  return (
    <section className="card ask" aria-label={copy.brain.title}>
      <form
        className="askform"
        onSubmit={(e) => {
          e.preventDefault();
          if (q.trim()) ask.mutate(q.trim());
        }}
      >
        <Icon name="search" size={18} />
        <label className="sr" htmlFor="ask-q">
          {copy.brain.title}
        </label>
        <input id="ask-q" value={q} onChange={(e) => setQ(e.target.value)} placeholder={copy.brain.placeholder} />
        <button className="btn primary" disabled={ask.isPending || !q.trim()}>
          {copy.brain.ask}
        </button>
      </form>
      <div className="examples">
        {copy.brain.examples.map((x) => (
          <button
            key={x}
            className="chip-btn"
            onClick={() => {
              setQ(x);
              ask.mutate(x);
            }}
          >
            {x}
          </button>
        ))}
      </div>
      {ask.isPending && <Skeleton lines={3} />}
      {ask.isError && <ErrorState error={ask.error} onRetry={() => ask.mutate(q)} />}
      {ask.data?.refused && (
        <div className="answer refused" role="status">
          <div className="answer-tag">
            <Icon name="shield" size={15} />
            <Chip tone="bad">{copy.brain.refused}</Chip>
          </div>
          <h3>{copy.brain.refusedWhy}</h3>
          <p>{ask.data.reason}</p>
        </div>
      )}
      {ask.data && !ask.data.refused && (
        <div className="answer" role="status">
          <p className="answer-text">{ask.data.answer}</p>
          <h3 className="sub">{copy.brain.citations}</h3>
          <ol className="cites">
            {ask.data.citations.map((c, i) => (
              <li key={i} className={c.stale ? 'old' : ''}>
                <div className="cite-head">
                  <Icon name="doc" size={16} />
                  <strong>{c.source_title}</strong>
                  <span className="muted">
                    {copy.brain.version} {c.version}
                  </span>
                  <Chip tone={c.stale ? 'warn' : 'good'}>{c.stale ? copy.brain.outOfDate : copy.brain.current}</Chip>
                </div>
                {c.quote && <blockquote>{c.quote}</blockquote>}
                <p className="muted small">
                  {copy.brain.owner}: {c.owner_team}. <span className="num">{fmtDateTime(c.valid_from)}</span>{' '}
                  {safeUrl(c.url) && (
                    <a href={safeUrl(c.url) as string} target="_blank" rel="noreferrer noopener">
                      Open source
                    </a>
                  )}
                </p>
              </li>
            ))}
          </ol>
        </div>
      )}
    </section>
  );
}

function WhoKnows() {
  const [topic, setTopic] = useState('refund policy');
  const [committed, setCommitted] = useState('refund policy');
  const q = useQuery({ queryKey: ['who-knows', committed], queryFn: () => getWhoKnows(committed) });
  const max = Math.max(1, ...(q.data ?? []).map((p) => p.evidence_count));
  return (
    <section className="card" aria-label={copy.brain.whoKnows}>
      <header className="card-h">
        <h2>{copy.brain.whoKnows}</h2>
      </header>
      <form
        className="inline-form"
        onSubmit={(e) => {
          e.preventDefault();
          setCommitted(topic);
        }}
      >
        <label className="sr" htmlFor="topic">
          {copy.brain.topic}
        </label>
        <input id="topic" value={topic} onChange={(e) => setTopic(e.target.value)} />
        <button className="btn">{copy.brain.lookup}</button>
      </form>
      {q.isLoading && <Skeleton lines={3} />}
      {q.isError && <ErrorState error={q.error} onRetry={() => void q.refetch()} />}
      {q.data && q.data.length === 0 && <Empty>{copy.brain.traceNone}</Empty>}
      <ol className="board">
        {q.data?.map((p, i) => (
          <li key={p.person}>
            <Avatar name={p.person} tier={i === 0 ? 'root' : 'established'} />
            <div className="board-main">
              <div className="board-head">
                <strong>{p.person}</strong>
                <span className="muted small">{p.team}</span>
                <span className="num board-n">{copy.brain.evidence(p.evidence_count)}</span>
              </div>
              <div className="rankbar">
                <i style={{ width: `${(p.evidence_count / max) * 100}%` }} />
              </div>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}

function Trace() {
  const nodes = useStore((s) => s.nodes);
  const decisions = Object.values(nodes).filter((n) => n.is_decision);
  const [id, setId] = useState('');
  const current = decisions.some((decision) => decision.id === id) ? id : decisions[0]?.id || '';
  const q = useQuery({ queryKey: ['trace', current], queryFn: () => getTrace(current), enabled: !!current });
  return (
    <section className="card" aria-label={copy.brain.traceTitle}>
      <header className="card-h">
        <h2>{copy.brain.traceTitle}</h2>
      </header>
      {decisions.length === 0 && <Empty>{copy.brain.traceEmpty}</Empty>}
      {decisions.length > 0 && (
        <label className="field">
          <span>{copy.brain.pickDecision}</span>
          <select value={current} onChange={(e) => setId(e.target.value)}>
            {decisions.map((d) => (
              <option key={d.id} value={d.id}>
                {d.label}
              </option>
            ))}
          </select>
        </label>
      )}
      {q.isLoading && current && <Skeleton lines={3} />}
      {q.isError && <ErrorState error={q.error} onRetry={() => void q.refetch()} />}
      {q.data && q.data.length === 0 && <Empty>{copy.brain.traceNone}</Empty>}
      <ol className="trace">
        {q.data?.map((r, i) => (
          <li key={i}>
            <span className="trace-dot">
              <Icon name="link" size={13} />
            </span>
            <div>
              <strong>{r.claim}</strong>
              {r.quote && <blockquote>{r.quote}</blockquote>}
              <p className="muted small">
                {r.source_title}, {copy.brain.version.toLowerCase()} {r.version}. {copy.brain.owner}: {r.owner}.{' '}
                {safeUrl(r.url) ? (
                  <a href={safeUrl(r.url) as string} target="_blank" rel="noreferrer noopener">
                    Open source
                  </a>
                ) : (
                  <span className="muted">No link</span>
                )}
              </p>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}

export function Brain() {
  const prefill = useStore((s) => s.brainQuestion);
  return (
    <div className="page" tabIndex={0}>
      <PageHead page="brain" />
      <Ask key={prefill ?? ''} prefill={prefill} />
      <div className="grid-2">
        <WhoKnows />
        <Trace />
      </div>
    </div>
  );
}
