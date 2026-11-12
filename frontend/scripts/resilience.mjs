/**
 * Pulls FalkorDB out from under a running product and checks that the product locks, says why, and recovers.
 *
 *   Needs the live stack (see docs/LIVE_TESTING.md) and Docker with a container named quorum-falkordb.
 *   node scripts/resilience.mjs [BASE] [API]
 */
import { execSync } from 'node:child_process';
import { chromium } from 'playwright-core';

const BASE = process.argv[2] ?? 'http://127.0.0.1:5174';
const API = process.argv[3] ?? 'http://127.0.0.1:8000';
const results = [];
const check = (name, ok, extra = '') => {
  results.push({ name, ok });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${extra ? `  ${extra}` : ''}`);
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const human = { Authorization: 'Bearer token_human' };
const docker = (a) => execSync(`docker ${a}`, { stdio: 'pipe' }).toString();

async function until(fn, ms = 30000, step = 400) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    if (await fn().catch(() => false)) return true;
    await sleep(step);
  }
  return false;
}

/* a tiny MCP client */
let sid = null;
let rid = 0;
async function mcp(method, params, notify = false) {
  const res = await fetch(`${API}/mcp`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', accept: 'application/json, text/event-stream', ...(sid ? { 'mcp-session-id': sid } : {}) },
    body: JSON.stringify({ jsonrpc: '2.0', method, ...(params ? { params } : {}), ...(notify ? {} : { id: ++rid }) }),
  });
  sid = res.headers.get('mcp-session-id') ?? sid;
  return res;
}

await fetch(`${API}/api/scenario/reset`, { method: 'POST', headers: human });
await sleep(1500);

const browser = await chromium.launch({ channel: process.env.QUORUM_BROWSER ?? 'msedge' });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
await page.goto(`${BASE}/#/app`);
await page.locator('.story-btn').first().waitFor({ timeout: 15000 });
check('product is up while FalkorDB is running', true);

// A fact written before the outage, so we can prove it is still there afterwards.
const init = await mcp('initialize', { protocolVersion: '2025-03-26', capabilities: {}, clientInfo: { name: 'resilience', version: '1' } });
check('MCP answers while FalkorDB is running', init.status === 200);
await mcp('notifications/initialized', undefined, true);
await mcp('tools/call', { name: 'remember_fact', arguments: { key: 'outage', value: 'kept', text: 'Fact written before the outage', token: 'token_agent_scout', id: 'fact_before_outage' } });
await sleep(1500);

/* ---- pull the database ---- */
docker('stop quorum-falkordb');
const t0 = Date.now();
check('console is replaced by the setup screen', await until(() => page.locator('.block-screen').isVisible(), 20000), `${Math.round((Date.now() - t0) / 1000)}s`);
check('the product is not shown behind it', (await page.locator('.story-btn').count()) === 0);
check('it says what is wrong', /FalkorDB/.test(await page.locator('.block-screen').innerText()));
check('it shows the commands to fix it', (await page.locator('.block-cmd code').count()) > 0);
check('the screen is an accessible blocking dialog', (await page.locator('.block-screen[role="alertdialog"]').count()) === 1);

const g = await fetch(`${API}/api/graph`, { headers: human });
const gb = await g.json();
check('API refuses data calls with 503 FALKORDB_UNAVAILABLE', g.status === 503 && gb.error?.code === 'FALKORDB_UNAVAILABLE');
const w = await fetch(`${API}/api/scenario/inject_poison`, { method: 'POST', headers: human });
check('API refuses writes too', w.status === 503);
const h = await fetch(`${API}/api/health`);
check('health reports blocked and stays reachable', h.status === 503 && (await h.json()).status === 'blocked');
const m = await mcp('tools/list', {});
check('MCP is refused as well', m.status === 503, `HTTP ${m.status}`);
await page.screenshot({ path: '../docs/screens/block-falkordb.png' });

/* ---- bring it back ---- */
docker('start quorum-falkordb');
const t1 = Date.now();
check('console reopens by itself when FalkorDB is back', await until(() => page.locator('.story-btn').first().isVisible(), 45000), `${Math.round((Date.now() - t1) / 1000)}s`);
check('setup screen is gone', (await page.locator('.block-screen').count()) === 0);
const again = await fetch(`${API}/api/graph`, { headers: human }).then((r) => r.json());
check('data written before the outage is still there', again.nodes?.some((n) => n.id === 'fact_before_outage'));
check('MCP works again', (await mcp('tools/list', {})).status === 200);

await browser.close();
const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} resilience checks passed`);
process.exit(failed.length ? 1 : 0);
