import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { API_MODE } from '../api/client';
import { useStore } from '../state/store';
import { connectTo, getInbox, getPeers, respondRequest } from '../workspace/server';
import { levelName } from '../workspace/roles';
import './network.css';

const ORDER = ['agent', 'auditor', 'human'] as const;

/**
 * Other Quorum hubs on this network, requests waiting for an answer, and the hubs already connected. The same things the
 * AI does with quorum_discover, quorum_connect, quorum_inbox and quorum_respond, for people who prefer to click.
 */
export function Network() {
  const qc = useQueryClient();
  const toast = useStore((s) => s.pushToast);
  const [levels, setLevels] = useState<Record<string, string>>({});
  const peers = useQuery({ queryKey: ['workspace', 'peers'], queryFn: getPeers, refetchInterval: 6000, enabled: API_MODE === 'live' });
  const inbox = useQuery({ queryKey: ['workspace', 'inbox'], queryFn: getInbox, refetchInterval: 6000, enabled: API_MODE === 'live' });
  const refresh = () => void qc.invalidateQueries({ queryKey: ['workspace'] });
  const ask = useMutation({
    mutationFn: (name: string) => connectTo(name, ''),
    onSuccess: (_r, name) => {
      toast(`Request sent to ${name}. Their admin has been notified.`, 'good');
      refresh();
    },
    onError: (e: Error) => toast(e.message, 'bad'),
  });
  const answer = useMutation({
    mutationFn: (a: { id: string; approve: boolean; role: string }) => respondRequest(a.id, a.approve, a.role),
    onSuccess: (_r, a) => {
      toast(a.approve ? 'Approved. They receive their access automatically.' : 'Declined.', a.approve ? 'good' : 'info');
      refresh();
    },
    onError: (e: Error) => toast(e.message, 'bad'),
  });
  if (API_MODE !== 'live') return null;
  const waiting = (inbox.data?.requests ?? []).filter((r) => r.status === 'pending');
  const seen = peers.data?.peers ?? [];
  const connected = peers.data?.connections ?? [];
  const sent = (peers.data?.outgoing ?? []).filter((o) => o.status !== 'approved');
  return (
    <section className="card wide nw" aria-label="Teammates on your network">
      <div className="nw-head">
        <h2>Connect with teammates</h2>
        <p>
          Hubs that other people run on this network show up here. Ask to join one and its admin is told right away; nothing is shared until they approve.
          {peers.data && <> You appear to them as <b>{peers.data.hub.name}</b>.</>}
        </p>
      </div>

      <div className="nw-block" aria-live="polite">
        <h3>Waiting for your approval {waiting.length > 0 && <span className="nw-badge">{waiting.length}</span>}</h3>
        {waiting.length === 0 ? (
          <p className="muted">Nobody is asking to join you right now.</p>
        ) : (
          <ul className="nw-list">
            {waiting.map((r) => (
              <li key={r.id} className="nw-row">
                <div>
                  <b>{r.person}</b> <span className="muted">from {r.from_hub || 'another hub'}</span>
                  {r.message && <small className="nw-msg">&ldquo;{r.message}&rdquo;</small>}
                </div>
                <label className="nw-level">
                  <span className="sr-only">Permission level for {r.person}</span>
                  <select value={levels[r.id] ?? 'agent'} onChange={(e) => setLevels({ ...levels, [r.id]: e.target.value })}>
                    {ORDER.map((k) => (
                      <option key={k} value={k}>
                        {levelName(k)}
                      </option>
                    ))}
                  </select>
                </label>
                <button className="btn primary" disabled={answer.isPending} onClick={() => answer.mutate({ id: r.id, approve: true, role: levels[r.id] ?? 'agent' })}>
                  Approve
                </button>
                <button className="btn quiet" disabled={answer.isPending} onClick={() => answer.mutate({ id: r.id, approve: false, role: 'agent' })}>
                  Decline
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="nw-block">
        <h3>Visible on this network</h3>
        {seen.length === 0 ? (
          <p className="muted">No other hub is visible yet. They need Quorum running with the beacon (setup does this) on the same Wi-Fi or LAN.</p>
        ) : (
          <ul className="nw-list">
            {seen.map((p) => (
              <li key={p.hub_id} className="nw-row">
                <div>
                  <b>{p.name}</b> <span className="muted mono">{p.url}</span>
                </div>
                {p.connected ? (
                  <span className="nw-state ok">Connected</span>
                ) : p.requested ? (
                  <span className="nw-state">Waiting for their approval</span>
                ) : (
                  <button className="btn primary" disabled={ask.isPending} onClick={() => ask.mutate(p.name)}>
                    Ask to connect
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      {(connected.length > 0 || sent.length > 0) && (
        <div className="nw-block">
          <h3>Your connections</h3>
          <ul className="nw-list">
            {connected.map((c) => (
              <li key={c.peer_name} className="nw-row">
                <div>
                  <b>{c.peer_name}</b> <span className="muted">you are {c.as_member || 'a member'}, {levelName(c.role)}</span>
                </div>
                <span className="nw-state ok">Connected</span>
              </li>
            ))}
            {sent.map((o) => (
              <li key={o.peer_name} className="nw-row">
                <div>
                  <b>{o.peer_name}</b>
                </div>
                <span className="nw-state">{o.status === 'declined' ? 'Declined' : o.status === 'expired' ? 'Expired' : 'Waiting for their approval'}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
