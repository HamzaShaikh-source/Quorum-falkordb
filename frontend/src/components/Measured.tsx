import short from '../../../bench/token_bench_short.json';
import long from '../../../bench/token_bench_long.json';
import { fmt } from '../context/tokens';

interface Scenario {
  label: string;
  detail: string;
  r: typeof short;
}

const SCENARIOS: Scenario[] = [
  { label: 'Short facts', detail: 'one or two sentences each', r: short },
  { label: 'Real-sized documents', detail: 'about 120 words each', r: long },
];

const sign = (n: number) => (n > 0 ? `${n}% fewer` : n < 0 ? `${-n}% more` : 'no change');

/**
 * Real Claude Code sessions, not an estimate: the same five questions from the same 160-fact memory, with and without
 * Quorum's compact context. Read straight from bench/token_bench_*.json at build time (see scripts/token_bench.mjs).
 */
export function Measured({ dark = false }: { dark?: boolean }) {
  return (
    <div className={`measured${dark ? ' dark' : ''}`}>
      <p className="measured-h">
        <b>Measured with a real AI.</b> Fresh Claude Code sessions answered the same five questions from the same 160-fact memory, {short.runs_per_arm} runs per arm, once with a typical top-8 retrieval and once with Quorum&apos;s compact
        format. Every arm answered {short.summary.plain.correct_median}/5 correctly.
      </p>
      <div className="cn-table-wrap" tabIndex={0} role="region" aria-label="Measured token savings with a real AI (scrolls sideways on a narrow screen)">
        <table className="measured-t">
          <thead>
            <tr>
              <th scope="col">Memory</th>
              <th scope="col">Tool-result tokens per run</th>
              <th scope="col">Whole session, input tokens</th>
              <th scope="col">Session cost</th>
            </tr>
          </thead>
          <tbody>
            {SCENARIOS.map(({ label, detail, r }) => (
              <tr key={label}>
                <th scope="row">
                  {label}
                  <small>{detail}</small>
                </th>
                <td>
                  <b className="num">
                    {fmt(r.summary.plain.tool_result_tokens_per_run)} → {fmt(r.summary.compact.tool_result_tokens_per_run)}
                  </b>
                  <small>{sign(r.versus_plain.compact.tool_result_saving_pct)}</small>
                </td>
                <td>
                  <b className="num">
                    {fmt(r.summary.plain.session_input_tokens_median)} → {fmt(r.summary.compact.session_input_tokens_median)}
                  </b>
                  <small>{sign(r.versus_plain.compact.session_input_saving_pct)}</small>
                </td>
                <td>
                  <b className="num">
                    ${r.summary.plain.cost_usd_median.toFixed(3)} → ${r.summary.compact.cost_usd_median.toFixed(3)}
                  </b>
                  <small>{sign(r.versus_plain.compact.cost_saving_pct)}</small>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="measured-f">
        What Quorum controls is the tool results, and those shrink a lot. The whole-session numbers move less because Claude Code sends its own fixed prompt (about 37,000 tokens) on every turn, which no memory can change. Adding a{' '}
        <code>get_map</code> call made the short run use {Math.abs(short.versus_plain.mapped.session_input_saving_pct)}% more, because the extra turn costs more than the map saves, so the shipped rules call it only when the agent does not know where to look.
        Token counts for tool results are Quorum&apos;s estimates; session totals and cost are what the <code>claude</code> CLI reported.
      </p>
    </div>
  );
}
