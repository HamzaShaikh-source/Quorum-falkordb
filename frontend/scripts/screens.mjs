// Drives the demo headlessly and saves one screenshot per beat. Usage:
//   node scripts/screens.mjs [baseUrl] [outDir]
import { chromium } from 'playwright-core';
import { mkdirSync } from 'node:fs';
import { resolve } from 'node:path';

const base = process.argv[2] ?? 'http://127.0.0.1:5173/';
const out = resolve(process.argv[3] ?? '../docs/screens');
mkdirSync(out, { recursive: true });

const browser = await chromium.launch({ channel: process.env.QUORUM_BROWSER ?? 'msedge', headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
const problems = [];
page.on('console', (m) => {
  if (m.type() === 'error') problems.push(`console: ${m.text()}`);
});
page.on('pageerror', (e) => problems.push(`pageerror: ${e.message}`));

const shot = async (name) => {
  await page.screenshot({ path: `${out}/${name}.png` });
  console.log('saved', name);
};
const click = (name) => page.getByRole('button', { name, exact: false }).first().click();
const wait = (ms) => page.waitForTimeout(ms);

await page.goto(base.replace(/\/?$/, '/') + '#/app');
await page.waitForSelector('.cy canvas', { timeout: 15000 });
await wait(800);
await shot('01-seed');

await click('Inject poisoned webhook');
await wait(1800);
await shot('02-poison');

await click('Run agents');
await wait(6000);
await shot('03-agents-acted');

await click('Audit memory');
await wait(1950);
await shot('04-wave');
await wait(2600);
await shot('05-contained');

await page.getByRole('tab', { name: 'Prompt diff' }).click();
await wait(600);
await shot('06-prompt-diff');

await page.getByRole('tab', { name: 'Actions and handoffs' }).click();
await wait(600);
await shot('07-ledger');
await page.getByRole('tab', { name: 'Graph', exact: true }).click();
await wait(400);

const slider = page.locator('.tl-track input');
const min = Number(await slider.getAttribute('min'));
const max = Number(await slider.getAttribute('max'));
await slider.evaluate((el, v) => {
  const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
  set.call(el, String(v));
  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
}, Math.round(min + (max - min) * 0.2));
await wait(900);
await shot('08-past');
await click('Back to now');
await wait(500);

await click('Replace policy');
await wait(3000);
await shot('09-supersede');

await page.getByRole('button', { name: 'Memory', exact: true }).click();
await wait(500);
await click('Show what it remembers');
await wait(600);
await shot('10-memory');

await page.getByRole('button', { name: 'Brain', exact: true }).click();
await wait(300);
await page.getByRole('button', { name: 'Who owns the refund policy?' }).click();
await wait(700);
await shot('11-brain');

await page.getByRole('button', { name: 'Tenants', exact: true }).click();
await wait(600);
await shot('12-tenants');

await page.getByRole('button', { name: 'Benchmark', exact: true }).click();
await wait(600);
await shot('13-benchmark');

await page.getByRole('button', { name: 'Console', exact: true }).click();
await wait(500);
await page.keyboard.press('p');
await wait(600);
await shot('14-presentation');

await browser.close();
if (problems.length) {
  console.error('PROBLEMS:\n' + problems.join('\n'));
  process.exit(1);
}
console.log('no console errors');
