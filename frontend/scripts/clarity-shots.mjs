// Screenshots the console screens (and the Guide drawer) at desktop and phone width for a visual review.
// Usage: node scripts/clarity-shots.mjs <baseUrl> <outDir>
import { chromium } from 'playwright-core';
import { mkdirSync } from 'node:fs';

const base = process.argv[2] ?? 'http://localhost:5199/';
const out = process.argv[3] ?? './shots';
mkdirSync(out, { recursive: true });
const browser = await chromium.launch({ channel: process.env.QUORUM_BROWSER ?? 'msedge', headless: true });
const KEYS = { overview: '0', console: '1', context: '6', connect: '7', team: '8', activity: '9' };
for (const [w, h, label] of [[1440, 900, 'desktop'], [390, 844, 'phone']]) {
  const page = await browser.newPage({ viewport: { width: w, height: h }, reducedMotion: 'reduce' });
  await page.goto(`${base}#/app`);
  await page.waitForTimeout(2500);
  for (const [name, key] of Object.entries(KEYS)) {
    await page.keyboard.press(key);
    await page.waitForTimeout(900);
    await page.screenshot({ path: `${out}/${label}-${name}.png` });
  }
  await page.keyboard.press('g');
  await page.waitForTimeout(600);
  await page.screenshot({ path: `${out}/${label}-guide.png` });
  await page.close();
}
await browser.close();
console.log('done');
