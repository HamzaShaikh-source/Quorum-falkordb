/** Landing-page cursor: snaps to the whole target, follows content that moves under a still pointer (keyboard scroll). node scripts/cursor.mjs [BASE] */
import { chromium } from 'playwright-core';
const b = await chromium.launch({ channel: process.env.QUORUM_BROWSER ?? 'msedge' });
const p = await b.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 });
await p.goto(process.argv[2] ?? 'http://127.0.0.1:5175/');
await p.evaluate(() => sessionStorage.setItem('quorum:loaded', '1'));
await p.reload();
await p.waitForTimeout(1800);
const mode = () => p.evaluate(() => document.querySelector('.cr-ring')?.dataset.mode ?? '');
const results = [];
const check = (n, ok, x = '') => { results.push(ok); console.log(`${ok ? 'PASS' : 'FAIL'}  ${n}  ${x}`); };

// 1. hovering a button, then scrolling with the keyboard, without moving the mouse
const bb = await p.locator('.lp-hero .lp-pill.dark').boundingBox();
await p.mouse.move(bb.x + bb.width / 2, bb.y + bb.height / 2, { steps: 8 });
await p.waitForTimeout(500);
check('over a button the ring is in snap mode', (await mode()) === 'snap', await mode());
for (let i = 0; i < 6; i++) { await p.keyboard.press('ArrowDown'); await p.waitForTimeout(60); }
await p.keyboard.press('PageDown');
await p.waitForTimeout(900);
const under = await p.evaluate(([x, y]) => document.elementFromPoint(x, y)?.closest('a,button,[role=button],.lp-pill,[data-cursor]') !== null, [bb.x + bb.width / 2, bb.y + bb.height / 2]);
const m1 = await mode();
check('after the page scrolled away, the ring left snap mode with no mouse move', !under ? m1 !== 'snap' : true, `under=${under} mode=${m1}`);

// 2. the other way: an empty spot, then scroll until a button arrives under the pointer
await p.evaluate(() => window.scrollTo(0, 0));
await p.waitForTimeout(500);
await p.mouse.move(bb.x + bb.width / 2, bb.y - 140, { steps: 6 }); // above the button, on empty page
await p.waitForTimeout(400);
const before = await mode();
// scroll the page up by 140px so the button slides under the pointer, via keyboard-like scrolling
await p.evaluate(() => window.scrollBy({ top: 140, behavior: 'instant' }));
await p.waitForTimeout(500);
const m2 = await mode();
check('a button scrolling under a still pointer turns the ring into snap mode', before !== 'snap' && m2 === 'snap', `${before} -> ${m2}`);

// 3. the playground tile: the ring and the wash should cover the whole tile
await p.evaluate(() => window.dispatchEvent(new Event('lp:below')));
await p.waitForTimeout(1800);
await p.evaluate(() => document.getElementById('playground')?.scrollIntoView());
await p.waitForTimeout(1200);
const tile = await p.locator('.lp-play .lg-node').first().boundingBox();
await p.mouse.move(tile.x + tile.width / 2, tile.y + tile.height / 2, { steps: 10 });
await p.waitForTimeout(900);
const ring = await p.evaluate(() => { const r = document.querySelector('.cr-ring').getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height }; });
const tile2 = await p.locator('.lp-play .lg-node').first().boundingBox();
const covers = ring.x <= tile2.x + 1 && ring.y <= tile2.y + 1 && ring.x + ring.w >= tile2.x + tile2.width - 1 && ring.y + ring.h >= tile2.y + tile2.height - 1;
check('the ring covers the whole tile', covers, `tile ${Math.round(tile2.x)},${Math.round(tile2.y)} ${Math.round(tile2.width)}x${Math.round(tile2.height)} ring ${Math.round(ring.x)},${Math.round(ring.y)} ${Math.round(ring.w)}x${Math.round(ring.h)}`);
await p.screenshot({ path: '../docs/screens/landing/cur-tile.png', clip: { x: Math.max(0, tile2.x - 80), y: Math.max(0, tile2.y - 70), width: tile2.width + 240, height: tile2.height + 150 } });
// Tab means keyboard use: the custom cursor steps aside until the mouse moves again
await p.keyboard.press('Tab');
await p.waitForTimeout(200);
check('pressing Tab hides the custom cursor and brings back the normal one', await p.evaluate(() => getComputedStyle(document.querySelector('.cr')).display === 'none' && getComputedStyle(document.querySelector('.lp')).cursor === 'auto'));
await p.mouse.move(400, 400, { steps: 4 });
await p.waitForTimeout(300);
check('moving the mouse brings the custom cursor back', await p.evaluate(() => getComputedStyle(document.querySelector('.cr')).display !== 'none'));
// the footer switch turns it off for good (this browser only)
await p.evaluate(() => document.querySelector('.lp-footer').scrollIntoView());
await p.waitForTimeout(400);
await p.getByRole('button', { name: 'Use the normal cursor' }).click();
await p.mouse.move(500, 300, { steps: 4 });
await p.waitForTimeout(300);
check('the footer switch turns the custom cursor off and remembers it', await p.evaluate(() => getComputedStyle(document.querySelector('.cr')).display === 'none' && localStorage.getItem('quorum:cursor') === 'off'));
await p.getByRole('button', { name: 'Use the custom cursor' }).click();
check('and back on', await p.evaluate(() => localStorage.getItem('quorum:cursor') === null && getComputedStyle(document.querySelector('.cr')).display !== 'none'));
await b.close();
console.log(`${results.filter(Boolean).length}/${results.length} cursor checks passed`);
process.exit(results.every(Boolean) ? 0 : 1);
