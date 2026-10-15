import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { copy } from '../copy';
import { Icon } from '../components/Icon';
import { Chip, Empty, ErrorState, fmtDateTime, Skeleton } from '../components/ui';
import { getAgents, getProcedures, getResume } from '../state/endpoints';

function PageHead({ page }: { page: keyof typeof copy.pages }) {
  return (
    <header className="page-head">
      <p className="page-tag">
        <span>[{copy.pages[page].tag}]</span> {page === 'bench' ? 'Benchmark' : copy.nav[page]}
      </p>
      <h1>{copy.pages[page].title}</h1>
      <p>{copy.pages[page].sub}</p>
    </header>
  );
}

function Avatar({ name, tier }: { name: string; tier: string }) {
  return <span className={`avatar tier-${tier}`}>{name.slice(0, 1)}</span>;
}

function Agents({ selected, onPick }: { selected: string | null; onPick: (id: string) => void }) {
  const q = useQuery({ queryKey: ['agents'], queryFn: getAgents });
  return (
    <section className="card" aria-label={copy.memory.agents}>
      <header className="card-h">
        <h2>{copy.memory.agents}</h2>
        {q.data && <span className="card-sub">{q.data.length}</span>}
      </header>
      {q.isLoading && <Skeleton lines={4} />}
      {q.isError && <ErrorState error={q.error} onRetry={() => void q.refetch()} />}
      {q.data && q.data.length === 0 && <Empty>{copy.memory.noAgents}</Empty>}
      <ul className="agent-list">
        {q.data?.map((a) => (
          <li key={a.id}>
            <button className={`agent-row${selected === a.id ? ' on' : ''}`} aria-pressed={selected === a.id} onClick={() => onPick(a.id)}>
              <Avatar name={a.name} tier={a.tier} />
              <span className="agent-main">
                <strong>{a.name}</strong>
                <span className="muted">{a.role}</span>
              </span>
              <Chip tone={a.tier === 'root' ? 'good' : a.tier === 'new' ? 'warn' : 'neutral'}>{copy.memory.tier[a.tier] ?? a.tier}</Chip>
              {a.last_active && <span className="muted small agent-time num">{fmtDateTime(a.last_active)}</span>}
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

function Procedures() {
  const q = useQuery({ queryKey: ['procedures'], queryFn: getProcedures });
  const list = [...(q.data ?? [])].sort((a, b) => b.rank_score - a.rank_score);
  return (
    <section className="card" aria-label={copy.memory.procedures}>
      <header className="card-h">
        <h2>{copy.memory.procedures}</h2>
        <span className="card-sub">Ranked by Laplace-smoothed success</span>
      </header>
      {q.isLoading && <Skeleton lines={4} />}
      {q.isError && <ErrorState error={q.error} onRetry={() => void q.refetch()} />}
      {q.data && q.data.length === 0 && <Empty>{copy.memory.noAgents}</Empty>}
      <ol className="procs">
        {list.map((p, i) => (
          <li key={p.id} className={i === 0 ? 'top' : ''}>
            <div className="proc-head">
              <span className="proc-rank num">{i + 1}</span>
              <div className="proc-title">
                <strong>{p.task_type.charAt(0).toUpperCase() + p.task_type.slice(1)} procedure</strong>
                <span className="mono muted">{p.id}</span>
              </div>
              {i === 0 && <Chip tone="good">Top ranked</Chip>}
              <Chip tone={p.seeded ? 'neutral' : 'review'}>{p.seeded ? copy.memory.seeded : copy.memory.learned}</Chip>
            </div>
            <ol className="steps-list">
              {p.steps.map((s) => (
                <li key={s}>{s}</li>
              ))}
            </ol>
            <div className="rank-row">
              <div className="rankbar" role="img" aria-label={`${copy.memory.rank} ${p.rank_score.toFixed(2)}`}>
                <i style={{ width: `${Math.round(p.rank_score * 100)}%` }} />
              </div>
              <span className="rank-n num">{p.rank_score.toFixed(2)}</span>
            </div>
            <div className="outcome" role="img" aria-label={`${p.successes} successes and ${p.failures} failures`}>
              <i className="win" style={{ flexGrow: p.successes }} />
              <i className="loss" style={{ flexGrow: p.failures }} />
            </div>
            <p className="muted small num">
              {p.uses} {copy.memory.uses}. {p.successes} {copy.memory.wins}, {p.failures} {copy.memory.losses}.
            </p>
          </li>
        ))}
      </ol>
    </section>
  );
}

function Resume({ agent }: { agent: string | null }) {
  const [shown, setShown] = useState<string | null>(null);
  const q = useQuery({ queryKey: ['resume', shown], queryFn: () => getResume(shown as string), enabled: !!shown });
  return (
    <section className="card" aria-label={copy.memory.resumeTitle}>
      <header className="card-h">
        <h2>{copy.memory.resumeTitle}</h2>
        <span className="card-sub">{copy.memory.resumeSub}</span>
      </header>
      {!agent && <Empty>{copy.memory.resumeEmpty}</Empty>}
      {agent && (
        <button className="btn primary" onClick={() => setShown(agent)}>
          <Icon name="refresh" size={14} />
          {copy.memory.resumeButton}
        </button>
      )}
      {shown && q.isLoading && <Skeleton lines={4} />}
      {shown && q.isError && <ErrorState error={q.error} onRetry={() => void q.refetch()} />}
      {shown && q.data && (
        <>
          <p className="summary">{q.data.summary}</p>
          <ul className="memories">
            {q.data.memories.map((m) => (
              <li key={m.fact_id}>
                <span className="mem-dot" />
                <div>
                  <strong>{m.text}</strong>
                  {m.quote && <blockquote>{m.quote}</blockquote>}
                  <p className="muted small">
                    {copy.memory.source}: <span className="mono">{m.source_ref}</span>
                  </p>
                </div>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

export function Memory() {
  const [agent, setAgent] = useState<string | null>('agent_refunder');
  return (
    <div className="page" tabIndex={0}>
      <PageHead page="memory" />
      <div className="grid-3">
        <Agents selected={agent} onPick={setAgent} />
        <Procedures />
        <Resume key={agent} agent={agent} />
      </div>
    </div>
  );
}

export { PageHead, Avatar };
