// Screenshots the landing page section by section. Usage: node scripts/landing-shots.mjs [baseUrl] [outDir] [width]
import { chromium } from 'playwright-core';
import { mkdirSync } from 'node:fs';
import { resolve } from 'node:path';

const base = process.argv[2] ?? 'http://127.0.0.1:5173/';
const out = resolve(process.argv[3] ?? '../docs/screens/landing');
const width = Number(process.argv[4] ?? 1440);
mkdirSync(out, { recursive: true });

const browser = await chromium.launch({ channel: process.env.QUORUM_BROWSER ?? 'msedge', headless: true });
const page = await browser.newPage({ viewport: { width, height: 900 } });
const problems = [];
page.on('console', (m) => m.type() === 'error' && problems.push(`console: ${m.text()}`));
page.on('pageerror', (e) => problems.push(`pageerror: ${e.message}`));

await page.goto(base);
await page.waitForTimeout(500);
await page.screenshot({ path: `${out}/00-loader-${width}.png` });
await page.waitForSelector('.lp-hero h1', { timeout: 15000 });
await page.waitForTimeout(2400);
await page.screenshot({ path: `${out}/01-hero-${width}.png` });

await page.evaluate(() => document.querySelector('.lp-window')?.scrollIntoView({ block: 'center' }));
await page.waitForTimeout(2200);
await page.screenshot({ path: `${out}/02-hero-poison-${width}.png` });
await page.waitForTimeout(3600);
await page.screenshot({ path: `${out}/03-hero-contained-${width}.png` });

const sections = ['problem', 'playground', 'defense', 'console', 'proof', 'tracks', 'start'];
for (const id of sections) {
  await page.evaluate((i) => document.getElementById(i)?.scrollIntoView({ block: 'start' }), id);
  await page.waitForTimeout(1300);
  await page.screenshot({ path: `${out}/${id}-${width}.png` });
  console.log('saved', id);
}

await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
await page.waitForTimeout(800);
await page.screenshot({ path: `${out}/footer-${width}.png` });

const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
if (overflow) problems.push(`horizontal overflow at ${width}px`);
await browser.close();
if (problems.length) {
  console.error('PROBLEMS:\n' + problems.join('\n'));
  process.exit(1);
}
console.log('landing: no console errors, no horizontal overflow');
