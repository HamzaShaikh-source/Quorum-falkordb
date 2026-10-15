// Verifies every text/background pairing used in the UI meets WCAG AA (4.5:1 text, 3:1 UI strokes).
// Translucent tints (--ok-dim etc.) are composited over the surface they sit on.
import { readFileSync } from 'node:fs';

const css =
  readFileSync(new URL('../src/styles/tokens.css', import.meta.url), 'utf8') +
  readFileSync(new URL('../src/landing/landing.css', import.meta.url), 'utf8');
const hex = Object.fromEntries([...css.matchAll(/--([a-z0-9-]+):\s*(#[0-9a-f]{6})/gi)].map((m) => [m[1], m[2]]));
const rgba = Object.fromEntries(
  [...css.matchAll(/--([a-z0-9-]+):\s*rgb\((\d+) (\d+) (\d+) \/ (\d+)%\)/gi)].map((m) => [m[1], [+m[2], +m[3], +m[4], +m[5] / 100]]),
);

const toRgb = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
const lum = ([r, g, b]) => {
  const c = [r, g, b].map((v) => v / 255).map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
};
const ratio = (a, b) => {
  const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
};
const resolve = (spec, under) => {
  if (spec.startsWith('#')) return toRgb(spec);
  if (hex[spec]) return toRgb(hex[spec]);
  const t = rgba[spec];
  if (t) return [0, 1, 2].map((i) => Math.round(t[i] * t[3] + under[i] * (1 - t[3])));
  throw new Error(`unknown token ${spec}`);
};

// [foreground, background, surface under a translucent background, minimum ratio, where it is used]
const pairs = [
  ['text', 'bg-2', null, 4.5, 'body text on cards'],
  ['text', 'bg', null, 4.5, 'body text on the page'],
  ['text-2', 'bg-2', null, 4.5, 'secondary text on cards'],
  ['text-2', 'bg-3', null, 4.5, 'secondary text on inset surfaces'],
  ['text-3', 'bg-2', null, 4.5, 'captions on cards'],
  ['text-3', 'bg-3', null, 4.5, 'captions on inset surfaces'],
  ['text-3', 'bg', null, 4.5, 'captions on the page'],
  ['ok', 'bg-2', null, 4.5, 'healthy text'],
  ['ok', 'ok-dim', 'bg-2', 4.5, 'healthy chip'],
  ['bad', 'bg-2', null, 4.5, 'quarantined text'],
  ['bad', 'bad-dim', 'bg-2', 4.5, 'quarantined chip'],
  ['#a52716', 'bad-dim', 'bg-2', 4.5, 'removed diff line'],
  ['warn', 'bg-2', null, 4.5, 'out-of-date text'],
  ['warn', 'warn-dim', 'bg-2', 4.5, 'out-of-date chip'],
  ['person', 'bg-2', null, 4.5, 'review text'],
  ['person', 'person-dim', 'bg-2', 4.5, 'review chip'],
  ['on-light', 'text', null, 4.5, 'primary button and active tab'],
  ['on-light', 'person', null, 4.5, 'review button'],
  ['on-light', 'bad', null, 4.5, 'dev error count'],
  ['text', 'bg-4', null, 4.5, 'button hover'],
  ['text', 'mint', null, 4.5, 'completed step, root avatar'],
  ['text', 'redfill', null, 4.5, 'Autopilot'],
  ['text', '#ffe9a8', null, 4.5, 'new-tier avatar, benchmark tile'],
  ['mint', 'text', null, 4.5, 'terminal output'],
  ['text', '#d9f2e9', null, 4.5, 'healthy graph node label'],
  ['#8c2112', '#fbe3de', null, 4.5, 'quarantined graph node label'],
  ['text', '#d9f2e9', null, 3, 'healthy node ring is ink'],
  ['bad', '#fbe3de', null, 3, 'quarantined node ring'],
  ['ghost', 'bg-2', null, 3, 'superseded ring'],
  // landing page (light stock, dark product window)
  ['lp-ink', 'lp-bg', null, 4.5, 'landing body text'],
  ['lp-ink2', 'lp-bg', null, 4.5, 'landing secondary text'],
  ['lp-ink2', 'lp-bg2', null, 4.5, 'landing hover and band text'],
  ['lp-ink2', '#ffffff', null, 4.5, 'landing text on white cards'],
  ['lp-teal', 'lp-bg', null, 4.5, 'landing accent text'],
  ['lp-teal', 'lp-tealwash', null, 4.5, 'landing check mark'],
  ['#ffffff', 'lp-ink', null, 4.5, 'landing dark pill'],
  ['lp-ink', 'lp-redfill', null, 4.5, 'landing red pill'],
  ['#8c2112', 'lp-redwash', null, 4.5, 'landing quarantined node'],
  ['lp-text', 'lp-dark', null, 4.5, 'landing dark band text'],
  ['lp-text2', 'lp-dark', null, 4.5, 'landing dark band secondary text'],
  ['lp-mint', 'lp-dark', null, 4.5, 'landing dark band accent'],
  ['lp-text2', 'lp-dark2', null, 4.5, 'landing window footer'],
  ['#ffb7aa', '#2b1614', null, 4.5, 'landing quarantined label in dark graph'],
  ['lp-ink', 'lp-tealwash', null, 4.5, 'landing playground node'],
];

let bad = 0;
for (const [fg, bg, under, min, use] of pairs) {
  const surface = under ? resolve(under) : null;
  const b = resolve(bg, surface);
  const a = resolve(fg, b);
  const r = ratio(a, b);
  const ok = r >= min;
  if (!ok) bad++;
  console.log(`${ok ? 'ok  ' : 'FAIL'} ${r.toFixed(2).padStart(5)}  (min ${min})  ${fg} on ${bg}  ${use}`);
}
console.log('Colour never carries state alone: every state also has a shape, border style, glyph or label.');
process.exit(bad ? 1 : 0);
