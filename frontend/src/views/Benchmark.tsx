import { useQuery } from '@tanstack/react-query';
import type { BenchResults } from '../api/schemas';
import { copy } from '../copy';
import { Empty, ErrorState, fmtDateTime, Skeleton } from '../components/ui';
import { getBench } from '../state/endpoints';
import { PageHead } from './Memory';

const SYS = ['var(--ok)', 'var(--warn)', 'var(--ghost)'];

function Kpi({ label, value, unit, hint }: { label: string; value: string; unit?: string; hint?: string }) {
  return (
    <div className="kpi">
      <span className="kpi-l">{label}</span>
      <span className="kpi-v num">
        {value}
        {unit && <small>{unit}</small>}
      </span>
      {hint && <span className="kpi-h">{hint}</span>}
    </div>
  );
}

function Latency({ c }: { c: BenchResults['concurrency'] }) {
  const rows = [
    ['p50', c.p50_ms],
    ['p95', c.p95_ms],
    ['p99', c.p99_ms],
  ] as const;
  const max = Math.max(c.p99_ms, 1);
  return (
    <svg className="chart" viewBox="0 0 420 150" role="img" aria-label="Latency percentiles in milliseconds">
      {rows.map(([name, v], i) => {
        const w = (v / max) * 300;
        return (
          <g key={name} transform={`translate(0 ${12 + i * 42})`}>
            <text x="0" y="20" className="axis">
              {name}
            </text>
            <rect x="40" y="4" width="320" height="24" rx="5" className="track" />
            <rect x="40" y="4" width={Math.max(6, w)} height="24" rx="5" fill={i === 2 ? 'var(--warn)' : 'var(--ok)'} />
            <text x={46 + Math.max(6, w)} y="21" className="val num">
              {v} ms
            </text>
          </g>
        );
      })}
    </svg>
  );
}

function PoisonChart({ data }: { data: BenchResults['poison'] }) {
  const systems = data[0]?.systems.map((s) => s.name) ?? [];
  const groupW = 150;
  const H = 170;
  return (
    <div>
      <svg className="chart" viewBox={`0 0 ${data.length * groupW + 50} ${H + 46}`} role="img" aria-label="Leaked rate by poison depth and system">
        {[0, 0.5, 1].map((t) => (
          <g key={t}>
            <line x1="38" x2={data.length * groupW + 44} y1={H - t * (H - 20)} y2={H - t * (H - 20)} className="gridline" />
            <text x="30" y={H - t * (H - 20) + 4} textAnchor="end" className="axis">
              {Math.round(t * 100)}%
            </text>
          </g>
        ))}
        {data.map((d, gi) => (
          <g key={d.depth} transform={`translate(${44 + gi * groupW} 0)`}>
            {d.systems.map((s, si) => {
              const h = Math.max(2, s.leaked_rate * (H - 20));
              return (
                <g key={s.name}>
                  <rect x={8 + si * 38} y={H - h} width="30" height={h} rx="4" fill={SYS[si % SYS.length]} />
                  <text x={23 + si * 38} y={H - h - 5} textAnchor="middle" className="val num">
                    {Math.round(s.leaked_rate * 100)}
                  </text>
                </g>
              );
            })}
            <text x="62" y={H + 20} textAnchor="middle" className="axis">
              {d.depth} {d.depth === 1 ? 'hop' : 'hops'}
            </text>
          </g>
        ))}
      </svg>
      <ul className="key">
        {systems.map((s, i) => (
          <li key={s}>
            <i style={{ background: SYS[i % SYS.length] }} />
            {s}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function Benchmark() {
  const q = useQuery({ queryKey: ['bench'], queryFn: getBench });
  const d = q.data;
  return (
    <div className="page" tabIndex={0}>
      <PageHead page="bench" />
      {q.isLoading && <Skeleton lines={8} />}
      {q.isError && <ErrorState error={q.error} onRetry={() => void q.refetch()} />}
      {d && (
        <>
          <div className="kpis">
            <Kpi label="Throughput" value={d.concurrency.ops_per_s.toLocaleString()} unit=" ops/s" hint={`${d.concurrency.agents} agents, mixed reads and writes`} />
            <Kpi label="p99 latency" value={String(d.concurrency.p99_ms)} unit=" ms" hint={`p50 ${d.concurrency.p50_ms} ms`} />
            <Kpi label={copy.bench.singleWinner} value={`${Math.round(d.handoff_race.single_winner_rate * 100)}`} unit="%" hint={`${d.handoff_race.handoffs} handoffs, ${d.handoff_race.claimers} claimers each`} />
            <Kpi label={copy.bench.invalidation} value={String(d.invalidation_under_load.violations)} hint={copy.bench.violations(d.invalidation_under_load.violations)} />
          </div>
          <div className="grid-2">
            <section className="card" aria-label={copy.bench.concurrency}>
              <header className="card-h">
                <h2>{copy.bench.concurrency}</h2>
                <span className="card-sub">Latency percentiles</span>
              </header>
              <Latency c={d.concurrency} />
            </section>
            <section className="card" aria-label={copy.bench.poison}>
              <header className="card-h">
                <h2>{copy.bench.poison}</h2>
                <span className="card-sub">{copy.bench.leaked}, lower is better</span>
              </header>
              {d.poison.length ? <PoisonChart data={d.poison} /> : <Empty>{copy.bench.empty}</Empty>}
            </section>
          </div>
          {d.poison.length > 0 && (
            <section className="card" aria-label="Containment table">
              <header className="card-h">
                <h2>{copy.bench.ttc}</h2>
                <span className="card-sub">
                  {copy.bench.generated} <span className="num">{fmtDateTime(d.generated_at)}</span>
                </span>
              </header>
              <table className="table">
                <thead>
                  <tr>
                    <th>Depth</th>
                    <th>System</th>
                    <th>{copy.bench.leaked}</th>
                    <th>{copy.bench.wrong}</th>
                    <th>{copy.bench.ttc}</th>
                  </tr>
                </thead>
                <tbody>
                  {d.poison.flatMap((p) =>
                    p.systems.map((s) => (
                      <tr key={`${p.depth}-${s.name}`}>
                        <td className="num">{p.depth}</td>
                        <td>{s.name}</td>
                        <td className="num">{Math.round(s.leaked_rate * 100)}%</td>
                        <td className="num">{s.wrongly_quarantined}</td>
                        <td className="num">{s.ms_to_containment} ms</td>
                      </tr>
                    )),
                  )}
                </tbody>
              </table>
            </section>
          )}
          <section className="card" aria-label={copy.bench.params}>
            <header className="card-h">
              <h2>{copy.bench.params}</h2>
            </header>
            <dl className="params">
              {Object.entries(d.params).map(([k, v]) => (
                <div key={k}>
                  <dt>{k.replace(/_/g, ' ')}</dt>
                  <dd>{String(v)}</dd>
                </div>
              ))}
            </dl>
          </section>
        </>
      )}
    </div>
  );
}
