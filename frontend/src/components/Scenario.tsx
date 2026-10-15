import { useState } from 'react';
import { ApiError } from '../api/client';
import { copy } from '../copy';
import { startAutopilot, stopAutopilot } from '../state/autopilot';
import { runStep } from '../state/endpoints';
import { useStore } from '../state/store';
import { Icon } from './Icon';

const STORY = ['inject_poison', 'run_agents', 'audit', 'supersede'] as const;
const EXTRA = ['restore', 'reset'] as const;

export function ScenarioControls() {
  const scenario = useStore((s) => s.scenario);
  const eventLog = useStore((s) => s.eventLog);
  const autopilot = useStore((s) => s.autopilot);
  const pushToast = useStore((s) => s.pushToast);
  const [pending, setPending] = useState<string | null>(null);
  const active = scenario.active ?? pending;

  const done = new Set(eventLog.filter((e) => e.type === 'scenario_progress' && e.state === 'finished').map((e) => e.step ?? ''));
  const next = STORY.find((s) => !done.has(s));

  async function go(step: string) {
    setPending(step);
    try {
      await runStep(step);
    } catch (e) {
      if (e instanceof ApiError && e.code === 'FORBIDDEN_ROLE') pushToast(copy.errors.forbiddenRoleScenario);
      else pushToast(copy.errors.generic(e instanceof ApiError ? e.code : 'ERROR', e instanceof ApiError ? e.message : 'Request failed'));
    } finally {
      setPending(null);
    }
  }

  const failed = !active && scenario.lastState === 'failed' && scenario.detail;

  return (
    <section className="card scenario" aria-label={copy.scenario.title}>
      <header className="card-h">
        <h2>{copy.scenario.title}</h2>
        <span className="card-sub">{copy.scenario.subtitle}</span>
      </header>
      <ol className="story">
        {STORY.map((s, i) => {
          const isDone = done.has(s);
          const isRun = active === s;
          const isNext = !active && next === s;
          return (
            <li key={s} className={`story-step${isDone ? ' done' : ''}${isRun ? ' run' : ''}${isNext ? ' next' : ''}`}>
              <span className="story-no" aria-hidden="true">
                {isDone ? <Icon name="check" size={13} /> : isRun ? <span className="spin" /> : i + 1}
              </span>
              <div className="story-body">
                <button
                  className={`story-btn${isNext ? ' primary' : ''}`}
                  disabled={!!active || autopilot}
                  onClick={() => void go(s)}
                  aria-label={copy.scenario.steps[s]}
                >
                  {copy.scenario.steps[s]}
                </button>
                <p>{copy.scenario.hints[s]}</p>
              </div>
            </li>
          );
        })}
      </ol>
      <div className="scenario-extra">
        {EXTRA.map((s) => (
          <button key={s} className="btn quiet" disabled={!!active || autopilot} onClick={() => void go(s)} title={copy.scenario.hints[s]}>
            <Icon name={s === 'reset' ? 'refresh' : 'rollback'} size={14} />
            {copy.scenario.steps[s]}
          </button>
        ))}
      </div>
      <p className={`progress${failed ? ' fail' : ''}`} role="status" aria-live="polite">
        {active
          ? copy.scenario.running(copy.scenario.steps[active]?.toLowerCase() ?? active)
          : failed
            ? copy.scenario.failed(String(scenario.detail))
            : ' '}
      </p>
      <button className={`autopilot${autopilot ? ' on' : ''}`} onClick={() => (autopilot ? stopAutopilot() : void startAutopilot())} title={copy.scenario.autopilotHint}>
        <Icon name={autopilot ? 'pause' : 'play'} size={15} />
        {autopilot ? copy.scenario.autopilotStop : copy.scenario.autopilot}
        <span className="autopilot-hint">{autopilot ? 'Hands off, it is running the story' : '90 second walkthrough'}</span>
      </button>
    </section>
  );
}
