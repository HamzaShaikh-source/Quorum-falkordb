# Landing page design

Direction: a print proof room. Cool off-white stock, ink black, crop marks at the corners, giant grotesque headlines, pixel numerals for anything countable, and one dark window that shows the product. The console behind it stays the dark incident command room, so the page reads as a printed brief that opens onto a live instrument.

## What was studied, and what was taken

Four public sites were opened in a real browser and read for systems, not assets. Nothing was copied: no logos, illustrations, photos, fonts that are not open, or wording. Every shape, line of copy and animation here is original.

| Source | Pattern taken (as a system) | Where it appears |
|---|---|---|
| Portfoliofy "creative artsy" playground | Oversized flat geometric primitives, a pixel display face for small captions, a hands-on gallery that rewards poking | Playground section: a graph you click to break, chunky offset card, pixel counters, floating circle and triangle behind the final call to action |
| Bindplane | Product-first hero: the real interface peeks out under the headline; animated flow lines on a pipeline diagram; pill call-to-action pair; centred eyebrow, title and one-line sub for every section; a logo strip | Hero window with a live, looping quarantine; dashed flow on every edge; dark and ghost pill pair; "built on" marquee as text marks |
| Dgrees | A boxy loader before the page; giant regular-weight headlines; numbered section labels in mono | Loader, 40 to 92 px section titles, `[01]` to `[06]` labels |
| Sparcley | Percent counter loader with a hairline progress rule; crop marks framing the viewport; a list where only the active line is dark and the rest sit at 20%; slide counter with square arrow buttons and a progress hairline | Loader, fixed crop-mark frame, the scroll-spy list in "The problem", the numbered counters and arrows in "Defense" and "Inside the console" |

The `replica-design` rules were followed: roles instead of copied colours, open fonts only (Bricolage Grotesque, Schibsted Grotesk, JetBrains Mono, Silkscreen), fresh copy, and a recorded contrast check.

## Tokens (CSS variables, `src/landing/landing.css`)

| Role | Token | Value |
|---|---|---|
| Stock | `--lp-bg` / `--lp-bg2` | #f3f5f2 / #e8ece7 |
| Ink | `--lp-ink` / `--lp-ink2` | #0b1218 / #46525e |
| Accent text | `--lp-teal` | #07745e |
| Accent fill | `--lp-mint` / `--lp-redfill` | #3ddbb0 / #ff6a55 |
| Washes | `--lp-tealwash` / `--lp-redwash` | #d9f2e9 / #fbe3de |
| Dark band | `--lp-dark` / `--lp-dark2` | #080d13 / #0d141c |

Type: Bricolage Grotesque variable for display (700 for the hero, 600 for section titles, 800 italic for counters), Schibsted Grotesk for body, JetBrains Mono for labels and numbers in code, Silkscreen for pixel captions and counters. Spacing is a 4 px base with section gaps of 80 to 160 px. Radius: 999 px pills, 14 px square buttons, 22 to 26 px cards.

`npm run contrast` checks every landing text and surface pair at 4.5:1; it currently reports zero failures.

## Components

```
LandingGraph
  modes     autoplay (hero), spread (problem), interactive (playground)
  themes    dark (matches the console), light (playground)
  states    healthy, quarantined, dimmed, suspect, dry-run preview, pulsing
  rules     a root fact is never quarantined; clicking it quarantines its challenger instead
  a11y      group with a label; in the playground every node is a keyboard button
  motion    flowing dashes on edges, 150 ms per hop, pulse ring; all off under reduced motion

Loader      percent counter, hairline rule, crop marks; once per session; skipped under reduced motion
Pill        dark, ghost, red; sizes md and xl; real <button>, visible focus, hover lift
Spy list    IntersectionObserver picks the active row; others at 20% opacity
Carousel    counter, square arrows, progress hairline, pauses on hover and focus
Gallery     six real screenshots of the console, with a thumbnail index
Stat        count-up on first view; real numbers recorded by the graph owner
```

## Behaviour

- Routing is a hash: `#/` is the landing page, `#/app` is the console. The console brand returns to the landing page.
- "Watch the 90-second story" opens the console and starts Autopilot once the graph has loaded.
- The hero loop, marquee, carousels and loader all stop under `prefers-reduced-motion`, leaving the final state visible.
- Below 700 px the graphs scroll horizontally inside their frame instead of shrinking to unreadable text.

## Verification

`npm run landing` captures every section at 1440, 1024 and 390 px into `docs/screens/landing/` and fails on console errors or horizontal overflow. `npm run qa` also drives the landing page: the playground cascade and restore, the root-fact rule, both carousels, routing into the console and back, and the 90-second story button.

## Motion layer and lazy loading

- **Liquid cursor** (`fx/LiquidCursor.tsx`): seven spring-chained ink blobs fused by an SVG goo filter; swells over buttons, shrinks on press. Fine pointers only.
- **Belief-network canvas** (`fx/NetworkCanvas.tsx`): hero backdrop; the cursor parts the facts, a click quarantines nearby ones (red) and sends a ring out, then they heal. Pauses off screen and in hidden tabs.
- **Parallax** (`fx/motion.ts`, `Parallax.tsx`): one scroll loop moves only on-screen layers, publishes `--sp` (progress) and `--vel` (velocity, which pushes the marquee). Scroll progress bar and section rail in `fx/Chrome.tsx`.
- **Word reveal** (`SplitText.tsx`), **card tilt** (`Tilt.tsx`), **magnetic pills** (`useMagnetic`), and an animated latency chart (`LatencyChart.tsx`, recorded p50/p95/p99 only).
- All of it is off under `prefers-reduced-motion`; cursor and magnetic effects are also off for coarse pointers.
- **Lazy loading**: landing and console are separate route chunks; sections below the hero load as `Below` when the reader nears them (or on idle, or on a nav click); the console is prefetched on idle and on hover/focus of "Open console"; console tabs and the palette are lazy too. The landing chunk contains no cytoscape, zod, react-query or zustand.
