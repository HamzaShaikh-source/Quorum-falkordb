import { PageTag } from '../components/PageTag';
import { useEffect, useMemo, useState } from 'react';
import { API_MODE } from '../api/client';
import { GettingStarted } from '../components/GettingStarted';
import { AiPrompt } from '../components/AiPrompt';
import { UseGuide } from '../components/UseGuide';
import { Icon } from '../components/Icon';
import { fmt, pct } from '../context/tokens';
import { useStore } from '../state/store';
import { useLive, type Activity } from '../workspace/live';
import { copyText } from '../workspace/clipboard';
import { agentRules, CLIENTS, DEFAULT_MCP_URL, DEFAULT_REPO, fromCatalog, MCP_TOOLS, setupSnippet, tokenHidden, WHO_LABEL, type ClientId, type McpTool } from '../workspace/mcp';
import { useWs } from '../workspace/useWs';
import { getActivity, getCatalog } from '../workspace/server';
import { useQuery } from '@tanstack/react-query';
import type { McpCall } from '../api/schemas';

function ago(ts: number): string {
  const s = Math.max(0, Math.round((Date.now() - ts) / 1000));
  if (s < 5) return 'just now';
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  return `${Math.round(s / 3600)}h ago`;
}

const TONE: Record<string, string> = { connected: 'ok', simulated: 'warn', checking: 'neutral', unreachable: 'bad', unexpected: 'warn' };
const WORD: Record<string, string> = {
  connected: 'Connected',
  simulated: 'Sample data (simulated)',
  checking: 'Checking',
  unreachable: 'Not reachable',
  unexpected: 'Reachable, handshake refused',
};

/** Live: the tools this hub actually serves (read from the code). Sample build or hub unreachable: the built-in list. */
function useHub() {
  const q = useQuery({ queryKey: ['catalog'], queryFn: getCatalog, enabled: API_MODE === 'live', staleTime: 30_000, retry: 1 });
  const tools: McpTool[] = q.data ? fromCatalog(q.data.tools) : MCP_TOOLS;
  return { tools, hub: q.data, fromBackend: !!q.data };
}

function CopyBlock({ title, body, lang, note }: { title: string; body: string; lang?: string; note?: string }) {
  const pushToast = useStore((s) => s.pushToast);
  return (
    <div className="cn-block">
      <div className="cn-block-h">
        <b>{title}</b>
        <span className="grow" />
        <button
          className="btn quiet"
          onClick={() => void copyText(body).then((ok) => pushToast(ok ? 'Copied' : 'Copy was blocked by the browser', ok ? 'good' : 'warn'))}
        >
          <Icon name="copy" size={14} /> Copy
        </button>
      </div>
      <pre className="terminal mono" data-lang={lang} tabIndex={0} aria-label={`${title}: command to copy`}>
        {body}
      </pre>
      {note && <p className="cn-help">{note}</p>}
    </div>
  );
}

/** The address other machines use: the hub's public URL (QUORUM_PUBLIC_URL), then the workspace label, then localhost. */
function hubMcpUrl(publicUrl?: string | null, label?: string): string {
  const base = publicUrl || label;
  return base && /^https?:\/\//.test(base) ? `${base.replace(/\/+$/, '')}/mcp` : DEFAULT_MCP_URL;
}

function Status() {
  const { tools, hub } = useHub();
  const ws = useWs();
  const mcp = useLive((s) => s.mcp);
  const check = useLive((s) => s.checkMcp);
  const [typed, setUrl] = useState<string | null>(null);
  const url = typed ?? hubMcpUrl(hub?.public_url, ws.hub);
  return (
    <section className="card wide cn-status" aria-label="MCP server status">
      <div className={`cn-light ${TONE[mcp.state]}`} aria-hidden="true" />
      <div className="cn-status-main">
        <h2>
          Quorum MCP server: <span className={`${TONE[mcp.state]}-text`}>{WORD[mcp.state]}</span>
        </h2>
        <p className="muted" role="status" aria-live="polite">
          {mcp.detail}
          {mcp.checkedAt > 0 && <> Checked {ago(mcp.checkedAt)}.</>}
        </p>
        {mcp.state === 'unreachable' && (
          <p className="cn-help">
            Start the backend with <code>docker compose up -d</code> (MCP is served from it at /mcp), then press Test connection.
          </p>
        )}
        {API_MODE === 'mock' && <p className="cn-help">This console is running on sample data, so nothing here talks to a real server. Switch to live mode to run the real handshake.</p>}
      </div>
      <dl className="cn-facts">
        <div>
          <dt>Endpoint</dt>
          <dd>
            <input className="cn-url mono" value={url} onChange={(e) => setUrl(e.target.value)} aria-label="MCP endpoint URL" spellCheck={false} />
          </dd>
        </div>
        <div>
          <dt>Latency</dt>
          <dd className="num">{mcp.state === 'unreachable' || mcp.state === 'checking' ? '—' : `${mcp.latencyMs} ms`}</dd>
        </div>
        <div>
          <dt>Tools</dt>
          <dd className="num">{tools.length} live</dd>
        </div>
      </dl>
      <button className="btn primary" onClick={() => void check()} disabled={mcp.state === 'checking'}>
        <Icon name="refresh" size={14} /> Test connection
      </button>
    </section>
  );
}

function Setup() {
  const ws = useWs();
  const { hub } = useHub();
  const members = ws.members;
  // Laptops on other machines connect to the hub's public address (QUORUM_PUBLIC_URL), then the workspace label, then localhost.
  const mcpUrl = hubMcpUrl(hub?.public_url, ws.hub);
  const [client, setClient] = useState<ClientId>('claude-code');
  const [transport, setTransport] = useState<'http' | 'stdio'>('http');
  const [memberId, setMemberId] = useState(members[0]?.id ?? '');
  const [repo, setRepo] = useState(DEFAULT_REPO);
  const info = CLIENTS.find((c) => c.id === client)!;
  const tr = info.transport === 'http' ? 'http' : info.transport === 'stdio' ? 'stdio' : transport;
  const member = members.find((m) => m.id === memberId) ?? members[0];
  const hidden = tokenHidden(client, tr);
  const snip = setupSnippet({ client, transport: tr, url: mcpUrl, repoPath: repo, token: member?.token });
  return (
    <section className="card wide" aria-label="Connect your AI">
      <header className="card-h">
        <h2>Connect your AI in three steps</h2>
        <span className="card-sub">Works with any MCP client</span>
      </header>
      <ol className="cn-steps">
        <li>
          <b>1. Start the server.</b> <span className="muted">Run it once per workspace.</span>
          <code className="cn-cmd mono">python scripts/setup.py</code>
        </li>
        <li>
          <b>2. Paste this into your client.</b> <span className="muted">Pick yours:</span>
          <div className="cn-clients" role="radiogroup" aria-label="AI client">
            {CLIENTS.map((c) => (
              <button key={c.id} role="radio" aria-checked={client === c.id} className={`cn-client${client === c.id ? ' on' : ''}`} onClick={() => setClient(c.id)}>
                {c.name}
              </button>
            ))}
          </div>
          <div className="cn-opts">
            {info.transport === 'either' && (
              <div className="seg" role="group" aria-label="Transport">
                {(['http', 'stdio'] as const).map((t) => (
                  <button key={t} className={`seg-btn${transport === t ? ' on' : ''}`} aria-pressed={transport === t} onClick={() => setTransport(t)}>
                    {t === 'http' ? 'HTTP (direct to the hub)' : 'stdio bridge (for apps that launch a command)'}
                  </button>
                ))}
              </div>
            )}
            {tr === 'stdio' && (
              <label className="field inline">
                <span>Path to the iFone repo (for mcp/bridge.py)</span>
                <input value={repo} onChange={(e) => setRepo(e.target.value)} spellCheck={false} />
              </label>
            )}
          </div>
          <CopyBlock title={snip.file} body={snip.body} lang={snip.lang} note={snip.note} />
        </li>
        <li>
          <b>3. Give the agent its rules.</b>{' '}
          <span className="muted">
            {hidden
              ? 'Your token travels in the connection (an Authorization header or an environment variable), so the model never sees it. Real tokens are shown once, when a member is added; use that one in place of <YOUR_TOKEN>.'
              : 'This client cannot send a header, so the token is passed as a tool argument and the model can see it. Keep this hub private.'}
          </span>
          <label className="field inline">
            <span>Member</span>
            <select value={member?.id ?? ''} onChange={(e) => setMemberId(e.target.value)}>
              {members.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name} · {m.role}
                </option>
              ))}
            </select>
          </label>
          {member ? <CopyBlock title="Paste into the agent's rules (CLAUDE.md, .cursorrules, system prompt)" body={agentRules(member.token ?? '<YOUR_TOKEN>', member.role, hidden)} /> : <p className="muted">Add a member on the Team page first.</p>}
        </li>
      </ol>
    </section>
  );
}

function Tools() {
  const { tools: MCP_TOOLS, hub, fromBackend } = useHub();
  return (
    <section className="card wide" aria-label="MCP tools">
      <header className="card-h">
        <h2>Tools your agent gets</h2>
        <span className="card-sub">
          {MCP_TOOLS.length} tools, {MCP_TOOLS.filter((t) => !t.writes).length} of them read-only{fromBackend ? ' · read from the hub' : ''}
        </span>
      </header>
      {hub && (
        <p className="cn-help" role="note">
          This hub {hub.header_required ? 'requires' : 'accepts'} an Authorization header on /mcp, limits each token to {hub.rate_limit_per_min.tools} tool calls a minute, and ranks context with {hub.embedding_provider === 'local' ? 'keywords plus built-in local vectors (no model, no network)' : `keywords plus ${hub.embedding_provider} embeddings`}.
        </p>
      )}
      <div className="cn-table-wrap" tabIndex={0} role="region" aria-label="Table (scrolls sideways on a narrow screen)">
        <table className="cn-table">
          <thead>
            <tr>
              <th scope="col">Tool</th>
              <th scope="col">What it does</th>
              <th scope="col">Who can call it</th>
              <th scope="col">How it saves tokens</th>
            </tr>
          </thead>
          <tbody>
            {MCP_TOOLS.map((t) => (
              <tr key={t.name} className={t.proposed ? 'proposed' : ''}>
                <th scope="row" className="mono">
                  {t.name}
                  {t.proposed && <em className="cn-tag">proposed</em>}
                </th>
                <td>
                  {t.summary}
                  <small className="muted mono">{t.args}</small>
                </td>
                <td>
                  <span className={`cn-role ${t.who}`}>{WHO_LABEL[t.who]}</span>
                  <small className="muted">{t.writes ? 'Writes' : 'Read only'}</small>
                </td>
                <td>{t.saves}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Row({ a }: { a: Activity }) {
  const saved = a.naive > 0 ? a.naive - a.used : 0;
  return (
    <li className="cn-row">
      <span className="avatar">{[...a.member][0] ?? ''}</span>
      <div className="cn-row-main">
        <p>
          <b>{a.member}</b> <code className="cn-tool mono">{a.tool}</code>
          {a.via && <em className="cn-tag">via {a.via}</em>}
        </p>
        <small className="muted">{a.summary}</small>
      </div>
      <div className="cn-row-tok num" title="Tokens the agent received, and what a notes-style memory would have sent">
        {a.naive > 0 ? (
          <>
            <b>{fmt(a.used)}</b> <s className="muted">{fmt(a.naive)}</s>
            <small className="ok-text">−{pct(saved, a.naive)}%</small>
          </>
        ) : (
          <small className="muted">{fmt(a.used)} tok</small>
        )}
      </div>
      <time className="muted small">{ago(a.ts)}</time>
    </li>
  );
}

function Feeds() {
  const activity = useLive((s) => s.activity);
  const chats = useLive((s) => s.chats);
  const presence = useLive((s) => s.presence);
  const clear = useLive((s) => s.clear);
  const members = useWs().members;
  const [tab, setTab] = useState<'feed' | 'chats'>('feed');
  const [who, setWho] = useState('all');
  const [tool, setTool] = useState('all');
  const [paused, setPaused] = useState(false);
  const [frozen, setFrozen] = useState<Activity[]>([]);
  const [chatId, setChatId] = useState(members[0]?.id ?? '');
  const [, tick] = useState(0);
  useEffect(() => {
    const t = setInterval(() => tick((n) => n + 1), 5000);
    return () => clearInterval(t);
  }, []);

  const source = paused ? frozen : activity;
  const rows = useMemo(() => source.filter((a) => (who === 'all' || a.memberId === who) && (tool === 'all' || a.tool === tool)), [source, who, tool]);
  const turns = chats[chatId] ?? [];
  const m = members.find((x) => x.id === chatId);

  return (
    <section className="card wide cn-feeds" aria-label="Live activity and chats">
      <header className="card-h">
        <div className="seg" role="tablist" aria-label="Activity">
          <button role="tab" aria-selected={tab === 'feed'} className={`seg-btn${tab === 'feed' ? ' on' : ''}`} onClick={() => setTab('feed')}>
            Live feed
          </button>
          <button role="tab" aria-selected={tab === 'chats'} className={`seg-btn${tab === 'chats' ? ' on' : ''}`} onClick={() => setTab('chats')}>
            Chats
          </button>
        </div>
        <span className="chip warn" title="Real activity needs the workspace service. This is generated in your browser from the real packer.">
          Simulated activity
        </span>
      </header>

      {tab === 'feed' ? (
        <>
          <div className="cn-filters">
            <label className="field inline">
              <span>Member</span>
              <select value={who} onChange={(e) => setWho(e.target.value)}>
                <option value="all">Everyone</option>
                {members.map((x) => (
                  <option key={x.id} value={x.id}>
                    {x.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="field inline">
              <span>Tool</span>
              <select value={tool} onChange={(e) => setTool(e.target.value)}>
                <option value="all">All tools</option>
                {MCP_TOOLS.map((t) => t.name).map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </select>
            </label>
            <span className="grow" />
            <button
              className="btn quiet"
              aria-pressed={paused}
              onClick={() => {
                setFrozen(activity);
                setPaused((p) => !p);
              }}
            >
              <Icon name={paused ? 'play' : 'pause'} size={13} /> {paused ? 'Resume' : 'Pause'}
            </button>
            <button className="btn quiet" onClick={clear}>
              Clear
            </button>
          </div>
          {rows.length ? (
            <ul className="cn-list" aria-live={paused ? 'off' : 'polite'} aria-relevant="additions" tabIndex={0} aria-label="Live feed">
              {rows.slice(0, 60).map((a) => (
                <Row key={a.id} a={a} />
              ))}
            </ul>
          ) : (
            <p className="muted cn-empty">Nothing yet. Calls from connected agents appear here as they happen.</p>
          )}
        </>
      ) : (
        <div className="cn-chats">
          <ul className="cn-people" aria-label="Members">
            {members.map((x) => (
              <li key={x.id}>
                <button className={chatId === x.id ? 'on' : ''} onClick={() => setChatId(x.id)}>
                  <span className={`pres ${presence[x.id] ?? 'offline'}`} aria-label={presence[x.id] ?? 'offline'} />
                  <span>
                    {x.name}
                    <small className="muted">{CLIENTS.find((c) => c.id === x.client)?.name}</small>
                  </span>
                </button>
              </li>
            ))}
          </ul>
          <div className="cn-thread" aria-label={`Conversation with ${m?.name ?? 'nobody'}`}>
            {turns.length ? (
              turns.map((t) => (
                <div key={t.id} className={`cn-turn ${t.from}`}>
                  <small className="muted">{t.from === 'user' ? m?.name : t.from === 'tool' ? 'Quorum MCP' : 'Assistant'}</small>
                  <p className={t.from === 'tool' ? 'mono' : ''}>{t.text}</p>
                  {t.tokens !== undefined && <small className="num ok-text">{fmt(t.tokens)} tokens sent</small>}
                </div>
              ))
            ) : (
              <p className="muted cn-empty">No conversation from {m?.name ?? 'this member'} yet. Sessions show the question, the MCP call and how many tokens it cost.</p>
            )}
          </div>
        </div>
      )}
    </section>
  );
}

const toRow = (c: McpCall): Activity => ({
  id: c.id,
  ts: c.ts,
  memberId: c.member_id ?? '',
  member: c.agent_id ?? 'agent',
  tool: c.tool,
  summary: c.ok ? c.summary : `${c.summary} (${c.error ?? 'refused'})`,
  used: c.tokens,
  naive: c.baseline,
  epoch: 0,
});

/** Live mode: every MCP call the backend recorded, plus per-member tool-call transcripts. Nothing is simulated. */
function LiveFeed() {
  const ws = useWs();
  const { tools: MCP_TOOLS } = useHub();
  const conn = useStore((st) => st.conn);
  const [tab, setTab] = useState<'feed' | 'chats'>('feed');
  const [who, setWho] = useState('all');
  const [tool, setTool] = useState('all');
  const [chatId, setChatId] = useState('');
  const act = useQuery({ queryKey: ['activity'], queryFn: () => getActivity(), refetchInterval: 8000 });
  const memberId = chatId || ws.members[0]?.id || '';
  const chat = useQuery({ queryKey: ['activity', 'member', memberId], queryFn: () => getActivity(memberId), enabled: tab === 'chats' && !!memberId, refetchInterval: 8000 });
  const rows = (act.data ?? []).filter((c) => (who === 'all' || c.member_id === who) && (tool === 'all' || c.tool === tool));
  const turns = [...(chat.data ?? [])].reverse();
  const m = ws.members.find((x) => x.id === memberId);

  return (
    <section className="card wide cn-feeds" aria-label="Live activity from the backend">
      <header className="card-h">
        <div className="seg" role="tablist" aria-label="Activity">
          <button role="tab" aria-selected={tab === 'feed'} className={`seg-btn${tab === 'feed' ? ' on' : ''}`} onClick={() => setTab('feed')}>
            Live feed
          </button>
          <button role="tab" aria-selected={tab === 'chats'} className={`seg-btn${tab === 'chats' ? ' on' : ''}`} onClick={() => setTab('chats')}>
            Sessions
          </button>
        </div>
        <span className="chip" title="Every MCP tool call the backend served, newest first. Real, not simulated.">
          From the backend · stream {conn}
        </span>
      </header>
      {tab === 'feed' ? (
        <>
          <div className="cn-filters">
            <label className="field inline">
              <span>Member</span>
              <select value={who} onChange={(e) => setWho(e.target.value)}>
                <option value="all">Everyone</option>
                {ws.members.map((x) => (
                  <option key={x.id} value={x.id}>
                    {x.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="field inline">
              <span>Tool</span>
              <select value={tool} onChange={(e) => setTool(e.target.value)}>
                <option value="all">All tools</option>
                {MCP_TOOLS.filter((t) => !t.proposed).map((t) => (
                  <option key={t.name} value={t.name}>
                    {t.name}
                  </option>
                ))}
              </select>
            </label>
          </div>
          {rows.length ? (
            <ul className="cn-list" aria-live="polite" aria-relevant="additions" tabIndex={0} aria-label="Live feed">
              {rows.slice(0, 80).map((c) => (
                <Row key={c.id} a={toRow(c)} />
              ))}
            </ul>
          ) : (
            <p className="muted cn-empty">No tool calls yet. Connect an AI (above) and its calls appear here as they happen, with the tokens each one cost.</p>
          )}
        </>
      ) : (
        <div className="cn-chats">
          <ul className="cn-people" aria-label="Members">
            {ws.members.length === 0 && <li className="muted">Add a member on the Team page.</li>}
            {ws.members.map((x) => (
              <li key={x.id}>
                <button className={memberId === x.id ? 'on' : ''} onClick={() => setChatId(x.id)}>
                  <span className={`pres ${x.status === 'unknown' ? 'offline' : x.status}`} aria-label={x.status} />
                  <span>
                    {x.name}
                    <small className="muted">{CLIENTS.find((c) => c.id === x.client)?.name ?? x.client}</small>
                  </span>
                </button>
              </li>
            ))}
          </ul>
          <div className="cn-thread" aria-label={`Tool calls by ${m?.name ?? 'nobody'}`}>
            <p className="cn-help">Quorum sees an agent&apos;s tool calls, not the conversation around them. This is the transcript of what {m?.name ?? 'this member'}&apos;s AI asked Quorum.</p>
            {turns.length ? (
              turns.map((c) => (
                <div key={c.id} className={`cn-turn ${c.ok ? 'tool' : 'user'}`}>
                  <small className="muted">
                    {ago(c.ts)} · {c.tool}
                  </small>
                  <p className="mono">{c.summary || c.tool}</p>
                  <small className="num ok-text">
                    {fmt(c.tokens)} tokens{c.baseline > 0 ? ` vs ${fmt(c.baseline)} (${c.baseline >= c.tokens ? '−' : '+'}${Math.abs(pct(c.baseline - c.tokens, c.baseline))}%)` : ''}
                  </small>
                </div>
              ))
            ) : (
              <p className="muted cn-empty">No calls from {m?.name ?? 'this member'} yet.</p>
            )}
          </div>
        </div>
      )}
    </section>
  );
}

export function Connect() {
  return (
    <div className="page cn" tabIndex={0}>
      <header className="page-head">
        <PageTag view="connect">Connect</PageTag>
        <h1>Plug any AI into one memory</h1>
        <p>Run one MCP server, paste a snippet into Claude, Cursor, VS Code or anything else that speaks MCP, and every agent reads and writes the same graph.</p>
      </header>
      <GettingStarted />
      <AiPrompt />
      <UseGuide />
      <Status />
      <Setup />
      {API_MODE === 'mock' ? <Feeds /> : <LiveFeed />}
      <Tools />
    </div>
  );
}
