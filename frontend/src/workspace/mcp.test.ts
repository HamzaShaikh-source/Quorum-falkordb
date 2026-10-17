import { describe, expect, it } from 'vitest';
import { agentRules, CLIENTS, fromCatalog, HEADER_CLIENTS, MCP_TOOLS, setupSnippet, tokenHidden, type ClientId } from './mcp';

const base = { url: 'https://quorum.example.com/mcp', repoPath: '/opt/iFone', token: 'qrm_acme_s3cret' };
const snip = (client: ClientId, transport: 'http' | 'stdio') => setupSnippet({ client, transport, ...base });
const json = (c: ClientId, t: 'http' | 'stdio') => JSON.parse(snip(c, t).body) as Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

describe('built-in tool list', () => {
  it('has the tools the hub serves, including observe, with the token hidden from the argument list', () => {
    const names = MCP_TOOLS.map((t) => t.name);
    expect(names).toContain('observe');
    expect(names).toHaveLength(25);
    expect(names).toEqual(expect.arrayContaining(["quorum_help", "quorum_start", "quorum_connect", "peer_retrieve"]));
    expect(MCP_TOOLS.every((t) => !/\btoken\b/.test(t.args))).toBe(true);
    expect(MCP_TOOLS.find((t) => t.name === 'retrieve_context')!.args).toBe('query, k=5, format="full", budget_tokens?');
  });
  it('maps a backend catalog entry to a row', () => {
    const [row] = fromCatalog([{ name: 'x', summary: 's', args: [{ name: 'a', required: true }, { name: 'token', required: false, default: '' }, { name: 'n', required: false, default: 3 }], who: 'any', writes: false, saves: 'v' }]);
    expect(row).toMatchObject({ name: 'x', args: 'a, n=3', who: 'any' });
  });
});

describe('setup snippets', () => {
  it('Claude Code sends the token as a header, not as a tool argument', () => {
    expect(snip('claude-code', 'http').body).toBe('claude mcp add --transport http quorum https://quorum.example.com/mcp --header "Authorization: Bearer qrm_acme_s3cret"');
    expect(snip('claude-code', 'stdio').body).toContain('--env QUORUM_MCP_TOKEN=qrm_acme_s3cret');
    expect(snip('claude-code', 'stdio').body).toContain('/opt/iFone/mcp/bridge.py');
  });
  it('Cursor uses mcpServers with url + headers', () => {
    expect(json('cursor', 'http').mcpServers.quorum).toEqual({ url: base.url, headers: { Authorization: 'Bearer qrm_acme_s3cret' } });
  });
  it('VS Code uses servers + type http, and asks for the token instead of storing it', () => {
    const j = json('vscode', 'http');
    expect(j.servers.quorum).toMatchObject({ type: 'http', url: base.url });
    expect(j.servers.quorum.headers.Authorization).toBe('Bearer ${input:quorum-token}');
    expect(j.inputs[0]).toMatchObject({ id: 'quorum-token', password: true });
    expect(snip('vscode', 'http').body).not.toContain('qrm_acme_s3cret');
    expect(json('vscode', 'stdio').servers.quorum).toMatchObject({ type: 'stdio', command: 'python' });
  });
  it('Windsurf and Antigravity use serverUrl', () => {
    expect(json('windsurf', 'http').mcpServers.quorum.serverUrl).toBe(base.url);
    expect(json('antigravity', 'http').mcpServers.quorum.serverUrl).toBe(base.url);
    expect(snip('antigravity', 'http').file).toBe('~/.gemini/antigravity/mcp_config.json');
  });
  it('Claude Desktop launches the bridge with the token in the environment', () => {
    const e = json('claude-desktop', 'stdio').mcpServers.quorum;
    expect(e.command).toBe('python');
    expect(e.args).toEqual(['/opt/iFone/mcp/bridge.py']);
    expect(e.env).toEqual({ QUORUM_MCP_URL: base.url, QUORUM_MCP_TOKEN: 'qrm_acme_s3cret' });
    expect(snip('claude-desktop', 'stdio').file).toContain('\\Claude\\claude_desktop_config.json');
  });
  it('ChatGPT is told plainly that it cannot hide the token', () => {
    const s = snip('chatgpt', 'http');
    expect(s.body).toContain('cannot send a custom header');
    expect(s.note).toMatch(/visible to the model/);
  });
  it('prints a placeholder, never a made-up token, when no token was just issued', () => {
    expect(setupSnippet({ client: 'cursor', transport: 'http', url: base.url, repoPath: '.' }).body).toContain('<YOUR_TOKEN>');
  });
  it('every client produces a non-empty snippet in both transports', () => {
    for (const c of CLIENTS) for (const t of ['http', 'stdio'] as const) expect(snip(c.id, t).body.length).toBeGreaterThan(20);
  });
});

describe('what the model can see', () => {
  it('only ChatGPT over HTTP exposes the token', () => {
    for (const c of CLIENTS) {
      expect(tokenHidden(c.id, 'http')).toBe(HEADER_CLIENTS.includes(c.id));
      expect(tokenHidden(c.id, 'stdio')).toBe(c.id !== 'chatgpt');
    }
  });
  it('rules never ask the agent to pass a token when the connection carries it', () => {
    expect(agentRules('qrm_x', 'agent', true)).not.toContain('qrm_x');
    expect(agentRules('qrm_x', 'agent', false)).toContain('token="qrm_x"');
    expect(agentRules('qrm_x', 'agent', true)).toContain('observe(text)');
  });
});
