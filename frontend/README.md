# Quorum console (frontend)

React 18, Vite, TypeScript (strict), Zustand, TanStack Query, zod, Cytoscape.js. No Tailwind, no animation library: tokens are CSS variables in `src/styles/tokens.css`. Design notes: `../docs/FRONTEND_DESIGN.md`.

## Run it

```bash
npm install
npm run dev          # http://127.0.0.1:5173, mock mode
npm run build && npm run preview   # production build on http://127.0.0.1:5173
```

## Pages

`#/` is the landing page (loader, live hero, problem, playground, defense, console gallery, proof, tracks). `#/app` is the console. Design notes for both: `../docs/LANDING_DESIGN.md` and `../docs/FRONTEND_DESIGN.md`.

## Two data modes

| Mode | How | What you see |
|---|---|---|
| mock (default) | `VITE_API_MODE=mock` | A scripted backend inside the browser. Every response and event goes through the same zod schemas as live data. A "Sample data" chip in the header says so. |
| live | `VITE_API_MODE=live` | Calls `/api/...` through the dev or preview proxy. Set `QUORUM_BACKEND=http://localhost:8000` if the backend is elsewhere. Tokens come from `public/config.json`, which you create at deploy time from `public/config.example.json` and which is git-ignored. |

`GET /meta` reports the backend's agent mode (Replay or Live). The header shows it as sent.

## Scripts

| Command | What it does |
|---|---|
| `npm run typecheck` | `tsc --noEmit` |
| `npm run lint` | ESLint over `src/` and the scripts |
| `npm test` | Unit tests: event reducer and sequencing, action display state, as-of diff, layout, zod boundary, and every mock response and SSE event against `contracts/events.schema.json` |
| `npm run contrast` | WCAG check for every text and background pair in the palette |
| `npm run contract:check` | Fixture validation; with `QUORUM_BACKEND` set, diffs the live `/openapi.json` against `contracts/openapi.snapshot.json` |
| `npm run screens` | Headless run of the whole demo, one screenshot per beat into `docs/screens/` |
| `node scripts/feature-shots.mjs` | Screenshots the palette, filters, links, local graph, insights, path and context menu into `docs/screens/features/` |
| `npm run landing` | Screenshots every landing section into `docs/screens/landing/` |
| `npm run qa` | Headless interaction checks (shortcuts, roles, tenants, time travel, autopilot, narrow widths) |
| `npm run verify` | typecheck, tests, contrast, contract check, production build |

`screens` and `qa` need the dev server running and a Chromium browser. They use the installed Microsoft Edge by default; set `QUORUM_BROWSER=chrome` to use Chrome.

## Layout

```
src/api/      zod schemas, request client, SSE client, dev log
src/state/    store, event reducer + sequencing, pipeline, autopilot, time travel
src/graph/    Cytoscape adapter (outside React state), layout, canvas, list view
src/components/  header, scenario, history, containment, inspector, timeline, prompt diff, ledger
src/views/    Console, Memory, Brain, Tenants, Benchmark
src/copy/     every user-facing string
src/mocks/    scripted backend and fixtures (synthetic)
```

## Keyboard

Ctrl K command palette, 1 to 5 switch views, F fit, / search, L list view, P presentation mode, Alt Left and Alt Right selection history, Esc dismiss, ? legend. Right-click a node for its menu.

## Honest limits

- Mock mode proves the interface, not the system. Numbers in the Benchmark view are a mock fixture until `make proof` produces a real `bench/results.json`.
- The as-of view in mock mode replays recorded snapshots; the real backend only knows the latest quarantine window per node.
- `?demo=1` hides the dev console; it never hides the Sample data chip.
