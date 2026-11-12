# Console design

Direction: a print proof room, shared with the landing page. Cool off-white stock, ink black outlines, hard offset shadows on the pieces that matter (the corner crop marks stay on the landing page only, so nothing frames or collides with console controls), Bricolage Grotesque for headings, Silkscreen pixel numerals for anything countable, JetBrains Mono for labels and ids. State colours are signals only: mint for healthy, red for quarantined, amber for out of date, periwinkle for "a person decides".

History of the direction: the first console was a light ledger, then a dark "incident command room". The owner asked for the console to look like the landing page, so the console now uses the same system. The role names in `src/styles/tokens.css` did not change, only their values, which is why one stylesheet pass (`proof-room.css`) restyled every view.

## Tokens (`src/styles/tokens.css`)

| Role | Token | Value |
|---|---|---|
| Stock / card / inset | `--bg` / `--bg-2` / `--bg-3` / `--bg-4` | #f3f5f2 / #ffffff / #eef1ed / #e3e8e2 |
| Ink | `--text` / `--text-2` / `--text-3` | #0b1218 / #46525e / #5d6975 |
| Healthy | `--ok`, `--mint` | #07745e text, #3ddbb0 fill |
| Quarantined | `--bad`, `--redfill` | #c93420 text, #ff6a55 fill |
| Out of date | `--warn` | #8f5a00 |
| Person decides | `--person` | #3b3fd1 |
| Replaced | `--ghost` | #6b7783 |

`npm run contrast` checks every console and landing pair (text at 4.5:1, rings at 3:1). Colour never carries state alone: shape, border style, glyph or label always accompany it.

## Structure

- Header: brand (back to the landing page), view switcher with number keys, tenant, data-source chip, agent mode, epoch, invariants, connection, search button for the command palette, presentation mode, help.
- Console: left column holds the story stepper and history; the centre card holds the graph, prompt diff, actions and handoffs, and insights; the right column holds the containment report and inspector; the timeline runs underneath.
- Secondary pages use one template: numbered tag, giant title, one sentence, cards.

## The canvas

Node shapes keep their meaning: source (cut corners), fact (pill), conclusion (rounded box), decision (double border), action (square corners). Healthy nodes are mint with an ink outline; quarantined are red wash with a dashed red outline; out of date has a dotted amber outline; replaced are faded. Columns are fixed by kind, with a counted header each. Edges are orthogonal and turn dashed red once their target is quarantined.

## Navigation and analysis

Borrowed as ideas from Obsidian (graph view, local graph, backlinks, quick switcher, back and forward through notes) and Graphify (god nodes, communities, path and explain queries). All computed client-side from the live nodes and edges, with unit tests in `src/state/insights.test.ts`.

| Feature | Where | Notes |
|---|---|---|
| Command palette | Ctrl K, or the header search button | Fuzzy search over beliefs, views, story steps and canvas commands |
| Graph filters | Canvas toolbar | Kinds, statuses, "only what needs attention", highlight most connected nodes |
| Local graph | Filters, or the node context menu | The selection and everything within 1 to 4 hops, refit on change |
| Selection history | Back and forward buttons, Alt Left, Alt Right | Like back and forward in a notes app |
| Breadcrumb | Under the tabs | Console / Graph / kind / selected node |
| Backlinks | Inspector, Links tab | Built from, feeds, connection count, plain-language explanation |
| Context menu | Right-click a node | Center, inspect, trace to sources, local graph, preview blast radius, collapse, copy id |
| Minimap | Bottom left of the canvas | Click to move the view; selected node in amber |
| Path highlight | Insights, or the context menu | The shortest chain between two nodes, drawn on the graph |
| Insights tab | Centre card | Belief flow ribbons, health bar, activity over time, most connected nodes, communities, shortest path, explain the selection |
| Activity strip | Timeline | Event density behind the slider |
| Keyboard | `?` for the full list | 1 to 5 switch views; F fit; / search; L list; P presentation; Ctrl K palette |

## Layers, overlays and dismissal

Everything that floats follows one set of rules (`src/styles/layers.css`, `src/components/focus.ts`, `src/state/toasts.ts`).

| Rule | How |
|---|---|
| One z-index scale | Tokens `--z-canvas` 1, `--z-overlay` 4, `--z-chrome` 20, `--z-popover` 40, `--z-dev` 50, `--z-menu` 60, `--z-modal` 80, `--z-toast` 90; no raw numbers in components |
| Canvas overlays cannot collide | They sit in a grid (toolbar row, one status line, bottom-left dock, bottom-right dock) and the graph area is inset to leave them clear; rail labels sit below the status line |
| One status line at a time | Past view, path, blast-radius preview and the welcome hint share one slot, chosen by priority |
| Popovers | One open at a time; close on outside click and Escape; focus returns to the button that opened them |
| Layers close in order | Escape closes the palette, then the context menu, then popovers, then the shortcuts dialog, then presentation mode, then the selection |
| Dialogs | The palette and shortcuts dialog trap Tab and give focus back to where it was; the context menu focuses its first item and supports arrow keys |
| Toasts | At most three, identical messages are counted not stacked, lifetime scales with length (problems stay longer), the timer pauses while the pointer or focus is on a toast, and they sit above the measured height of the timeline |
| Transient previews | The blast-radius preview lingers 450 ms after the pointer leaves, so it does not flicker |
| Docked, not floating | The dev console is a compact drawer (128 px, drag its top edge to resize, remembered) below the stage; toasts move up to make room, and the graph refits once, instantly, so everything stays visible |
| No constant redraws | The timeline no longer ticks every second, dev log rows are memoised, and layout changes trigger one instant refit rather than repeated animated ones |
| Minimap | Off by default and remembered; when on, the graph area shrinks to leave room instead of drawing underneath it |

## Motion

Two orchestrated moments: the quarantine wave (each node pulses at 120 ms per hop) and the claim race. Everything else is a short response to a click. `prefers-reduced-motion` turns the wave into an instant change with a brief highlight.

## Voice

Plain and specific. Statuses: Healthy, Quarantined, Replaced by a newer fact, Out of date. Actions: Staged, Held for review, Ran, Rolled back, Needs a person. Buttons name the result. Errors say what failed and what to do. All strings live in `src/copy/`.

## Honesty

A "Sample data" chip sits in the header whenever the scripted in-browser backend is active; it explains itself on hover. Benchmark numbers and every other figure come from the API (or, in sample mode, from a labelled fixture).

## Security and quality

- Production builds carry a strict Content-Security-Policy (`script-src 'self'`, no inline scripts, `connect-src 'self'`, `object-src 'none'`); fonts are never inlined so `font-src 'self'` holds. The QA run fails on any CSP violation.
- No tokens in the repo: the sample backend uses built-in throwaway tokens; live mode uses token sign-in; public config files must never carry bearer secrets.
- Links that come from the API are only followed if they are http or https (`src/api/safeUrl.ts`).
- Every response and event is validated at the boundary (zod); unknown enum values render neutrally; one failing component is caught by an error boundary instead of blanking the page.
- ESLint (TypeScript, React hooks and compiler rules, no `any`, no stray `console.log`) runs as part of `npm run verify`.
- `npm audit --omit=dev` reports no known vulnerabilities.

## Verification

`npm run verify` runs typecheck, unit tests, contrast, contract check and the production build. `npm run screens`, `npm run qa`, `npm run landing` and `node scripts/feature-shots.mjs` drive the app headlessly; screenshots land in `docs/screens/` and are reviewed before a beat counts as done.

## Workspace views (Context, Connect, Team) and the mind map

Direction: the same print proof room, read as a switchboard. Links are cables, meters are pixel numerals, every MCP tool is a labelled jack.

- **Context** (`views/Context.tsx`, `context/packer.ts`, `components/Measured.tsx`): the two views of one memory. The mind map (`components/MindMap.tsx`) is for people; the compact map and packed answer are what the agent receives. On a live backend the numbers come from `GET /api/context/preview`, computed by the same Python the MCP tools run; sample mode computes them in the browser with a TypeScript twin whose token estimator is held identical by a shared fixture test. The **Measured** card reads `bench/token_bench_*.json`: real Claude Code sessions, not estimates.
- **Connect** (`views/Connect.tsx`, `workspace/mcp.ts`): MCP status (a real handshake in live mode), a three-step setup with snippets for eight clients (header auth, VS Code prompts for the token, Claude Desktop through the stdio bridge), the 15 tools read from the hub, and a feed and per-member sessions built from the backend's call log (`GET /api/mcp/activity`, plus `mcp_call` events). Sample mode simulates these and says so.
- **Team** (`views/Team.tsx`, `workspace/useWs.ts`): plan and seats, teams, members with their own token and AI client, and links that share team-private facts. In live mode every action is a server call and the server enforces the rules; sample mode keeps a browser-stored copy.
- **Mind map tab** in the Console: the same tree, fed by the live graph.
- **Setup screen** (`components/BlockScreen.tsx`): shown instead of the product when `/api/health` says FalkorDB is not ready or the backend is not running.

Honesty rules: simulated activity is labelled "Simulated activity" and only exists in the sample build; synthetic memories are labelled synthetic; token numbers are estimates and say so; measured numbers say what was measured and what they exclude; nothing claims a saving it did not compute.
