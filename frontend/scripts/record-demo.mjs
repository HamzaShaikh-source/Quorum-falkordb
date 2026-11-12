// Records the demo video (WebM) from the real console and real tool output.
//   node scripts/record-demo.mjs <demo-console-url> <demo-api-url> <transcript.json> <out-dir>
// The console must run with QUORUM_DEMO=1 in development (the guided story and sandbox sign-in). The transcript comes from
// `python scripts/e2e_connect.py ... --transcript transcript.json` run against two real hubs.
import { chromium } from 'playwright-core';
import { readFileSync, mkdirSync } from 'node:fs';

const [BASE, API, TRANSCRIPT, OUT] = process.argv.slice(2);
mkdirSync(OUT, { recursive: true });
const steps = JSON.parse(readFileSync(TRANSCRIPT, 'utf8'));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const human = { Authorization: 'Bearer token_human' };

const browser = await chromium.launch({ channel: process.env.QUORUM_BROWSER ?? 'msedge' });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, recordVideo: { dir: OUT, size: { width: 1440, height: 900 } } });
const page = await ctx.newPage();
await page.addInitScript(() => { try { localStorage.setItem('quorum_tour_seen', 'true'); } catch { /* private mode */ } });

async function caption(text, ms = 3200) {
  await page.evaluate((t) => {
    let el = document.getElementById('__cap');
    if (!el) {
      el = document.createElement('div');
      el.id = '__cap';
      el.setAttribute('role', 'status');
      el.style.cssText = 'position:fixed;left:50%;bottom:28px;transform:translateX(-50%);z-index:2147483647;max-width:1100px;padding:14px 22px;background:#111827;color:#fff;font:600 22px/1.35 system-ui,sans-serif;border-radius:14px;box-shadow:4px 4px 0 rgba(17,24,39,.25);text-align:center';
      document.body.appendChild(el);
    }
    el.textContent = t;
  }, text);
  await sleep(ms);
}
const btn = (n) => page.getByRole('button', { name: n, exact: false });
async function click(name) {
  const b = btn(name).first();
  for (let i = 0; i < 100 && !(await b.isEnabled().catch(() => false)); i++) await sleep(300);
  await b.click();
}

// 1. what it is
await page.goto(`${BASE}/`);
await sleep(1200);
await caption('Quorum: shared memory for AI agents, with a one-step undo for wrong facts.', 4200);

// 2. the poisoned fact, contained
await fetch(`${API}/api/scenario/reset`, { method: 'POST', headers: human });
await page.goto(`${BASE}/#/app`);
await page.locator('.story-btn').first().waitFor({ timeout: 20000 });
await sleep(1500);
await caption('A partner feed says refunds are allowed for 90 days. The real policy says 30.', 3000);
await click('Inject poisoned webhook');
await sleep(1800);
await click('Run agents');
await caption('Agents build conclusions and a refund action on the wrong fact.', 3200);
await click('Audit memory');
await sleep(2500);
await caption('The auditor quarantines it. FalkorDB walks the causal chain: everything built on it goes too.', 4200);
await click('Replace policy');
await caption('The correct policy replaces it. The refund that already ran is compensated automatically.', 4200);

// 3. a teammate asks to connect, in the console
await fetch(`${API}/api/connect/request`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ person: 'Dana Ortiz', hub: "Dana's Quorum", message: 'Hi, I run the billing agents' }) });
await page.keyboard.press('8');
await page.locator('.nw').waitFor({ timeout: 15000 });
await sleep(1500);
await caption('Teammates on your network can ask to connect. You are notified and decide.', 3600);
await page.getByRole('button', { name: 'Approve' }).first().click();
await caption('Approved at a permission level you choose. They get their access automatically.', 3600);

// 4. the AI-first path
await page.keyboard.press('7');
await page.locator('.ai-prompt').waitFor({ timeout: 15000 });
await page.getByRole('button', { name: /Read it first/ }).click();
await caption('Or skip the console: paste one prompt into any AI and it sets everything up in the chat.', 4800);

// 5. the chat, from real tool output
const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;');
const keep = ['quorum_help', 'quorum_start', 'quorum_discover', 'quorum_connect', 'quorum_inbox', 'quorum_respond', 'quorum_connections', 'peer_retrieve', 'quorum_status', 'quorum_manage'];
const seenTools = new Set();
const shown = steps.filter((s) => keep.includes(s.tool) && !(seenTools.has(s.tool + s.who) || (seenTools.add(s.tool + s.who), false)));
const rows = shown
  .map((s) => `<section class="t"><div class="c">${esc(s.who ? s.who + '  →  ' : '')}${esc(s.tool)}(${esc(Object.entries(s.args).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(', '))})</div><pre>${esc(s.text.split('\n').slice(0, 7).join('\n'))}${s.notice ? `\n\n! ${esc(s.notice)}` : ''}</pre></section>`)
  .join('');
await page.setContent(`<!doctype html><meta charset="utf-8"><title>Chat</title><style>
body{margin:0;background:#f4f5f3;color:#111827;font:16px/1.5 system-ui,sans-serif}
header{padding:22px 40px;border-bottom:1.5px solid #111827;font:600 22px system-ui}
header small{display:block;font:400 14px system-ui;color:#4b5563;margin-top:2px}
main{padding:24px 40px;display:grid;gap:14px;max-width:1100px}
.t{border:1.5px solid #111827;border-radius:14px;background:#fff;overflow:hidden;opacity:0;transform:translateY(8px);transition:all .5s}
.t.on{opacity:1;transform:none}
.c{background:#111827;color:#fff;padding:7px 14px;font:13px ui-monospace,Menlo,monospace}
pre{margin:0;padding:12px 16px;white-space:pre-wrap;font:14px/1.45 ui-monospace,Menlo,monospace}
</style><header>What your AI shows in its own chat<small>Real tool output captured from two live Quorum hubs on one network. Secrets removed; the AI's wording is omitted.</small></header><main>${rows}</main>`);
const total = await page.locator('.t').count();
const WHY = {
  quorum_help: 'quorum_help: every command and what it does.',
  quorum_start: 'quorum_start: a short survey sets up the team.',
  quorum_discover: 'quorum_discover: finds other Quorum hubs on the network.',
  quorum_connect: 'quorum_connect: ask to join one by name.',
  quorum_status: 'quorum_status: what is remembered, shared, and who is asking.',
  quorum_inbox: 'The other admin sees the request in the chat they already use.',
  quorum_respond: 'They approve and pick a permission level. No token is pasted by hand.',
  quorum_connections: 'The first AI is now connected.',
  peer_retrieve: 'peer_retrieve: it reads a teammate shared memory.',
  quorum_manage: 'quorum_manage: people and permission levels, from the chat.',
};
for (let i = 0; i < total; i++) {
  await page.locator('.t').nth(i).evaluate((e) => e.classList.add('on'));
  await page.locator('.t').nth(i).scrollIntoViewIfNeeded();
  await caption(WHY[shown[i].tool] ?? shown[i].tool, 2600);
}
await caption('Discover, connect, approve, read a teammate\'s memory, manage people: all from the chat.', 4200);

// 5b. the real-agent result, read from the evaluation file
const ev = JSON.parse(readFileSync(new URL('../../bench/agent_eval_results.json', import.meta.url), 'utf8')).summary;
await page.setContent(`<!doctype html><meta charset="utf-8"><body style="margin:0;display:grid;place-items:center;height:100vh;background:#f4f5f3;color:#111827;font:600 34px system-ui;text-align:center"><div style="max-width:1000px;padding:0 40px">A real Claude agent, same memory, same correction<div style="display:flex;gap:60px;justify-content:center;margin:34px 0"><div><div style="font:700 96px system-ui">${ev.flat.unsafe_refunds_executed}/${ev.flat.trials}</div><div style="font:400 22px system-ui;color:#4b5563">wrong refunds issued<br>without causal provenance</div></div><div><div style="font:700 96px system-ui;color:#0f766e">${ev.quorum.unsafe_refunds_executed}/${ev.quorum.trials}</div><div style="font:400 22px system-ui;color:#4b5563">wrong refunds issued<br>with Quorum</div></div></div><div style="font:400 18px system-ui;color:#4b5563">One scenario we wrote, small trial counts, outcome measured on the hub. Details in docs/FALKORDB.md.</div></div>`);
await sleep(6500);

// 6. close
await page.setContent('<!doctype html><meta charset="utf-8"><body style="margin:0;display:grid;place-items:center;height:100vh;background:#111827;color:#fff;font:600 40px system-ui;text-align:center"><div>Quorum<div style="font:400 22px system-ui;margin-top:14px;color:#d1d5db">python scripts/setup.py, then paste one prompt into your AI.<br>The graph decides what is safe to believe, in FalkorDB.</div></div>');
await sleep(4000);

const video = page.video();
await ctx.close();
console.log('video:', await video.path());
await browser.close();
