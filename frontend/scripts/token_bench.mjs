/**
 * Does Quorum's compact context actually save tokens for a real AI? A measured comparison, not an estimate.
 *
 * The same five questions, answered from the same 160-fact memory by fresh Claude Code sessions in three arms:
 *   plain     only retrieve_context with the default full format and k=8: what a typical top-8 memory returns
 *   compact   retrieve_context(format="compact", budget_tokens=400)
 *   mapped    get_map once, then the compact calls
 *
 * Two kinds of numbers are reported. Tool-result tokens come from Quorum's own call log (what the tools returned, by
 * the same estimator everywhere). Session totals come from the `claude` CLI's usage report (what was actually
 * billed, including Claude Code's own large fixed prompt, which Quorum cannot change).
 *
 *   node scripts/token_bench.mjs [API] [RUNS_PER_ARM]     costs real Claude usage (a few dollars at the default)
 */
import { spawnSync } from 'node:child_process';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const API = process.argv[2] ?? 'http://127.0.0.1:8000';
const RUNS = Number(process.argv[3] ?? 2);
const SCENARIO = process.argv[4] ?? 'short'; // 'long' gives every fact a realistic paragraph of background, like real documents
const BG = (n) =>
  SCENARIO === 'long'
    ? ` Background: this figure was agreed after the quarterly policy review (item ${n}). Finance, support and legal compared the previous period with current volumes, looked at complaint rates by region, reviewed the cost of exceptions, and recorded that the team should apply the number consistently unless a manager approves a documented exception in writing. The review also noted that customer communication templates must quote the same figure, that audit logs keep the approval, and that any change needs a new version of the policy document before agents act on it.`
    : '';
const human = { Authorization: 'Bearer token_human' };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const J = (r) => r.json();

/* ---- MCP client ---- */
let sid = null;
let rid = 0;
async function mcp(name, args) {
  const post = (body) =>
    fetch(`${API}/mcp`, { method: 'POST', headers: { 'content-type': 'application/json', accept: 'application/json, text/event-stream', ...(sid ? { 'mcp-session-id': sid } : {}) }, body: JSON.stringify(body) });
  if (!sid) {
    const r = await post({ jsonrpc: '2.0', id: ++rid, method: 'initialize', params: { protocolVersion: '2025-03-26', capabilities: {}, clientInfo: { name: 'token-bench', version: '1' } } });
    sid = r.headers.get('mcp-session-id');
    await post({ jsonrpc: '2.0', method: 'notifications/initialized' });
  }
  const r = await post({ jsonrpc: '2.0', id: ++rid, method: 'tools/call', params: { name, arguments: args } });
  const t = await r.text();
  const line = t.split('\n').find((l) => l.startsWith('data:'));
  const out = JSON.parse(line ? line.slice(5) : t);
  return JSON.parse(out.result?.content?.[0]?.text ?? '{}');
}

/* ---- the memory: 8 topics x 20 facts, with a conclusion per topic ---- */
const TOPICS = [
  ['refund window', 'refunds'], ['shipping time', 'shipments'], ['warranty length', 'claims'], ['data retention period', 'exports'],
  ['escalation response time', 'tickets'], ['trial length', 'accounts'], ['invoice payment term', 'invoices'], ['password reset validity', 'requests'],
];
const TARGETS = [
  { topic: 0, region: 2 },
  { topic: 2, region: 4 },
  { topic: 3, region: 1 },
  { topic: 6, region: 3 },
  { topic: 5, region: 0 },
].map((t) => ({ ...t, days: 17 + t.topic * 6 + t.region }));

const run = Date.now().toString(36).slice(-4);
console.log('Setting up: reset, two members, a 160-fact memory…');
await fetch(`${API}/api/scenario/reset`, { method: 'POST', headers: human });
await sleep(1500);
const ws = await fetch(`${API}/api/workspace/teams`, { method: 'POST', headers: { ...human, 'content-type': 'application/json' }, body: JSON.stringify({ name: `Bench ${run}`, seats: 4 }) }).then(J);
const team = ws.teams.find((t) => t.name === `Bench ${run}`);
const issue = async (name) => {
  const r = await fetch(`${API}/api/workspace/members`, { method: 'POST', headers: { ...human, 'content-type': 'application/json' }, body: JSON.stringify({ name, role: 'agent', team_id: team.id, client: 'claude-code', device: 'bench' }) }).then(J);
  return { token: r.issued.token, id: r.issued.member.id, name };
};
const arms = { plain: await issue(`Plain${run}`), compact: await issue(`Compact${run}`), mapped: await issue(`Mapped${run}`) };

for (let ti = 0; ti < TOPICS.length; ti++) {
  const [label, thing] = TOPICS[ti];
  const ids = [];
  for (let region = 0; region < 5; region++) {
    const days = 17 + ti * 6 + region; // unique per (topic, region): every question has exactly one right answer
    const id = `b_${ti}_r${region}`;
    await mcp('remember_fact', { key: `${label.replace(/ /g, '_')}_R${region}`, value: String(days), text: `The ${label} for ${thing} in region R${region} is ${days} days. Confirmed by the finance team.${BG(ti * 100 + region)}`, token: 'token_agent_scout', id });
    ids.push(id);
  }
  for (let j = 0; j < 15; j++) {
    // Distractors about the same topic, so a keyword match alone is not enough.
    const id = `b_${ti}_x${j}`;
    await mcp('remember_fact', { key: `${label.replace(/ /g, '_')}_exception_${j}`, value: String(70 + j), text: `Customer C${j} has an exception to the ${label} for ${thing}: ${70 + j} days, ticket ${2000 + ti * 40 + j}, approved by the ${['finance', 'support', 'legal', 'ops'][j % 4]} team.${BG(ti * 100 + 10 + j)}`, token: 'token_agent_scout', id });
    ids.push(id);
  }
  await mcp('derive', { text: `Standard ${label} rule for ${thing}: follow the regional figure, escalate exceptions to a manager.`, parent_ids: ids.slice(0, 3), token: 'token_agent_scout', id: `b_c_${ti}`, is_decision: true });
}
const QUESTIONS = TARGETS.map((t) => `What is the ${TOPICS[t.topic][0]} for ${TOPICS[t.topic][1]} in region R${t.region}? (answer in days)`);

/* ---- one fresh Claude session ---- */
const dir = mkdtempSync(join(tmpdir(), 'quorum-bench-'));
const cfg = join(dir, 'mcp.json');
writeFileSync(cfg, JSON.stringify({ mcpServers: { quorum: { type: 'http', url: `${API}/mcp` } } }));

const list = QUESTIONS.map((q, i) => `${i + 1}. ${q}`).join('\n');
const head = (token) => ['You answer questions from a shared team memory that is available as MCP tools (server "quorum").', `Pass token="${token}" on every call.`];
const tail = ['Answer all five questions, then reply with five lines, "N. <days>" and nothing else.', '', list];
const prompts = {
  plain: (token) => [...head(token), 'For each question call retrieve_context(query, k=8) and read the facts it returns. Do not use any other tool.', ...tail].join('\n'),
  compact: (token) => [...head(token), 'Ask each question with retrieve_context(query, format="compact", budget_tokens=400). Cite ids. Do not load more than you need.', ...tail].join('\n'),
  mapped: (token) => [...head(token), 'Rules: call get_map once at the start. Ask each question with retrieve_context(query, format="compact", budget_tokens=400). Cite ids, do not load more than you need.', ...tail].join('\n'),
};
const allowed = { plain: 'mcp__quorum__retrieve_context', compact: 'mcp__quorum__retrieve_context', mapped: 'mcp__quorum' };

function session(arm) {
  const t0 = Date.now();
  const r = spawnSync('claude', ['-p', prompts[arm](arms[arm].token), '--mcp-config', cfg, '--strict-mcp-config', '--allowedTools', allowed[arm], '--output-format', 'json', '--max-turns', '24', '--no-session-persistence'], {
    encoding: 'utf8',
    timeout: 420000,
    maxBuffer: 30 * 1024 * 1024,
    cwd: dir,
  });
  let out = {};
  try {
    out = JSON.parse(r.stdout);
  } catch {
    console.error('could not parse the claude output:', (r.stdout ?? '').slice(0, 200), (r.stderr ?? '').slice(0, 200));
  }
  const u = out.usage ?? {};
  const answer = out.result ?? '';
  const correct = TARGETS.filter((t, i) => new RegExp(`(^|\\n)\\s*${i + 1}[.)]?\\s*[^\\n]*\\b${t.days}\\b`).test(answer)).length;
  return {
    arm,
    turns: out.num_turns ?? 0,
    cost: out.total_cost_usd ?? 0,
    input: (u.input_tokens ?? 0) + (u.cache_creation_input_tokens ?? 0) + (u.cache_read_input_tokens ?? 0),
    cacheRead: u.cache_read_input_tokens ?? 0,
    output: u.output_tokens ?? 0,
    correct,
    answer: answer.replace(/\s+/g, ' ').slice(0, 120),
    seconds: Math.round((Date.now() - t0) / 1000),
  };
}

const results = [];
for (let i = 0; i < RUNS; i++) {
  const order = ['plain', 'compact', 'mapped'];
  for (const arm of order.slice(i % 3).concat(order.slice(0, i % 3))) {
    process.stdout.write(`run ${i + 1}/${RUNS} ${arm}… `);
    const r = session(arm);
    results.push(r);
    console.log(`${r.correct}/5 correct, ${r.turns} turns, ${r.input.toLocaleString()} input tokens, $${r.cost.toFixed(3)}`);
  }
}

/* ---- what the tools themselves returned, from Quorum's call log ---- */
const calls = await fetch(`${API}/api/mcp/activity?limit=500`, { headers: human }).then(J);
const byArm = {};
for (const arm of Object.keys(arms)) {
  const mine = calls.filter((c) => c.member_id === arms[arm].id);
  const reads = mine.filter((c) => ['retrieve_context', 'get_map', 'get_node'].includes(c.tool));
  byArm[arm] = {
    calls: mine.length,
    tool_tokens: reads.reduce((s, c) => s + c.tokens, 0),
    baseline_tokens: reads.reduce((s, c) => s + c.baseline, 0),
    retrievals: mine.filter((c) => c.tool === 'retrieve_context').length,
  };
}

const med = (a) => [...a].sort((x, y) => x - y)[Math.floor(a.length / 2)] ?? 0;
const summary = {};
for (const arm of Object.keys(arms)) {
  const rs = results.filter((r) => r.arm === arm);
  summary[arm] = {
    runs: rs.length,
    correct_median: med(rs.map((r) => r.correct)),
    turns_median: med(rs.map((r) => r.turns)),
    session_input_tokens_median: med(rs.map((r) => r.input)),
    session_output_tokens_median: med(rs.map((r) => r.output)),
    cost_usd_median: Number(med(rs.map((r) => r.cost)).toFixed(3)),
    tool_result_tokens_per_run: Math.round(byArm[arm].tool_tokens / Math.max(1, rs.length)),
    tool_calls_per_run: Math.round(byArm[arm].calls / Math.max(1, rs.length)),
  };
}
const pctLess = (a, b) => Math.round((1 - a / Math.max(1e-9, b)) * 100);
const vs = (arm) => ({
  tool_result_saving_pct: pctLess(summary[arm].tool_result_tokens_per_run, summary.plain.tool_result_tokens_per_run),
  session_input_saving_pct: pctLess(summary[arm].session_input_tokens_median, summary.plain.session_input_tokens_median),
  cost_saving_pct: pctLess(summary[arm].cost_usd_median, summary.plain.cost_usd_median),
});
const report = {
  generated_at: Date.now(),
  runs_per_arm: RUNS,
  scenario: SCENARIO,
  memory: { facts: 160, conclusions: 8, fact_text: SCENARIO === 'long' ? 'about 120 words each' : 'one or two sentences' },
  questions: QUESTIONS.length,
  summary,
  versus_plain: { compact: vs('compact'), mapped: vs('mapped') },
  sessions: results,
  note: 'Session totals include Claude Code\'s own fixed prompt (about 37k tokens, re-read every turn), which Quorum cannot change. Tool-result tokens are what Quorum controls.',
};
writeFileSync(new URL(`../../bench/token_bench_${SCENARIO}.json`, import.meta.url), JSON.stringify(report, null, 2));

console.log('\n                          plain        compact      compact+map');
const row = (label, f) => console.log(`${label.padEnd(25)} ${String(f('plain')).padEnd(12)} ${String(f('compact')).padEnd(12)} ${f('mapped')}`);
row('correct answers (median)', (a) => `${summary[a].correct_median}/5`);
row('turns (median)', (a) => summary[a].turns_median);
row('tool-result tokens/run', (a) => summary[a].tool_result_tokens_per_run);
row('session input tokens', (a) => summary[a].session_input_tokens_median.toLocaleString());
row('cost (median)', (a) => `$${summary[a].cost_usd_median}`);
for (const arm of ['compact', 'mapped']) {
  const v = report.versus_plain[arm];
  console.log(`${arm}: tool results ${v.tool_result_saving_pct}% fewer tokens · session input ${v.session_input_saving_pct}% · cost ${v.cost_saving_pct}% (negative means more)`);
}

/* ---- leave the workspace as we found it ---- */
for (const a of Object.values(arms)) await fetch(`${API}/api/workspace/members/${a.id}`, { method: 'DELETE', headers: human });
await fetch(`${API}/api/workspace/teams/${team.id}`, { method: 'DELETE', headers: human });
