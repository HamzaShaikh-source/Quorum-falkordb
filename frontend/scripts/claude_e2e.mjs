/**
 * A real task with a real AI: Claude Code (the `claude` CLI) uses Quorum's MCP tools as shared memory.
 *
 *   A  Claude records facts and a conclusion.
 *   B  A fresh Claude session, with no memory of A, answers a customer question from Quorum.
 *   C  A wrong fact is planted, B-style session sees the danger; an auditor quarantines it;
 *      a third fresh session answers correctly and never retrieves the quarantined fact.
 *
 *   Needs: the live stack (docs/LIVE_TESTING.md), the `claude` CLI logged in. It uses a little of your Claude usage.
 *   node scripts/claude_e2e.mjs [API]
 */
import { spawnSync } from 'node:child_process';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { chromium } from 'playwright-core';

const API = process.argv[2] ?? 'http://127.0.0.1:8000';
const BASE = process.argv[3] ?? 'http://127.0.0.1:5174';
const results = [];
const check = (name, ok, extra = '') => {
  results.push({ name, ok });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${extra ? `  ${extra}` : ''}`);
};
const human = { Authorization: 'Bearer token_human' };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const dir = mkdtempSync(join(tmpdir(), 'quorum-claude-'));
const cfg = join(dir, 'mcp.json');
writeFileSync(cfg, JSON.stringify({ mcpServers: { quorum: { type: 'http', url: `${API}/mcp` } } }));

/** Runs one fresh Claude session. Returns its answer and the Quorum tools it called. */
function claude(prompt, retry = true) {
  const r = spawnSync(
    'claude',
    ['-p', prompt, '--mcp-config', cfg, '--strict-mcp-config', '--allowedTools', 'mcp__quorum', '--output-format', 'stream-json', '--verbose', '--max-turns', '12', '--no-session-persistence'],
    { encoding: 'utf8', timeout: 240000, maxBuffer: 20 * 1024 * 1024, cwd: dir }, // an empty folder: no project context to second-guess the task
  );
  const tools = [];
  const results = []; // what the Quorum tools actually returned to Claude
  let answer = '';
  let cost = 0;
  for (const line of (r.stdout ?? '').split('\n')) {
    if (!line.trim()) continue;
    try {
      const ev = JSON.parse(line);
      if (ev.type === 'assistant') for (const c of ev.message?.content ?? []) if (c.type === 'tool_use') tools.push({ name: c.name.replace('mcp__quorum__', ''), input: c.input });
      if (ev.type === 'user') for (const c of ev.message?.content ?? []) if (c.type === 'tool_result') results.push(typeof c.content === 'string' ? c.content : JSON.stringify(c.content));
      if (ev.type === 'result') {
        answer = ev.result ?? '';
        cost = ev.total_cost_usd ?? 0;
      }
    } catch {
      /* not JSON */
    }
  }
  if (r.status !== 0 && !answer) console.error((r.stderr ?? '').slice(0, 500));
  // An AI sometimes declines a task out of caution without touching the tools. That is not Quorum's doing, so try once more.
  if (retry && tools.length === 0) return claude(prompt, false);
  return { answer, tools, cost, results: results.join('\n') };
}

async function graph() {
  return (await fetch(`${API}/api/graph`, { headers: human })).json();
}

/* an MCP client for the "other agents" and the auditor in the story */
let sid = null;
let rid = 0;
async function mcpCall(name, args) {
  const post = (body) =>
    fetch(`${API}/mcp`, { method: 'POST', headers: { 'content-type': 'application/json', accept: 'application/json, text/event-stream', ...(sid ? { 'mcp-session-id': sid } : {}) }, body: JSON.stringify(body) });
  if (!sid) {
    const r = await post({ jsonrpc: '2.0', id: ++rid, method: 'initialize', params: { protocolVersion: '2025-03-26', capabilities: {}, clientInfo: { name: 'e2e', version: '1' } } });
    sid = r.headers.get('mcp-session-id');
    await post({ jsonrpc: '2.0', method: 'notifications/initialized' });
  }
  const r = await post({ jsonrpc: '2.0', id: ++rid, method: 'tools/call', params: { name, arguments: args } });
  const t = await r.text();
  const line = t.split('\n').find((l) => l.startsWith('data:'));
  const out = JSON.parse(line ? line.slice(5) : t);
  return JSON.parse(out.result?.content?.[0]?.text ?? '{}');
}

await fetch(`${API}/api/scenario/reset`, { method: 'POST', headers: human });
await sleep(1500);
const baseline = (await graph()).nodes.length;

/* ---------------- A: Claude writes ---------------- */
console.log('\nSession A: Claude records the policy in Quorum');
const A = claude(
  [
    'You are a support agent for Acme. Use the quorum MCP tools as the team shared memory. Always pass token="token_agent_scout".',
    'Record these facts with remember_fact, each with a clear key, value and one sentence of text:',
    '1. The refund window for physical goods is 30 days from purchase (id fact_e2e_window).',
    '2. Digital goods are refundable within 7 days if not downloaded (id fact_e2e_digital).',
    'Then call derive once, with is_decision=true, text "Refunds after 30 days are declined unless a manager approves", and both fact ids as parent_ids (id conc_e2e_rule).',
    'Reply with just the three ids you stored.',
  ].join('\n'),
);
check('Claude called remember_fact and derive', A.tools.filter((t) => t.name === 'remember_fact').length >= 2 && A.tools.some((t) => t.name === 'derive'), A.tools.map((t) => t.name).join(','));
let g = await graph();
const ids = new Set(g.nodes.map((n) => n.id));
check('the facts and the conclusion are in the graph', ['fact_e2e_window', 'fact_e2e_digital', 'conc_e2e_rule'].every((i) => ids.has(i)));
check('the conclusion is linked to both facts', g.edges.filter((e) => e.target === 'conc_e2e_rule').length === 2);
check('they were written by the agent token Claude was given', g.nodes.find((n) => n.id === 'fact_e2e_window')?.asserted_by?.id === 'agent_scout');

/* ---------------- B: a fresh Claude reads ---------------- */
console.log('\nSession B: a fresh Claude, no memory of A, answers a customer');
const B = claude(
  [
    'You are a support agent for Acme with access to the quorum MCP tools (shared team memory). Pass token="token_agent_scout".',
    'A customer bought a physical item 40 days ago and wants a refund. Call retrieve_context first (query "refund window", k=5), then answer in two sentences and cite the fact ids you relied on.',
  ].join('\n'),
);
check('B retrieved context from Quorum', B.tools.some((t) => t.name === 'retrieve_context'));
check('B answered from what A stored (30 days, so not eligible)', /30/.test(B.answer) && /(not|declin|outside|past|exceed|ineligible|cannot|can't)/i.test(B.answer), B.answer.replace(/\s+/g, ' ').slice(0, 140));
check('B cited a fact id', /fact_e2e_window/.test(B.answer));

/* ---------------- C: poison, quarantine, a clean answer ---------------- */
console.log('\nSession C: a wrong fact is planted, an auditor reverts it, a fresh Claude answers');
const bad = await mcpCall('remember_fact', { key: 'partner_feed_refund_terms', value: '90 days', text: 'Partner feed: the refund window for physical goods is 90 days.', token: 'token_agent_scout', id: 'fact_e2e_bad90' });
check('a wrong fact is planted (a different key, so the write-time contradiction check cannot catch it)', bad.fact_id === 'fact_e2e_bad90' && bad.status === 'HEALTHY', JSON.stringify(bad).slice(0, 80));
const poisoned = claude(
  [
    'You are a support agent for Acme with access to the quorum MCP tools. Pass token="token_agent_scout".',
    'A teammate asks what Quorum currently says about the refund window for physical goods. Call retrieve_context (query "refund window physical goods", k=10) and summarise each fact it returns, with its id and value. If values disagree, say so.',
  ].join('\n'),
);
console.log('   before-revert answer:', poisoned.answer.replace(/\s+/g, ' ').slice(0, 600));
const direct0 = await mcpCall('retrieve_context', { query: 'refund window physical goods', token: 'token_agent_scout', k: 10 });
check('before the revert, Quorum hands the wrong fact to an agent', JSON.stringify(direct0).includes('fact_e2e_bad90'));
// Informational only: whether a given Claude session chooses to call a tool is not something Quorum controls.
console.log(`   Claude, asked the same thing before the revert: ${poisoned.tools.some((t) => t.name === 'retrieve_context') ? (poisoned.results.includes('fact_e2e_bad90') ? 'called retrieve_context and was handed the wrong fact' : 'called retrieve_context with a k that missed it') : 'did not call retrieve_context this time'}`);

const q = await mcpCall('quarantine', { root_id: 'fact_e2e_bad90', token: 'token_auditor', reason: 'Unverified partner feed' });
check('the auditor quarantines it', q.success === true);
await sleep(1500);

const C = claude(
  [
    'You are a support agent for Acme with access to the quorum MCP tools. Pass token="token_agent_scout".',
    'Call retrieve_context with query "refund window physical goods" and k=10, then state the refund window in one sentence and list the fact ids you used.',
  ].join('\n'),
);
check('after the revert, Quorum no longer hands it over', C.tools.some((t) => t.name === 'retrieve_context') && !C.results.includes('fact_e2e_bad90'));
check('and the answer does not repeat it', !/fact_e2e_bad90/.test(C.answer) && !/\b90\b/.test(C.answer), C.answer.replace(/\s+/g, ' ').slice(0, 140));
console.log('   after-revert tools:', C.tools.map((t) => t.name).join(',') || '(none)');
console.log('   after-revert answer:', C.answer.replace(/\s+/g, ' ').slice(0, 400));
check('and the right one is', /30/.test(C.answer));

g = await graph();
check('the console graph shows it quarantined', g.nodes.find((n) => n.id === 'fact_e2e_bad90')?.status === 'QUARANTINED');
const direct = spawnSync('docker', ['exec', 'quorum-falkordb', 'redis-cli', 'GRAPH.QUERY', 'quorum:acme', "MATCH (n:QNode {id:'fact_e2e_bad90'}) RETURN n.status"], { encoding: 'utf8' }).stdout ?? '';
check('FalkorDB holds it as QUARANTINED (queried with redis-cli, not through the API)', direct.includes('QUARANTINED'), direct.replace(/\s+/g, ' ').slice(0, 80));

/* ---------------- see it in the console ---------------- */
const browser = await chromium.launch({ channel: process.env.QUORUM_BROWSER ?? 'msedge' });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
await page.goto(`${BASE}/#/app`);
await page.locator('.story-btn').first().waitFor({ timeout: 15000 });
await page.getByRole('button', { name: 'List view' }).click();
await sleep(1200);
const rows = await page.locator('.list-row').allInnerTexts();
check('the console lists what Claude wrote', rows.some((t) => t.includes('30 days')) && rows.some((t) => /quarantined/i.test(t)));
await page.screenshot({ path: '../docs/screens/claude-e2e-console.png' });
await page.keyboard.press('7');
await sleep(1200);
await page.screenshot({ path: '../docs/screens/claude-e2e-feed.png' });
await browser.close();

const total = [A, B, poisoned, C].reduce((s, r) => s + r.cost, 0);
console.log(`\nClaude sessions: 4, tool calls: ${[A, B, poisoned, C].reduce((s, r) => s + r.tools.length, 0)}, reported cost: $${total.toFixed(3)}`);
const failed = results.filter((r) => !r.ok);
console.log(`${results.length - failed.length}/${results.length} checks passed`);
void baseline;
process.exit(failed.length ? 1 : 0);
