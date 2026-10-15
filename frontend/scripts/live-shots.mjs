// Screenshots every console screen against a live hub, at laptop, desktop and phone widths. Usage: node scripts/live-shots.mjs <baseUrl> <outDir> <token>
import { chromium } from 'playwright-core';
import { mkdirSync } from 'node:fs';
const [base = 'http://localhost:5200/', out = './shots', token = 'token_acme_human'] = process.argv.slice(2);
mkdirSync(out, { recursive: true });
const browser = await chromium.launch({ channel: process.env.QUORUM_BROWSER ?? 'msedge', headless: true });
const SIZES = [[1366, 768, 'laptop'], [1440, 900, 'desktop'], [390, 844, 'phone']];
for (const [w, h, label] of SIZES) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h }, reducedMotion: 'reduce' });
  const page = await ctx.newPage();
  await page.addInitScript((t) => { try { sessionStorage.setItem("quorum:token", t); } catch { /* private mode: sign in by hand */ } }, token);
  await page.goto(`${base}#/app`);
  await page.waitForSelector('.page, .console', { timeout: 30000 }).catch(() => console.log('content never rendered at', label));
  await page.waitForTimeout(1500);
  for (const [name, key] of [['overview', '1'], ['console', '2'], ['context', '3'], ['connect', '4'], ['team', '5'], ['activity', '6']]) {
    await page.keyboard.press(key);
    await page.waitForSelector('.page, .console', { timeout: 15000 }).catch(() => {});
    await page.waitForTimeout(1200);
    await page.screenshot({ path: `${out}/${label}-${name}.png`, fullPage: name !== 'console' });
  }
  await ctx.close();
}
await browser.close();
console.log('done');
