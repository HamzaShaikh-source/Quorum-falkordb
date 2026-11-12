import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { copy } from '../copy';
import { graphBus } from '../graph/bus';
import { getAgents, getPromptDiff } from '../state/endpoints';
import { useStore } from '../state/store';
import { Chip, Empty, ErrorState, Skeleton } from './ui';

export function PromptDiff() {
  const [agent, setAgent] = useState('agent_refunder');
  const nodes = useStore((s) => s.nodes);
  const set = useStore((s) => s.set);
  const agents = useQuery({ queryKey: ['agents'], queryFn: getAgents });
  const q = useQuery({ queryKey: ['prompt-diff', agent], queryFn: () => getPromptDiff(agent) });

  const focus = (id: string) => {
    set({ selectedId: id, tab: 'graph' });
    graphBus.focus.emit(id);
  };

  return (
    <section className="panel diff" aria-label={copy.diff.title}>
      <div className="panel-top">
        <h2 className="panel-h">{copy.diff.title}</h2>
        <label>
          <span className="sr">{copy.diff.agent}</span>
          <select value={agent} onChange={(e) => setAgent(e.target.value)} aria-label={copy.diff.agent}>
            {(agents.data ?? [{ id: agent, name: agent }]).map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </select>
        </label>
      </div>
      {q.isLoading && <Skeleton lines={6} />}
      {q.isError && <ErrorState error={q.error} onRetry={() => void q.refetch()} />}
      {q.data && q.data.before.length === 0 && <Empty>{copy.diff.empty}</Empty>}
      {q.data && q.data.before.length > 0 && (
        <>
          <p className="diff-epochs">
            <span className="num">{copy.diff.epochs(q.data.epoch_before, q.data.epoch_after)}</span>
            {q.data.removed_ids.length > 0 ? (
              <strong className="guarantee">{copy.diff.guarantee}</strong>
            ) : (
              <span className="muted">{copy.diff.nothing}</span>
            )}
          </p>
          <div className="diff-cols">
            <div>
              <h3 className="sub">{copy.diff.before}</h3>
              <ul className="code">
                {[...q.data.before].sort((a, b) => Number(q.data.removed_ids.includes(b.node_id)) - Number(q.data.removed_ids.includes(a.node_id))).map((l) => {
                  const removed = q.data.removed_ids.includes(l.node_id);
                  return (
                    <li key={l.node_id} className={removed ? 'removed' : ''}>
                      <span className="sign" aria-hidden="true">{removed ? '-' : ' '}</span>
                      <span className="line">{l.text}</span>
                      <button className="nodechip" onClick={() => focus(l.node_id)}>
                        {nodes[l.node_id]?.label ?? l.node_id}
                      </button>
                      {removed && <Chip tone="bad">{copy.diff.removedLine}</Chip>}
                    </li>
                  );
                })}
              </ul>
            </div>
            <div>
              <h3 className="sub">{copy.diff.after}</h3>
              {q.data.after.length === 0 && <Empty>{copy.diff.noneLeft}</Empty>}
              <ul className="code">
                {q.data.after.map((l) => (
                  <li key={l.node_id}>
                    <span className="sign" aria-hidden="true"> </span>
                    <span className="line">{l.text}</span>
                    <button className="nodechip" onClick={() => focus(l.node_id)}>
                      {nodes[l.node_id]?.label ?? l.node_id}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </>
      )}
    </section>
  );
}
