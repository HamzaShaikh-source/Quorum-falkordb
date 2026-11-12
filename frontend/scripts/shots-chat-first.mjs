// Screenshots of the chat-first additions (Team: connect with teammates, Connect: AI prompt). usage: node scripts/shots-chat-first.mjs <console-url> <admin-token> <out-dir>
import { chromium } from 'playwright-core';
const [base, token, outDir] = process.argv.slice(2);
const b = await chromium.launch({ channel: 'msedge' });
const page = await b.newPage({ viewport: { width: 1440, height: 1000 } });
await page.addInitScript(() => { try { localStorage.setItem('quorum_tour_seen', 'true'); } catch { /* private mode */ } });
await page.goto(`${base}/#/app`);
await page.waitForTimeout(1500);
await page.locator('input[type="password"], input[type="text"]').first().fill(token);
await page.getByRole('button', { name: /sign in|continue|open/i }).first().click();
await page.waitForTimeout(2500);
for (const [name, label] of [['team', 'Team'], ['connect', 'Connect']]) {
  await page.getByRole('button', { name: new RegExp(`^${label}$`, 'i') }).first().click().catch(async () => { await page.locator(`.navbtn[aria-label*="${label}"]`).first().click(); });
  await page.waitForTimeout(2500);
  await page.screenshot({ path: `${outDir}/${name}.png`, fullPage: false });
}
await b.close();
