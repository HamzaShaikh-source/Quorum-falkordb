/* global axe */
// Accessibility QA with axe-core on every screen of the sample build, at desktop and phone width.
// Fails on any serious or critical violation (and lists moderate ones). Usage: node scripts/a11y.mjs [baseUrl]
import { chromium } from 'playwright-core';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';

const base = process.argv[2] ?? 'http://127.0.0.1:5175/';
const axeSource = readFileSync(createRequire(import.meta.url).resolve('axe-core/axe.min.js'), 'utf8');
const browser = await chromium.launch({ channel: process.env.QUORUM_BROWSER ?? 'msedge', headless: true });

const SCREENS = [
  { name: 'landing', url: '', key: null },
  { name: 'overview', url: '#/app', key: '0' },
  { name: 'console', url: '#/app', key: '1' },
  { name: 'memory', url: '#/app', key: '2' },
  { name: 'brain', url: '#/app', key: '3' },
  { name: 'tenants', url: '#/app', key: '4' },
  { name: 'benchmark', url: '#/app', key: '5' },
  { name: 'context', url: '#/app', key: '6' },
  { name: 'connect', url: '#/app', key: '7' },
  { name: 'team', url: '#/app', key: '8' },
  { name: 'activity', url: '#/app', key: '9' },
];

let failures = 0;
let moderate = 0;
for (const [w, h, label] of [[1440, 900, 'desktop'], [390, 844, 'phone']]) {
  const page = await browser.newPage({ viewport: { width: w, height: h } });
  // reveal animations fade text in; a reader who asked for reduced motion (and axe) sees the final state
  await page.emulateMedia({ reducedMotion: 'reduce' });
  for (const s of SCREENS) {
    for (let attempt = 1; attempt <= 15; attempt++) {
      try {
        await page.goto(base + s.url);
        break;
      } catch (err) {
        if (attempt === 15) throw err;
        await new Promise((r) => setTimeout(r, 1000));
      }
    }
    await page.waitForTimeout(s.name === 'landing' ? 2500 : 3000);
    if (s.key) {
      await page.evaluate(() => document.activeElement instanceof HTMLElement && document.activeElement.blur());
      await page.keyboard.press(s.key);
      await page.waitForTimeout(1800);
    }
    await page.evaluate(axeSource);
    const res = await page.evaluate(() => axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice'] } }));
    const bad = res.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical');
    const mod = res.violations.filter((v) => !(v.impact === 'serious' || v.impact === 'critical'));
    failures += bad.length;
    moderate += mod.length;
    console.log(`${bad.length ? 'FAIL' : 'PASS'}  ${label.padEnd(7)} ${s.name.padEnd(10)} ${bad.length} serious/critical, ${mod.length} minor  (${res.passes.length} rules passed)`);
    for (const v of [...bad, ...mod]) console.log(`      ${v.impact.padEnd(8)} ${v.id}: ${v.help}  [${v.nodes.length} element${v.nodes.length === 1 ? '' : 's'}] e.g. ${v.nodes[0].target.join(' ')}`);
  }
  await page.close();
}
await browser.close();
console.log(failures ? `${failures} serious/critical violation(s)` : `no serious or critical violations (${moderate} minor)`);
process.exit(failures ? 1 : 0);
