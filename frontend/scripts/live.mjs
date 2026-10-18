/**
 * End-to-end check of the console against a LIVE backend, with a real MCP client acting as an outside agent.
 *
 *   Needs: backend on :8000 started with QUORUM_EMBED_MCP=1 (so MCP and the API share one graph),
 *          the console built with VITE_API_MODE=live and served on BASE (default http://127.0.0.1:5174)
 *          with an explicitly enabled development demo sandbox (see docs/LIVE_TESTING.md).
 *
 *   node scripts/live.mjs [BASE] [API]
 */
import { chromium } from 'playwright-core';

const BASE = process.argv[2] ?? 'http://127.0.0.1:5174';
const API = process.argv[3] ?? 'http://127.0.0.1:8000';
const results = [];
const check = (name, ok, extra = '') => {
  results.push({ name, ok });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${extra ? `  ${extra}` : ''}`);
};
const human = { Authorization: 'Bearer token_human' };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/* ---- a tiny MCP client over streamable HTTP, standing in for Claude Code / Cursor ---- */
let sid = null;
let rid = 0;
async function rpc(method, params, notify = false) {
  const body = { jsonrpc: '2.0', method, ...(params ? { params } : {}), ...(notify ? {} : { id: ++rid }) };
  const res = await fetch(`${API}/mcp`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', accept: 'application/json, text/event-stream', ...(sid ? { 'mcp-session-id': sid } : {}) },
    body: JSON.stringify(body),
  });
  sid = res.headers.get('mcp-session-id') ?? sid;
  if (notify) return null;
  const text = await res.text();
  const line = text.split('\n').find((l) => l.startsWith('data:'));
  return JSON.parse(line ? line.slice(5) : text);
}
async function tool(name, args) {
  const out = await rpc('tools/call', { name, arguments: args });
  const t = out.result?.content?.[0]?.text ?? '{}';
  try {
    return JSON.parse(t);
  } catch {
    return { raw: t };
  }
}

await fetch(`${API}/api/scenario/reset`, { method: 'POST', headers: human });
await sleep(1200);

const browser = await chromium.launch({ channel: process.env.QUORUM_BROWSER ?? 'msedge' });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const problems = [];
page.on('pageerror', (e) => problems.push(`pageerror ${e}`));
page.on('console', (m) => m.type() === 'error' && !/Failed to load resource/.test(m.text()) && problems.push(`console ${m.text()}`));
const bad = [];
page.on('response', (r) => r.status() >= 400 && !r.url().includes('/mcp') && !(r.status() === 409 && r.url().includes('/api/workspace/teams')) && bad.push(`${r.status()} ${r.url().replace(BASE, '')}`));

const btn = (n) => page.getByRole('button', { name: n });
const text = (sel) => page.locator(sel).first().innerText();
const waitEnabled = async (n, ms = 30000) => {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    if (await btn(n).first().isEnabled().catch(() => false)) return true;
    await sleep(300);
  }
  return false;
};

await page.goto(`${BASE}/#/app`);
try {
  await page.locator('.story-btn').first().waitFor({ timeout: 15000 });
} catch {
  // If story button is not immediately visible (e.g. initial view landed on overview), switch to console
  await page.keyboard.press('1');
  await page.locator('.story-btn').first().waitFor({ timeout: 15000 });
}
await sleep(1500);

/* ---- live mode, not sample mode ---- */
check('no sample-data chip in live mode', (await page.locator('.pill.sample', { hasText: /sample data/i }).count()) === 0);
check('event stream connected', (await page.locator('.pill.conn.live').count()) === 1);
check('header shows real savings, not a simulation', (await page.locator('.pill.saved').getAttribute('title'))?.includes('real read calls') === true);

/* ---- Connect page: real handshake with the real server ---- */
await page.keyboard.press('7');
await page.locator('.cn-status').waitFor({ timeout: 4000 });
await sleep(1500);
const status = await text('.cn-status');
check('MCP status is a real, connected handshake', status.includes('Connected') && !status.includes('simulated'), status.split('\n')[0]);
check('live feed is labelled as coming from the backend, not simulated', !(await page.locator('.cn-feeds .chip.warn', { hasText: 'Simulated' }).count()));

/* ---- an outside agent over MCP ---- */
await rpc('initialize', { protocolVersion: '2025-03-26', capabilities: {}, clientInfo: { name: 'live-qa', version: '1' } });
await rpc('notifications/initialized', undefined, true);
const listed = (await rpc('tools/list', {})).result.tools.map((t) => t.name);
check('MCP server lists all 28 tools (memory, token savers, observe, chat layer)', listed.length === 28 && ['quorum_help', 'quorum_start', 'quorum_status', 'quorum_manage', 'quorum_connect', 'peer_retrieve'].every((n) => listed.includes(n)) && ['get_map', 'get_node', 'find_path', 'delta', 'observe'].every((n) => listed.includes(n)), listed.join(','));
await page.keyboard.press('1');
await sleep(500);
await page.getByRole('button', { name: 'List view' }).click();
const wrote = await tool('remember_fact', { key: 'qa_window', value: '45', text: 'QA agent says the refund window is 45 days', token: 'token_agent_scout', id: 'fact_qa_mcp' });
check('agent can write a fact over MCP', wrote.fact_id === 'fact_qa_mcp', JSON.stringify(wrote).slice(0, 80));
await sleep(2500);
check('that fact appears in the console without a reload', (await page.locator('.list-row', { hasText: 'QA agent says' }).count()) > 0);
const got = await tool('retrieve_context', { query: 'refund window days', token: 'token_agent_scout', k: 5 });
check('agent can read it back with retrieve_context', JSON.stringify(got).includes('fact_qa_mcp'));
const blocked = await tool('quarantine', { root_id: 'fact_qa_mcp', token: 'token_agent_scout' });
check('an agent token cannot quarantine', blocked.success === false, blocked.error ?? '');
const q = await tool('quarantine', { root_id: 'fact_qa_mcp', token: 'token_auditor' });
check('an auditor token can quarantine', q.success === true);
await sleep(2500);
const afterQ = await tool('retrieve_context', { query: 'QA agent refund window', token: 'token_agent_scout', k: 5 });
check('retrieval no longer returns the quarantined fact', !JSON.stringify(afterQ).includes('fact_qa_mcp'));
check('console shows the quarantine', (await page.locator('.list-row', { hasText: 'QA agent says' }).first().innerText()).toLowerCase().includes('quarantined'));
await fetch(`${API}/api/scenario/reset`, { method: 'POST', headers: human });
await sleep(2500);

/* ---- the 90-second story against the real backend and real agents ---- */
for (const step of ['Inject poisoned webhook', 'Run agents', 'Audit memory', 'Replace policy']) {
  const ok = await waitEnabled(step);
  check(`step enabled in order: ${step}`, ok);
  if (!ok) break;
  await btn(step).first().click();
  await sleep(2500);
}
await sleep(1500);
const feed = await text('.feed');
check('feed shows the quarantine and the replacement', feed.includes('Quarantined') && feed.includes('Replaced'), feed.replace(/\s+/g, ' ').slice(0, 90));
check('scenario is not stuck running', !(await text('.progress')).toLowerCase().includes('running'));
check('no "Out of sync" loop', !(await page.locator('.banner, .blocking').allInnerTexts()).join(' ').includes('Out of sync'));

await page.getByRole('tab', { name: 'Insights' }).click();
await sleep(600);
check('Insights counts quarantined nodes from the real graph', Number((await text('.ins-kpi.bad')).match(/\d+/)?.[0] ?? 0) > 0);
await page.getByRole('tab', { name: 'Prompt diff' }).click();
await sleep(1200);
check('Prompt diff renders the before/after from the backend', (await page.locator('.belief-diff, .diff').count()) > 0);
await page.getByRole('tab', { name: /Actions and handoffs/ }).click();
await sleep(1200);
check('Actions and handoffs lists real handoffs', (await page.locator('.ledger, .handoff, .board li').count()) > 0);
await page.getByRole('tab', { name: 'Graph' }).click();

/* ---- enterprise: teams, members with real tokens, team scope, links, presence, token savings ---- */
const run = Date.now().toString(36).slice(-5);
const alpha = `Alpha ${run}`;
const beta = `Beta ${run}`;
await page.keyboard.press('8');
await page.locator('.tm').waitFor({ timeout: 4000 });
for (const name of [alpha, beta]) {
  await page.locator('.tm-form input[placeholder="Customer success"]').fill(name);
  await page.locator('.tm-form').first().getByRole('button', { name: 'Add team' }).click();
  await sleep(500);
}
check('teams are created on the server and listed', (await page.locator('.tm-team', { hasText: alpha }).count()) === 1 && (await page.locator('.tm-team', { hasText: beta }).count()) === 1);
await page.locator('.tm-form input[placeholder="Customer success"]').fill(alpha);
await page.locator('.tm-form').first().getByRole('button', { name: 'Add team' }).click();
await sleep(400);
check('a duplicate team is refused by the server', (await page.locator('.tm-err', { hasText: 'already exists' }).count()) === 1);

async function addMember(name, teamName, role = 'agent') {
  const form = page.locator('.tm-form').nth(1);
  await form.getByLabel('Name').fill(name);
  await form.getByLabel('Team').selectOption({ label: teamName });
  await form.getByLabel('Role').selectOption({ value: role });
  await form.getByRole('button', { name: 'Add member' }).click();
  await page.locator('.tm-issued code').waitFor({ timeout: 4000 });
  const token = (await text('.tm-issued code')).trim();
  await page.getByRole('button', { name: 'I have saved it' }).click();
  return token;
}
const miraName = `Mira${run}`;
const aishaName = `Aisha${run}`;
const miraToken = await addMember(miraName, alpha);
const aishaToken = await addMember(aishaName, beta);
check('adding a member shows a real token once', miraToken.startsWith('qrm_acme_') && miraToken !== aishaToken);
check('the token is not shown again after reload', !(await page.locator('.tm').innerText()).includes(miraToken));

const memberCall = (token, name, args) => tool(name, { token, ...args });
const teamWrite = await memberCall(miraToken, 'remember_fact', { key: 'floor', value: '40', text: `Alpha-only pricing floor ${run} is 40 percent`, id: `f_priv_${run}`, scope: 'team' });
check('a member token writes a team-private fact', teamWrite.fact_id === `f_priv_${run}`, JSON.stringify(teamWrite).slice(0, 80));
const seeAisha = await memberCall(aishaToken, 'retrieve_context', { query: `pricing floor ${run}`, format: 'compact' });
check('another team cannot read it', !JSON.stringify(seeAisha).includes('40 percent'));
const seeMira = await memberCall(miraToken, 'retrieve_context', { query: `pricing floor ${run}`, format: 'compact' });
check('its own team can', JSON.stringify(seeMira).includes('40 percent'));

const form = page.locator('.tm-mesh .tm-form');
await form.getByLabel('From').selectOption({ label: miraName });
await form.getByLabel('To').selectOption({ label: aishaName });
await form.getByRole('button', { name: 'Link' }).click();
await sleep(600);
const linked = await memberCall(aishaToken, 'retrieve_context', { query: `pricing floor ${run}`, format: 'compact' });
check('linking the two members shares the team fact across laptops', JSON.stringify(linked).includes('40 percent'));
await page.locator('.tm-links li').first().getByRole('button', { name: 'Pause' }).click();
await sleep(600);
check('pausing the link ends access at once', !JSON.stringify(await memberCall(aishaToken, 'retrieve_context', { query: `pricing floor ${run}`, format: 'compact' })).includes('40 percent'));
await page.locator('.tm-links li').first().getByRole('button', { name: 'Unlink' }).click();
await sleep(400);

// Token savings need something to save: a few dozen facts, then compact reads against them.
for (let i = 0; i < 40; i++) {
  await tool('remember_fact', { key: `refund_${i}`, value: String(10 + i), text: `Ticket ${1000 + i} says the refund window for region R${i % 5} is ${10 + i} days, confirmed by email.`, token: 'token_agent_scout', id: `f_sav_${run}_${i}` });
}
for (let i = 0; i < 3; i++) await tool('retrieve_context', { query: 'refund window days', token: 'token_agent_scout', format: 'compact', budget_tokens: 250 });
await sleep(1500);
const stats = await fetch(`${API}/api/token-stats`, { headers: human }).then((r) => r.json());
check('the backend measured real savings on those reads', stats.saved > 0 && stats.measured_calls >= 3, `${stats.saved} tokens, ${stats.saved_pct}% fewer than a top-8 retriever`);
const full = await tool('retrieve_context', { query: 'refund window days', token: 'token_agent_scout', k: 8 });
const compact = await tool('retrieve_context', { query: 'refund window days', token: 'token_agent_scout', format: 'compact', budget_tokens: 250 });
check('compact format costs under 60% of the full format for the same question', compact.tokens < full.tokens * 0.6, `${compact.tokens} vs ${full.tokens} tokens`);
const mp = await tool('get_map', { token: 'token_agent_scout' });
check('get_map is a small index', mp.map.includes('## areas') && mp.tokens < 300 && mp.tokens < mp.tokens_baseline / 4, `${mp.tokens} tokens vs ${mp.tokens_baseline} to paste the graph`);
const nd = await tool('get_node', { id: 'F1', token: 'token_agent_scout' });
check('get_node answers by short id', nd.success === true && nd.text.length > 0);
await tool('retrieve_context', { query: 'refund window days', token: 'token_agent_scout', format: 'compact' });
await fetch(`${API}/api/scenario/reset`, { method: 'POST', headers: human });
await sleep(1200);
const dl = await tool('delta', { token: 'token_agent_scout' });
check('delta tells an agent what stopped being true', dl.invalidated.length > 0, dl.text.split('\n').slice(0, 2).join(' | '));

await page.keyboard.press('8');
await sleep(1500);
const row = page.locator('.tm-table tbody tr', { hasText: miraName });
check('presence comes from real calls', /online|idle/.test(await row.innerText()), (await row.innerText()).replace(/\s+/g, ' ').slice(0, 90));
check('per-member call counts are real', Number((await row.locator('td.num').first().innerText()).replace(/[^0-9]/g, '')) >= 2);
await page.keyboard.press('7');
await sleep(1500);
check('the Connect feed lists real tool calls with their tokens', (await page.locator('.cn-row').count()) >= 3 && /tokens|tok/.test(await text('.cn-list')));
await page.getByRole('tab', { name: 'Sessions' }).click();
await page.locator('.cn-people button', { hasText: miraName }).click();
await sleep(1200);
check("a member's session shows their tool calls", (await page.locator('.cn-turn').count()) >= 2);
check('Connect reads the tool list from the hub', (await page.locator('.cn-table tbody tr').count()) === 28 && (await text('.card-sub >> nth=-1')).length > 0 && (await page.locator('.cn-table tbody th', { hasText: /^observe$/ }).count()) === 1);
check('Connect shows the hub policy (header, limit, ranking)', /Authorization header/.test(await text('.cn')) && /tool calls a minute/.test(await text('.cn')));

/* ---- header auth, passive capture, semantic ranking and the audit log, on the live hub ---- */
const hdr = (tok) => ({ 'content-type': 'application/json', accept: 'application/json, text/event-stream', Authorization: `Bearer ${tok}` });
const hinit = await fetch(`${API}/mcp`, { method: 'POST', headers: hdr(miraToken), body: JSON.stringify({ jsonrpc: '2.0', id: 91, method: 'initialize', params: { protocolVersion: '2025-03-26', capabilities: {}, clientInfo: { name: 'qa', version: '1' } } }) });
const hsid = hinit.headers.get('mcp-session-id');
await fetch(`${API}/mcp`, { method: 'POST', headers: { ...hdr(miraToken), 'mcp-session-id': hsid }, body: JSON.stringify({ jsonrpc: '2.0', method: 'notifications/initialized' }) });
const hcall = async (name, args) => {
  const r = await fetch(`${API}/mcp`, { method: 'POST', headers: { ...hdr(miraToken), 'mcp-session-id': hsid }, body: JSON.stringify({ jsonrpc: '2.0', id: 92, method: 'tools/call', params: { name, arguments: args } }) });
  const t = await r.text();
  const line = t.split('\n').find((l) => l.startsWith('data:'));
  return JSON.parse(JSON.parse(line ? line.slice(5) : t).result.content[0].text);
};
const viaHeader = await hcall('observe', { text: `The ${run} retention period for audit exports is 400 days and must never be shortened.`, source: 'qa' });
check('a tool call with only an Authorization header works (no token argument)', viaHeader.success === true && viaHeader.stored === 1, JSON.stringify(viaHeader.facts?.[0]?.key));
const badTok = await fetch(`${API}/mcp`, { method: 'POST', headers: hdr('qrm_acme_wrongwrongwrong'), body: '{}' });
check('a wrong header token is refused with 401', badTok.status === 401);
const para = await hcall('retrieve_context', { query: `how many days do we keep audit exports ${run}`, format: 'compact' });
check('a paraphrased question finds the observed fact', /400 days/.test(para.packed ?? ''), (para.packed ?? '').split('\n')[1]?.slice(0, 70));
const sem = await fetch(`${API}/api/context/semantic-check?query=${encodeURIComponent('how long are audit exports retained')}`, { headers: human }).then((r) => r.json());
check("FalkorDB's own vector index agrees with the ranking", sem.agrees === true && sem.falkordb_top?.length > 0, `${sem.falkordb_top?.[0]?.id} (${sem.provider})`);
await page.keyboard.press('9');
await sleep(2200);
check('Activity lists real events in plain words, with the member named', (await page.locator('.au-row').count()) >= 3 && (await text('.au-list')).includes(miraName) && /captured facts|looked up|recorded a fact/.test(await text('.au-list')));
check('Activity does not claim to be sample data', !(await text('.au')).includes('Sample data'));
const csv = await fetch(`${API}/api/audit?format=csv`, { headers: human });
check('the audit log exports as CSV', csv.headers.get('content-type')?.startsWith('text/csv') && (await csv.text()).split('\n').length > 3);
await page.keyboard.press('6');
await sleep(2000);
check('the Context page shows the backend numbers', /\d+/.test(await text('.ctx-pct')) && Number((await text('.ctx-session .ctx-pct')).replace(/\D/g, '')) > 0);

// leave the workspace as we found it
for (const m of (await fetch(`${API}/api/workspace`, { headers: human }).then((r) => r.json())).members.filter((x) => x.name.endsWith(run))) {
  await fetch(`${API}/api/workspace/members/${m.id}`, { method: 'DELETE', headers: human });
}
for (const tm of (await fetch(`${API}/api/workspace`, { headers: human }).then((r) => r.json())).teams.filter((x) => x.name.endsWith(run))) {
  await fetch(`${API}/api/workspace/teams/${tm.id}`, { method: 'DELETE', headers: human });
}
await page.keyboard.press('1');
await sleep(500);

/* ---- the other views, on real data ---- */
await page.keyboard.press('2');
await sleep(1500);
check('Memory lists the real agents', (await page.locator('.agent-list li').count()) >= 3);
await page.keyboard.press('3');
await sleep(800);
await page.locator('#ask-q').fill('What is the refund window?');
await page.locator('.askform .btn.primary').click();
await sleep(2500);
check('Company brain answers or refuses with a trace', (await text('.page')).length > 300);
await page.keyboard.press('4');
await sleep(1500);
check('Tenants shows the isolation result', /denied/i.test(await text('.page')));
await page.keyboard.press('5');
await sleep(1500);
check('Benchmark shows recorded results', /ops|p99|ms/i.test(await text('.page')));
await page.keyboard.press('6');
await sleep(1500);
check('Context page works on the live graph', /\d+/.test(await text('.ctx-pct')));
await page.keyboard.press('8');
await sleep(800);
check('Team page renders', (await page.locator('.tm-org').count()) === 1);

/* ---- health ---- */
await page.getByText('Dev console').click();
await sleep(400);
const dev = await text('.dev-drawer');
check('dev console has no validation failures', !/failed validation/.test(dev), dev.split('\n').filter((l) => /failed validation/.test(l)).join(' | '));
check('no failed requests other than MCP probes', bad.length === 0, bad.join(', '));

await browser.close();
const failed = results.filter((r) => !r.ok);
if (problems.length) console.error('PROBLEMS:\n' + problems.join('\n'));
console.log(`\n${results.length - failed.length}/${results.length} live checks passed, ${problems.length} console problems`);
process.exit(failed.length || problems.length ? 1 : 0);
