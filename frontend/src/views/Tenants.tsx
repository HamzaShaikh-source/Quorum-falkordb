import { useQuery } from '@tanstack/react-query';
import { copy } from '../copy';
import { Icon } from '../components/Icon';
import { Chip, ErrorState, Skeleton } from '../components/ui';
import { getDenied } from '../state/endpoints';
import { useStore } from '../state/store';
import { PageHead } from './Memory';

export function Tenants() {
  const tenant = useStore((s) => s.tenant);
  const q = useQuery({ queryKey: ['denied', tenant], queryFn: getDenied });
  const d = q.data;
  const denied = d?.result === 'DENIED';
  return (
    <div className="page" tabIndex={0}>
      <PageHead page="tenants" />
      <section className="card wide" aria-label={copy.tenants.title}>
        {q.isLoading && <Skeleton lines={5} />}
        {q.isError && <ErrorState error={q.error} onRetry={() => void q.refetch()} />}
        {d && (
          <div className="denied">
            <div className="denied-flow">
              <div className="tenant-card mine">
                <span className="tenant-ico">
                  <Icon name="tenants" size={22} />
                </span>
                <span className="muted small">{copy.header.tenant}</span>
                <strong className="mono">{d.tenant}</strong>
                <span className="small ok-text">Your token</span>
              </div>
              <div className={`denied-link ${denied ? 'bad' : 'warn'}`} aria-hidden="true">
                <svg width="100%" height="40" viewBox="0 0 220 40" preserveAspectRatio="none">
                  <path d="M0 20 H92 M128 20 H212" stroke="currentColor" strokeWidth="2" strokeDasharray="7 5" fill="none" />
                  <path d="m214 20-9-5.5M214 20l-9 5.5" stroke="currentColor" strokeWidth="2" fill="none" />
                </svg>
                <span className="denied-badge">
                  <Icon name={denied ? 'lock' : 'alert'} size={18} />
                </span>
              </div>
              <div className="tenant-card theirs">
                <span className="tenant-ico">
                  <Icon name="lock" size={22} />
                </span>
                <span className="muted small">{copy.tenants.attempt}</span>
                <strong className="mono">{d.attempted_graph}</strong>
                <span className="small bad-text">Not yours to read</span>
              </div>
            </div>
            <p className="denied-result">
              {copy.tenants.result}{' '}
              <Chip tone={denied ? 'good' : 'bad'}>
                {d.result === 'DENIED' ? copy.tenants.denied : d.result === 'ALLOWED' ? copy.tenants.allowed : copy.status.UNKNOWN}
              </Chip>
            </p>
            <div className="grid-2 tight">
              <div>
                <h2 className="sub">{copy.tenants.detail}</h2>
                <pre className="terminal mono">{d.detail}</pre>
              </div>
              <div>
                <h2 className="sub">{copy.tenants.visible}</h2>
                <ul className="graphs mono">
                  {d.visible_graph_names.map((g) => (
                    <li key={g}>
                      <Icon name="memory" size={14} />
                      {g}
                    </li>
                  ))}
                </ul>
              </div>
            </div>
            <button className="btn" onClick={() => void q.refetch()}>
              <Icon name="refresh" size={14} />
              {copy.tenants.run}
            </button>
          </div>
        )}
      </section>
    </div>
  );
}
