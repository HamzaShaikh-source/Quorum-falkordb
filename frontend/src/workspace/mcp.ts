/**
 * Everything the UI knows about Quorum's MCP server. In live mode the tool list comes from the backend (GET /api/mcp/catalog,
 * read from the tools themselves). MCP_TOOLS below is the fallback for the sample build and for a hub that is unreachable;
 * it is built from backend/tests/fixtures/mcp_catalog.json, which a backend test keeps equal to the real catalog.
 */
import catalogFixture from '../../../backend/tests/fixtures/mcp_catalog.json';

export interface McpTool {
  name: string;
  summary: string;
  args: string;
  who: 'agent' | 'auditor' | 'human' | 'any';
  writes: boolean;
  saves: string;
  /** not in the backend yet; documented in docs/CONTRACT_PROPOSALS.md */
  proposed?: boolean;
}


/** Shape of one entry from GET /api/mcp/catalog. */
export interface CatalogTool {
  name: string;
  summary: string;
  args: { name: string; required: boolean; default?: unknown }[];
  who: McpTool['who'];
  writes: boolean;
  saves: string;
}

const argText = (a: CatalogTool['args'][number]) => (a.name === 'token' ? '' : a.required ? a.name : `${a.name}=${JSON.stringify(a.default)}`.replace(/=null$/, '?'));

export function fromCatalog(tools: CatalogTool[]): McpTool[] {
  return tools.map((t) => ({
    name: t.name,
    summary: t.summary,
    args: t.args.map(argText).filter(Boolean).join(', '),
    who: t.who,
    writes: t.writes,
    saves: t.saves,
  }));
}

/** The built-in list (sample build, or a hub that cannot be reached). It is the backend's catalog, snapshotted by scripts/gen_catalog_fixture.py. */
export const MCP_TOOLS: McpTool[] = fromCatalog(catalogFixture.tools as CatalogTool[]);

export const WHO_LABEL: Record<McpTool['who'], string> = { agent: 'Agent token', auditor: 'Auditor token', human: 'Human token', any: 'Any token' };

export type ClientId = 'claude-code' | 'claude-desktop' | 'cursor' | 'vscode' | 'windsurf' | 'antigravity' | 'chatgpt' | 'custom';

export interface ClientInfo {
  id: ClientId;
  name: string;
  where: string;
  transport: 'http' | 'stdio' | 'either';
}

export const CLIENTS: ClientInfo[] = [
  { id: 'claude-code', name: 'Claude Code', where: 'Terminal', transport: 'either' },
  { id: 'claude-desktop', name: 'Claude Desktop', where: 'claude_desktop_config.json', transport: 'stdio' },
  { id: 'cursor', name: 'Cursor', where: '.cursor/mcp.json', transport: 'either' },
  { id: 'vscode', name: 'VS Code', where: '.vscode/mcp.json', transport: 'either' },
  { id: 'windsurf', name: 'Windsurf', where: '~/.codeium/windsurf/mcp_config.json', transport: 'either' },
  { id: 'antigravity', name: 'Antigravity', where: '~/.gemini/antigravity/mcp_config.json', transport: 'either' },
  { id: 'chatgpt', name: 'ChatGPT / OpenAI Agents', where: 'Connector URL (HTTPS)', transport: 'http' },
  { id: 'custom', name: 'Any MCP client', where: 'URL or command', transport: 'either' },
];

export interface SetupInput {
  client: ClientId;
  transport: 'http' | 'stdio';
  /** the hub's MCP endpoint, e.g. http://localhost:8000/mcp or https://quorum.example.com/mcp */
  url: string;
  repoPath: string;
  /** a real member token if one was just issued; otherwise a placeholder is printed */
  token?: string;
}

export const DEFAULT_MCP_URL = 'http://localhost:8000/mcp';
export const DEFAULT_REPO = 'path/to/iFone';
export const TOKEN_PLACEHOLDER = '<YOUR_TOKEN>';

/** Clients that can attach an Authorization header to a remote server. ChatGPT connectors cannot. */
export const HEADER_CLIENTS: ClientId[] = ['claude-code', 'cursor', 'vscode', 'windsurf', 'antigravity', 'custom'];

/** Whether this client/transport combination keeps the token out of the model's view. */
export const tokenHidden = (client: ClientId, transport: 'http' | 'stdio') => (transport === 'stdio' ? client !== 'chatgpt' : HEADER_CLIENTS.includes(client));

const bridge = (repo: string) => `${repo}/mcp/bridge.py`;
const bridgeEnv = (url: string, token: string) => ({ QUORUM_MCP_URL: url, QUORUM_MCP_TOKEN: token });
const stdioEntry = (i: SetupInput, token: string) => ({ command: 'python', args: [bridge(i.repoPath)], env: bridgeEnv(i.url, token) });

/**
 * Ready-to-paste setup for one client. Text only: the UI never writes to the user's machine.
 * Remote servers carry the token in an Authorization header, stdio servers get it from the environment, so the model
 * never sees it. stdio goes through mcp/bridge.py, which forwards to the hub (one shared graph, one audit log).
 */
export function setupSnippet(i: SetupInput): { file: string; lang: 'bash' | 'json'; body: string; note?: string } {
  const info = CLIENTS.find((c) => c.id === i.client) ?? CLIENTS[CLIENTS.length - 1]!;
  const http = i.transport === 'http';
  const token = i.token || TOKEN_PLACEHOLDER;
  const bearer = `Bearer ${token}`;
  const json = (v: unknown) => JSON.stringify(v, null, 2);

  if (i.client === 'claude-code') {
    return {
      file: 'Run in your project folder',
      lang: 'bash',
      body: http
        ? `claude mcp add --transport http quorum ${i.url} --header "Authorization: ${bearer}"`
        : `claude mcp add quorum --env QUORUM_MCP_URL=${i.url} --env QUORUM_MCP_TOKEN=${token} -- python ${bridge(i.repoPath)}`,
    };
  }
  if (i.client === 'claude-desktop') {
    return {
      file: '%APPDATA%\\Claude\\claude_desktop_config.json (Windows) or ~/Library/Application Support/Claude/claude_desktop_config.json (macOS). Restart Claude Desktop after saving.',
      lang: 'json',
      body: json({ mcpServers: { quorum: stdioEntry(i, token) } }),
      note: 'Claude Desktop starts local commands, so it uses the stdio bridge. The bridge forwards to your hub; it keeps no data of its own.',
    };
  }
  if (i.client === 'chatgpt') {
    return {
      file: 'Add as a connector. It must be reachable over HTTPS.',
      lang: 'bash',
      body: `Server URL:  ${i.url.replace('http://localhost', 'https://your-host')}
Transport:   Streamable HTTP
Auth:        none (ChatGPT connectors cannot send a custom header)

Then tell the assistant to pass token="${token}" on each call (see step 3).`,
      note: 'Without header auth the token is visible to the model. Prefer a client that can send the header, and keep this hub private.',
    };
  }
  if (i.client === 'vscode') {
    const inputs = [{ type: 'promptString', id: 'quorum-token', description: 'Quorum token', password: true }];
    const server = http
      ? { type: 'http', url: i.url, headers: { Authorization: 'Bearer ${input:quorum-token}' } }
      : { type: 'stdio', command: 'python', args: [bridge(i.repoPath)], env: bridgeEnv(i.url, '${input:quorum-token}') };
    return { file: '.vscode/mcp.json (VS Code asks for the token when it starts the server, so the file holds no secret)', lang: 'json', body: json({ inputs, servers: { quorum: server } }) };
  }
  if (i.client === 'windsurf' || i.client === 'antigravity') {
    const entry = http ? { serverUrl: i.url, headers: { Authorization: bearer } } : stdioEntry(i, token);
    return {
      file: i.client === 'antigravity' ? '~/.gemini/antigravity/mcp_config.json' : info.where,
      lang: 'json',
      body: json({ mcpServers: { quorum: entry } }),
      note: i.client === 'antigravity' && http ? 'If your Antigravity build ignores "headers", switch to the stdio bridge.' : undefined,
    };
  }
  // cursor and any other client that reads mcpServers with url + headers
  const entry = http ? { url: i.url, headers: { Authorization: bearer } } : stdioEntry(i, token);
  return { file: info.where, lang: 'json', body: json({ mcpServers: { quorum: entry } }) };
}

/** Paste into the agent's rules so it uses the cheap paths first. */
export function agentRules(token: string, role: string, hidden = true): string {
  return [
    '# Quorum context rules',
    hidden
      ? `Use the quorum MCP server for shared memory. Your connection is already authenticated as ${role}; do not pass a token.`
      : `Use the quorum MCP server for shared memory. Pass token="${token}" (${role}) on every call.`,
    '1. Ask with retrieve_context(query, format="compact", budget_tokens=400). It returns one line per node with short ids (F3, C2) and the sources that prove them. Do not load more than you need.',
    '2. Call get_map only when you do not know where to look. Every extra call is another turn, and in a short session a turn costs more than the map saves.',
    '3. Cite ids (F3, C2) instead of repeating their text. Use get_node(id) for one node and find_path(from_id, to_id) for how two things connect.',
    '4. When any response shows a new epoch, call delta(). It lists what stopped being true (drop it) and what is new.',
    '5. Record new knowledge once with remember_fact or derive, then reference its id. Use scope="team" for facts only your team should see.',
    '6. When you finish, call observe(text) once with your final answer or notes. It saves the numbers, policies and limits in them with their source sentence, so you do not need a remember_fact call per fact.',
  ].join('\n');
}

export interface Probe {
  state: 'connected' | 'unreachable' | 'unexpected';
  latencyMs: number;
  detail: string;
}

/** Real reachability check against the MCP endpoint through the dev/preview proxy (live mode only). */
export async function probeMcp(path = '/mcp', token?: string | null): Promise<Probe> {
  const t0 = performance.now();
  try {
    const res = await fetch(path, {
      method: 'POST',
      headers: { 'content-type': 'application/json', accept: 'application/json, text/event-stream', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'initialize', params: { protocolVersion: '2025-03-26', capabilities: {}, clientInfo: { name: 'quorum-console', version: '0.1.0' } } }),
      signal: AbortSignal.timeout(4000),
    });
    const latencyMs = Math.round(performance.now() - t0);
    if (res.ok) return { state: 'connected', latencyMs, detail: `HTTP ${res.status} from ${path}` };
    if (res.status === 401) return { state: 'unexpected', latencyMs, detail: 'HTTP 401. The hub is up and wants an Authorization header; the console token was refused.' };
    return { state: 'unexpected', latencyMs, detail: `HTTP ${res.status}. The server answered but did not accept the handshake.` };
  } catch (e) {
    return { state: 'unreachable', latencyMs: Math.round(performance.now() - t0), detail: e instanceof Error ? e.message : 'Network error' };
  }
}
