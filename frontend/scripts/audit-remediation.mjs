// Focused audit QA. Does not overwrite historical screenshot evidence.
import assert from 'node:assert/strict';
import { chromium } from 'playwright-core';

const browser = await chromium.launch({ channel: process.env.QUORUM_BROWSER ?? 'msedge' });
const problems = [];
try {
  for (const width of [1440, 1024, 1023, 768, 390]) {
    const page = await browser.newPage({ viewport: { width, height: 900 } });
    page.on('pageerror', (error) => problems.push(error.message));
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.goto((process.argv[2] ?? 'http://127.0.0.1:15173') + '/#/app');
    await page.locator('.navbtn').first().waitFor();
    const tabs = page.locator('.navbtn');
    assert.ok(await tabs.count() >= 9);
    for (const tab of await tabs.all()) {
      assert.ok(await tab.getAttribute('aria-label'));
      assert.ok(await tab.getAttribute('title'));
      await tab.focus();
      assert.ok(await tab.locator('.nav-tip').isVisible());
      const box = await tab.boundingBox();
      assert.ok(box.x >= -1 && box.x + box.width <= width + 1, `${width}: focused tab is reachable`);
    }
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${width}: no page overflow`);
    console.log(`PASS navigation names, titles, focus tooltips, reachability and overflow at ${width}px`);
    await page.close();
  }
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await page.goto((process.argv[2] ?? 'http://127.0.0.1:15173') + '/');
  await page.evaluate(() => sessionStorage.setItem('quorum:loaded', '1'));
  await page.reload();
  await page.waitForTimeout(1800);
  const pill = page.locator('.lp-hero .lp-pill.dark');
  await pill.locator('svg').hover();
  await page.waitForTimeout(600);
  const radii = await page.evaluate(() => {
    const ring = document.querySelector('.cr-ring');
    return { mode: ring.dataset.mode, radius: parseFloat(getComputedStyle(ring).borderRadius), height: ring.getBoundingClientRect().height };
  });
  assert.equal(radii.mode, 'snap');
  assert.ok(radii.radius >= radii.height / 2 - 2);
  await page.keyboard.press('Tab');
  assert.ok(await page.evaluate(() => document.documentElement.classList.contains('cr-keyboard')));
  await page.evaluate(() => window.dispatchEvent(new Event('lp:below')));
  await page.locator('.lp-thumbs button').first().waitFor();
  assert.equal(await page.locator('.lp-thumbs button').first().evaluate((el) => getComputedStyle(el).overflow), 'hidden');
  console.log('PASS nested-icon pill cursor radius, keyboard fallback and clipped numbered accent');
  for (const mode of ['touch', 'reduced-motion']) {
    const context = await browser.newContext(mode === 'touch'
      ? { viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true }
      : { viewport: { width: 1440, height: 900 }, reducedMotion: 'reduce' });
    const native = await context.newPage();
    await native.goto((process.argv[2] ?? 'http://127.0.0.1:15173') + '/');
    await native.evaluate(() => sessionStorage.setItem('quorum:loaded', '1'));
    await native.reload();
    const target = native.locator('.lp-hero .lp-pill.dark');
    await target.waitFor();
    assert.ok(await native.evaluate(() => !document.documentElement.classList.contains('cr-on')));
    if (mode === 'touch') await target.tap();
    else { await target.focus(); await native.keyboard.press('Enter'); }
    await native.waitForURL('**/#/app*');
    console.log(`PASS ${mode}: native cursor and working ${mode === 'touch' ? 'tap' : 'keyboard'} activation`);
    await context.close();
  }
  assert.deepEqual(problems, []);
} finally {
  await browser.close();
}
