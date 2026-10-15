// Screenshots the navigation and analysis features. Usage: node scripts/feature-shots.mjs [baseUrl] [outDir]
import { chromium } from 'playwright-core';
import { mkdirSync } from 'node:fs';
import { resolve } from 'node:path';

const base = process.argv[2] ?? 'http://127.0.0.1:5173/';
const out = resolve(process.argv[3] ?? '../docs/screens/features');
mkdirSync(out, { recursive: true });

const browser = await chromium.launch({ channel: process.env.QUORUM_BROWSER ?? 'msedge', headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const problems = [];
page.on('console', (m) => m.type() === 'error' && problems.push(`console: ${m.text()}`));
page.on('pageerror', (e) => problems.push(`pageerror: ${e.message}`));
const wait = (ms) => page.waitForTimeout(ms);
const shot = async (n) => {
  await page.screenshot({ path: `${out}/${n}.png` });
  console.log('saved', n);
};
const btn = (name) => page.getByRole('button', { name, exact: false }).first();

await page.goto(base.replace(/\/?$/, '/') + '#/app');
await page.waitForSelector('.cy canvas');
await btn('Inject poisoned webhook').click();
await wait(1700);
await btn('Run agents').click();
await wait(6000);
await btn('Audit memory').click();
await wait(5200);

await page.keyboard.press('Control+k');
await wait(300);
await page.keyboard.type('refund');
await wait(300);
await shot('01-palette');
await page.keyboard.press('Escape');
await wait(200);

await btn('Filters').click();
await wait(200);
await page.getByLabel('Highlight the most connected nodes').check().catch(() => undefined);
await shot('02-filters');
await btn('Filters').click();
await wait(200);

await page.getByRole('button', { name: 'List view' }).click();
await page.locator('.list-row', { hasText: 'Refund window is 90 days' }).first().click();
await page.getByRole('button', { name: 'Graph view' }).click();
await wait(500);
await page.getByRole('tab', { name: 'Links' }).click();
await wait(400);
await shot('03-links-and-history');

await btn('Filters').click();
await page.getByLabel(/Local graph around the selection/).check();
await wait(500);
await page.keyboard.press('Escape');
await shot('04-local-graph');
await btn('Filters').click();
await wait(200);
await page.getByLabel(/Local graph around the selection/).uncheck();
await page.getByRole('button', { name: 'Reset filters' }).click();
await page.mouse.click(1000, 110);
await wait(200);

await page.getByRole('tab', { name: 'Insights' }).click();
await wait(900);
await shot('05-insights-top');
await page.locator('.insights').evaluate((el) => el.scrollTo(0, 560));
await wait(400);
await shot('06-insights-bottom');
await page.getByRole('button', { name: 'Show on graph' }).click();
await wait(700);
await shot('07-path-on-graph');

await page.getByRole('button', { name: 'Clear path' }).click().catch(() => undefined);
await page.locator('.status-chip.path .iconbtn').click().catch(() => undefined);
if (await page.locator('.filters').count()) await btn('Filters').click();
await wait(200);
const pos = await page.locator('.cy').evaluate((el) => {
  const p = el.__cy.getElementById('fact_90d').renderedPosition();
  const r = el.getBoundingClientRect();
  return { x: r.left + p.x, y: r.top + p.y };
});
await page.mouse.click(pos.x, pos.y, { button: 'right' });
await wait(300);
await shot('08-context-menu');

await browser.close();
if (problems.length) {
  console.error('PROBLEMS:\n' + problems.join('\n'));
  process.exit(1);
}
console.log('features: no console errors');
