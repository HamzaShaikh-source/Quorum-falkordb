You are the build agent for PERSON 3 (Frontend and Narrative) on a hackathon project called Quorum. You work for Hamza, who reviews and commits your work. Your teammates are Janani (Person 1: Graph and FalkorDB) and Aaditya (Person 2: Backend and Agents).

## 1. Read first, in this order
1. `docs/QUORUM_BRIEF.md` (source of truth; wins over every other file)
2. `docs/TEAM_PLAN.md` (ownership, checkpoints; your section is Person 3)
3. `docs/CONTRACT.md` and `contracts/` (the REST and SSE contract; Section 5 below is the proposed starting point). Once frozen, it is authoritative for every field name, enum and shape. If Section 5 below and `docs/CONTRACT.md` ever differ, `docs/CONTRACT.md` wins.
Flag anything ambiguous or contradictory instead of guessing.

## 2. Start now
Build now. The hard deadline is the hackathon submission close, Oct 18, 2026, 11:59 PM IST. The first move is the contract freeze (Section 4, rule 1) with Janani and Aaditya, because everything else depends on it. Scaffold, tokens and the design plan can proceed in parallel while they confirm it, but do not build against fields that are not yet agreed.

## 3. Mission
Build the console judges will look at for 90 seconds, plus the secondary views that prove the Track 02 and Track 03 focuses. It must look like a shipped product, not a hackathon dashboard, and it must never lie: every number on screen comes from the API.

What Quorum is, in one line: Git revert for agent beliefs. Agents share memory; when a belief is wrong, Quorum rolls back that fact and everything built on it, and no later retrieval can return it. The UI makes that visible: a causal graph (Source, Fact, Conclusion, Action), a history feed, a containment report, a prompt diff, a time slider, handoffs, and a review queue.

## 4. Alignment rules: how we make sure nothing breaks
You cannot guarantee a backend that does not exist yet, so make drift impossible to miss.
1. **Contract first.** Before building any data-driven UI, get the contract in `contracts/` frozen by Janani, Aaditya and Hamza (target: the first working session). After that, a shape change needs all three to agree and a version bump.
2. **One source of truth for types.** REST types are generated from the backend's FastAPI OpenAPI (`/openapi.json`, via `openapi-typescript`) and checked in as a snapshot. SSE event shapes come from `contracts/events.schema.json`. No hand-copied types anywhere else.
3. **Runtime validation at the boundary.** Every REST response and SSE event passes through a zod schema in `src/api/`. Objects accept unknown extra fields (`passthrough`); unknown enum values map to an `UNKNOWN` member that renders neutrally and logs once in the dev console panel. A validation failure never white-screens: the affected panel shows an error state naming the endpoint and the field.
4. **Version handshake.** `GET /meta` returns `contract_version`. If the major version differs from the one the UI was built against, show a blocking screen listing both versions. Minor differences show a dismissible banner.
5. **Two data modes, never confused.**
   - `VITE_API_MODE=live`: real backend.
   - `VITE_API_MODE=mock`: MSW REST handlers plus a scripted SSE timeline; every fixture is validated by the same zod schemas, and a permanent banner reads "Mock data, not from Quorum" so a mock run can never be recorded as the real demo.
   - Separately, the backend has Replay and Live agent modes (cached vs real LLM). `/meta.mode` says which; show it truthfully in the header.
6. **Drift check.** `npm run contract:check` diffs the live `/openapi.json` against the checked-in snapshot and validates every fixture. Run it at the start of each day and before every merge to `main`.
7. **Honesty.** Never compute, estimate or hard-code a figure the API did not send. Seeded values (procedure scores) carry `seeded: true` from the API and the UI labels them "seeded". Benchmark views show `generated_at` and run parameters.
8. **Tenant and roles.** The tenant comes from the token, never from request bodies or URLs the UI builds. The UI uses a `human` token for review actions and an `auditor` or `human` token for scenario and quarantine controls. Tokens are entered through the sign-in screen, never published in public config files or committed.
9. **Same origin.** The frontend calls `/api/...` through the dev proxy and the compose reverse proxy; no hard-coded hosts, no CORS dependence.

## 5. Contract (proposed starting point; Janani and Aaditya confirm or amend)
Conventions: ids are strings (`fact_...`, `conc_...`, `act_...`, `hand_...`); all times are `Ms` (epoch milliseconds, FalkorDB `timestamp()`); errors use `{ error: { code, message, details? } }` with codes `UNAUTHENTICATED, FORBIDDEN_TENANT, FORBIDDEN_ROLE, NOT_FOUND, CONFLICT, GUARD_REFUSED, VALIDATION, INTERNAL`. Every response carries `X-Request-Id`; Cypher-log entries and SSE events that stem from a request carry the same `request_id`.

```ts
type Ms = number;
type Status = 'HEALTHY' | 'SUPERSEDED' | 'QUARANTINED';
type NodeKind = 'source' | 'fact' | 'conclusion' | 'action';
type EdgeType = 'LEADS_TO' | 'SUPPORTS' | 'CONTRADICTS' | 'SUPERSEDES' | 'FROM_SOURCE';

interface GraphNode {
  id: string; kind: NodeKind; is_decision?: boolean;
  label: string;                       // server-made, <= 60 chars
  text?: string; key?: string; value?: string;
  status: Status; stale: boolean;
  valid_from?: Ms; valid_to?: Ms | null;
  quarantined_at?: Ms | null; released_at?: Ms | null;
  quarantine_root?: string | null;
  depth?: number | null;               // hop depth from the root of the latest cascade
  source_ref?: string; quote?: string; origin?: string;
  asserted_by?: { id: string; name: string; tier: 'root' | 'established' | 'new' };
  source?: { kind: 'DOC' | 'TICKET' | 'CHAT' | 'WEBHOOK'; title: string; url: string; version: string };
  owner_team?: string;
  action?: {
    type: string; reversibility: 'REVERSIBLE' | 'COMPENSABLE' | 'IRREVERSIBLE';
    executed: boolean; confirmed: boolean; gate: 'PASSED' | 'BLOCKED';
    procedure_id?: string | null;
    compensation?: { status: 'PENDING_COMPENSATION' | 'DISPATCHING' | 'COMPENSATED' | 'FAILED'; attempts: number } | null;
  };
}
interface GraphEdge { id: string; source: string; target: string; type: EdgeType }
interface GraphResponse {
  contract_version: string; tenant: string; epoch: number; seq: number;
  as_of: Ms | null; generated_at: Ms; truncated: boolean;
  nodes: GraphNode[]; edges: GraphEdge[];   // ALL statuses, not just healthy
}
```

Action display state (derive in one pure function, unit-tested):
| Fields | Shown as |
|---|---|
| HEALTHY, not executed, gate PASSED | Staged |
| HEALTHY, not executed, gate BLOCKED | Held for review |
| executed, not confirmed | Running |
| executed, confirmed, HEALTHY | Ran |
| QUARANTINED, not executed | Cancelled |
| QUARANTINED, executed, compensation PENDING_COMPENSATION or DISPATCHING | Rolling back |
| QUARANTINED, executed, compensation COMPENSATED | Rolled back |
| QUARANTINED, executed, compensation FAILED | Rollback failed |
| QUARANTINED, executed, IRREVERSIBLE | Needs a person |

**REST** (all under `/api`, `Authorization: Bearer <token>`):
- `GET /meta` -> `{ contract_version, mode: 'replay'|'live', tenant, role, epoch, seq, replay_available }`
- `GET /graph?as_of=<Ms>` -> `GraphResponse`
- `GET /node/{id}` -> `{ node, path: GraphNode[] /* root sources to this node, ordered */, descendant_count, status_history: { ts: Ms; status: Status; reason: string }[] }`
- `POST /scenario/{step}` where step is `inject_poison | run_agents | audit | supersede | restore | reset` -> `{ run_id, accepted: boolean }` (progress arrives over SSE)
- `GET /blast/{id}/preview` -> `{ root_id, node_ids: string[], facts: number, conclusions: number, actions: { staged: number; executed_reversible: number; executed_compensable: number; executed_irreversible: number } }` (read-only dry run)
- `GET /blast/{id}/report` -> `{ root_id, quarantined_at: Ms, ms_to_containment: number, facts: number, conclusions: number, actions_cancelled: number, actions_compensated: number, actions_escalated: number, node_ids_by_depth: string[][] }`
- `GET /handoffs` -> `Handoff[]`: `{ id, kind: 'TASK'|'REVIEW'|'ESCALATION', task, status: 'PENDING'|'IN_PROGRESS'|'COMPLETED'|'TIMED_OUT', assigned_to?, claimed_by?, claimed_at?, lease_until?, context_root_id? }`
- `POST /handoffs/{id}/review` body `{ decision: 'approve'|'reject', note?: string }` (human token only; a 403 `FORBIDDEN_ROLE` is rendered as "Only a person can decide this")
- `GET /actions` -> `GraphNode[]` filtered to actions, newest first
- `GET /agents` -> `{ id, name, role, tier, last_active?: Ms }[]`; `GET /agents/{id}/resume` -> `{ session_id, summary, memories: { fact_id, text, source_ref, quote }[] }`
- `GET /procedures` -> `{ id, task_type, steps: string[], uses, successes, failures, rank_score, seeded: boolean }[]`
- `GET /prompt-diff?agent=<id>` -> `{ epoch_before, epoch_after, before: { node_id, text, status }[], after: { node_id, text, status }[], removed_ids: string[] }`
- `POST /brain/ask` `{ question }` -> `{ refused: boolean, reason?, answer?, citations: { source_title, url, version, quote, owner_team, valid_from, stale }[] }`
- `GET /who-knows?topic=` -> `{ person, team, evidence_count, source_ids: string[] }[]`
- `GET /decisions/{id}/trace` -> `{ claim, quote, source_title, url, version, owner }[]`
- `GET /invariants` -> `{ ok, checked_at, checks: { name, ok, value }[] }`
- `GET /cypher-log?request_id=&limit=` -> `{ ts, request_id?, name, cypher, ms, rows }[]` (parameters redacted)
- `GET /bench/results` -> `{ generated_at, params, concurrency: { agents, ops_per_s, p50_ms, p95_ms, p99_ms }, handoff_race: { handoffs, claimers, single_winner_rate }, invalidation_under_load: { violations }, poison: { depth: number; systems: { name: string; leaked_rate: number; wrongly_quarantined: number; ms_to_containment: number }[] }[] }`
- `GET /tenants/denied-demo` -> `{ tenant, attempted_graph, result: 'DENIED' | 'ALLOWED', detail, visible_graph_names: string[] }`

**SSE** `GET /api/events` (use `@microsoft/fetch-event-source` so the Authorization header works; honor `Last-Event-ID`). Envelope: `{ seq, ts, epoch, type, request_id?, data }`. Types and `data`:
- `node_added` `{ node: GraphNode }`; `edge_added` `{ edge: GraphEdge }`
- `status_changed` `{ id, status, stale?, depth?: number, root_id?: string }`
- `epoch_changed` `{ epoch, reason: 'quarantine' | 'restore' | 'supersede' }`
- `gate_blocked` `{ action_id, handoff_id, reason }`
- `action_executed` `{ action_id, confirmed: boolean }`
- `compensation_updated` `{ action_id, status, attempts }`
- `handoff_claimed` `{ handoff_id, winner, losers: string[], lease_until: Ms }`; `handoff_updated` `{ handoff: Handoff }`
- `invariant_status` `{ ok, violations: number }`
- `scenario_progress` `{ run_id, step, state: 'started'|'finished'|'failed', detail? }`
- `resync_required` `{}` and `heartbeat` `{}`

Open items for Janani and Aaditya (settle at the contract freeze, write answers into `docs/CONTRACT.md`):
1. How the UI gets its tokens in the demo (static demo tokens in runtime config is my default).
2. Is `depth` on `/graph` nodes (latest cascade) or only on `status_changed` events? The UI needs both for as-of replays.
3. Exact `as_of` semantics (latest quarantine window only, per the brief) and whether `/graph` returns superseded and quarantined nodes (UI needs them all).
4. Node count limits and `truncated` behavior; paging if ever needed.
5. What `reset` does to seeded data, and how long it takes.
6. Whether `/scenario` steps are idempotent if clicked twice.

## 6. Stack and architecture (confirm with the team)
React 18 + Vite + TypeScript (strict, no `any`) + Tailwind (tokens as CSS variables) + TanStack Query (server state) + Zustand (UI and live-graph state) + Framer Motion (non-graph motion) + zod + Cytoscape.js with `cytoscape-dagre` (or ELK) + visx or Recharts for benchmark charts + MSW (mock mode) + Vitest + Playwright. Fonts and icons are bundled (`@fontsource/...`, local SVG sprite): `docker compose up` must work with no network.

Layout of `frontend/src/`: `api/` (client, zod schemas, generated types), `state/` (stores, event reducer), `graph/` (Cytoscape adapter, styles, layout, wave animation; imperative, outside React renders), `components/`, `views/` (Console, Memory, Brain, Tenants, Benchmark), `copy/` (all user-facing strings in one file), `mocks/`, `test/`.

## 7. Art direction (use this; refine, do not replace)
Calibration: avoid the generic looks (dark mode with one neon accent, cream paper with a serif, identical rounded cards with the same shadow, all-caps tracked eyebrow labels, middle-dot meta strings, arrows appended to buttons, monospace for every small label).

**Concept:** a forensic ledger drawn on drafting paper. A calm, light, high-contrast instrument that treats the belief graph like evidence. Most screens are dark dashboards; this one stands out by being quiet, precise and light.
- **Color (verify every text pairing at 4.5:1 or better):** vellum `#E6EBEF` (canvas), paper `#F7F9FA` (panels), ink `#101C2B` (text, lines), graphite `#566577` (secondary text). Status inks: healthy verdigris `#17806D`, quarantined stamp red `#C62F1B`, out of date ochre `#B5760F` (use `#8A5A0B` for text), superseded pencil `#8D9AA8`, review and human actions ultramarine `#3B3FD1`. Hairlines are ink at 14% opacity. No gradients, no glow, no glass.
- **Type:** two clearly different families. Schibsted Grotesk (400, 500, 700) for UI, headings and big numerals (tabular figures); JetBrains Mono only for things that really are code (ids, Cypher, timestamps in the Cypher drawer). Scale 12, 13, 14, 16, 20, 28, 44. Sentence case everywhere. Lines under 80 characters.
- **Shape language, not a card kit:** panels are flat with 1 px ink hairlines and 2 px corners; chips are pills; graph nodes are shape-coded (fact circle, conclusion rounded rectangle, decision a rectangle with a double border, action square with an icon, source a folded-corner document). Vary radius by role; do not put everything in the same box.
- **The canvas is the signature:** a faint drafting grid with four labeled column rails (Sources, Facts, Conclusions, Actions) that encode the causal order; labels are small sentence-case text on the rail, not decoration.
- **Spend boldness in two places only:** (1) the quarantine wave: a red ring draws around each node in order of hop depth (about 120 ms per hop, ink-stamp easing), edges into it turn dashed red, then compensation badges settle; (2) the prompt diff, styled like a code review with removed lines struck through and chipped to graph nodes. Everything else stays quiet.
- **Motion:** one orchestrated sequence per major event (the wave, the claim race), plus responses to clicks (expand, confirm). No scroll-triggered fades, no hover lifts on every card. Respect `prefers-reduced-motion` (the wave becomes an instant state change with a short highlight).
- **Voice:** plain and specific. Statuses: Healthy, Quarantined, Replaced by a newer fact, Out of date. Actions: Staged, Held for review, Ran, Rolled back, Needs a person. Buttons name the result: "Inject poisoned webhook", "Audit memory", "Restore", "Replace policy". Errors say what failed and what to do ("Can't reach Quorum at /api. Check that the backend is running, then retry."). Empty states invite action ("No beliefs yet. Run the scenario to watch agents form facts.").
- Before building, write a short design plan (palette, type, layout, principles) in `docs/FRONTEND_DESIGN.md`, review it against the generic defaults above, say what you changed and why, then build.

## 8. Layout and components

```
┌ Quorum   Console  Memory  Brain  Tenants  Benchmark    tenant ▾  Replay | epoch 14 | Invariants hold ┐
├────────────┬───────────────────────────────────────────────┬──────────────────┤
│ History    │  Sources   Facts   Conclusions   Actions      │ Containment      │
│ (feed)     │  causal graph on drafting grid                │ report (docked   │
│            │                                               │ while a cascade  │
│ Scenario   │  [fit] [collapse] [search] [lock layout]      │ is shown)        │
│ controls   │                                               │ ──────────────── │
│            │                                               │ Inspector: Path, │
│            │                                               │ Details, Cypher  │
├────────────┴───────────────────────────────────────────────┴──────────────────┤
│ Timeline: poison · quarantine · supersede markers · as-of slider · play        │
└──────────────────────────────────────────────────────────────────────────────┘
```
Desktop-first (1440 and up, comfortable at 1280, usable at 1024; no horizontal page scroll).

Console components (each needs loading, empty, error and disconnected states):
1. **Header:** tenant selector, mode badge (Replay or Live from `/meta`), epoch badge (pulses and a toast on `epoch_changed`), invariant light (from `invariant_status`, with a popover listing `/invariants` checks), connection status.
2. **History feed:** events written like commits ("Quarantined fact_90d: 11 nodes held, 2 refunds rolling back"); newest first; click focuses the node; filters by type; `aria-live` polite.
3. **Scenario controls:** the demo buttons wired to `POST /scenario/{step}`, disabled while a step runs, with `scenario_progress` shown inline; an "Autopilot" button that runs the 90-second sequence.
4. **Causal graph canvas** (Section 9).
5. **Containment report:** facts, conclusions and actions affected; cancelled vs rolled back vs needs a person; ms to containment with count-up. Appears when a cascade event arrives and persists until dismissed. Hovering a node shows the dry run from `/blast/{id}/preview` as a highlighted subgraph plus counts.
6. **Inspector:** Path (root sources to the node, with quotes and source origin), Details (status history, asserted by, owner team, action display state), Cypher (the "Show Cypher" drawer from `/cypher-log?request_id=`, with ms and rows).
7. **Prompt diff:** before and after context for a chosen agent from `/prompt-diff`; removed lines struck through, each chipped to its node (click to focus); shows the epoch before and after and states plainly "No later retrieval returns these."
8. **Action ledger and handoff board:** actions with display state and Saga status; handoffs by kind with lease countdown rings; the claim race animation driven by `handoff_claimed` (winner highlighted, losers dimmed); REVIEW queue with Approve and Reject that calls `/handoffs/{id}/review` and handles the 403 role error.
9. **Timeline and as-of:** markers from event history, a slider that refetches `/graph?as_of=` and swaps the canvas into a clearly marked "Viewing the past" state (ink-blue frame, "Back to now" button); a belief diff between two timestamps computed client-side by diffing two `/graph` responses (added, removed, quarantined).
10. **Presentation mode:** hides chrome, enlarges the canvas and containment report, hides the dev console and mock banner only in live mode.

Secondary views:
- **Memory:** agents (`/agents`), procedure ranking (`/procedures`, "seeded" label from the API), and the resume panel (`/agents/{id}/resume`: "What this agent remembers and where it came from").
- **Brain:** Ask box (`/brain/ask`) with citations, owner team and freshness; a refusal state that explains why; who-knows leaderboard (`/who-knows`); decision trace (`/decisions/{id}/trace`).
- **Tenants:** the denied-read demo (`/tenants/denied-demo`): attempted graph, result, and the visible graph names.
- **Benchmark:** charts from `/bench/results` (latency percentiles, handoff race, poison results by depth against both baselines) with `generated_at` and params visible.

## 9. Graph canvas spec
- Layered left-to-right layout (dagre or ELK), ranks fixed by node kind into the four columns. New nodes are placed without moving existing ones (lock positions after first placement; "Re-layout" is a manual button).
- Show about 60 labels at once: label on hover or zoom, truncate to the server `label`, full text in the Inspector. Collapse and expand subtrees. Minimap or fit control. Handle 400 nodes without dropping below 50 fps.
- Encoding (never color alone): shape by kind, ring and fill by status, dashed outline for quarantined, grey ghost for superseded, dotted ochre ring for stale, lock glyph for gate BLOCKED, check glyph for confirmed. Edges: LEADS_TO solid with arrowhead, SUPPORTS dashed, CONTRADICTS red, SUPERSEDES long-dash grey, FROM_SOURCE thin.
- Keep the Cytoscape instance outside React state. Apply changes in `cy.batch()`. Throttle event application to animation frames.
- The wave: on `status_changed` events with `depth`, schedule each node's state change at `depth * 120 ms`; coalesce events that arrive within one frame. When replaying a past state (`as_of`), skip the animation.
- Accessible alternative: a keyboard-navigable list view of the same nodes (toggle in the toolbar), with focus rings that match canvas selection.

## 10. Event handling
- One reducer applies events in `seq` order. Duplicates (seq already applied) are ignored. A gap in `seq` or a `resync_required` triggers a refetch of `/graph` and a replay of the buffered events after its `seq`.
- `epoch_changed` invalidates dependent queries (prompt diff, procedures, handoffs) and shows a toast ("Memory changed: quarantine. Agents will rebuild their context.").
- On disconnect: keep the last graph visible, mark it as stale with a banner, retry with backoff, resync on reconnect.
- Authorization failures: 401 shows a sign-in-needed state; 403 `FORBIDDEN_TENANT` shows the denial state (it is also part of the demo); 403 `FORBIDDEN_ROLE` explains who can do it.

## 11. Demo choreography (the 90-second path must work from the Scenario controls with no terminal)
1. **Poison:** "Inject poisoned webhook". `node_added` grows the graph live; Refunder stages 3 refunds, 2 execute (Ran); Comms' email is Held for review (`gate_blocked`, lock glyph, REVIEW appears in the board).
2. **Correct:** "Audit memory". The wave runs by depth; containment report docks (counts, ms to containment); the prompt diff shows the poisoned context gone; the third refund shows Cancelled with a visible "Refused by the execute guard" entry in History; the invariant light stays green.
3. **Time travel and learning:** the as-of slider replays the earlier belief; Memory view shows the Refunder's procedure ranking changed; restart-and-resume shows the "what this agent remembers" panel.
4. **Legitimate update:** "Replace policy" supersedes the fact, dependent decisions show Out of date and a REVIEW handoff reaches the owning team; Brain answers "who owns this?" with citations; Tenants shows the denied read.
Provide `?demo=1` to hide dev affordances. The Autopilot runs the same four steps with fixed pauses so the video can be recorded in one take.

## 12. Quality floor
- Every panel: skeleton while loading, empty state, error state naming the endpoint, disconnected state.
- Keyboard: all controls reachable, visible focus, shortcuts for fit (F), search (/), toggle list view (L), presentation mode (P), dismiss (Esc); shortcut legend on `?`.
- Accessibility: WCAG AA contrast, `aria-live` on History and toasts, labels for icon buttons, reduced motion honored, status never conveyed by color alone.
- Performance: first paint under 2 s locally, event batch applied under 16 ms, no layout thrash, no memory growth over a 10-minute run.
- No console errors or unhandled rejections in a full demo run. Strict TypeScript, ESLint clean.

## 13. Verification
`npm run verify` runs typecheck, lint, unit tests (event reducer, action display-state function, zod schemas against every fixture, as-of diff), contract check, and a production build. Playwright runs the four-step demo in mock mode (deterministic timeline) and, when the backend is up, in live Replay mode; save screenshots of each beat to `docs/screens/` for the README. Never say a screen works without having run it and looked at it.

## 14. Phases (work back from Oct 18, 11:59 PM IST; target submitting by about 8 PM IST)
- **Phase 0 (first session):** freeze the contract with Janani and Aaditya; write `docs/FRONTEND_DESIGN.md`; scaffold, tokens, app shell; zod client and mock mode; hand Aaditya the shot list and replay fixtures; README skeleton with both focus tables.
- **Phase 1:** canvas rendering a fixture graph with every status style; the SSE event reducer; History feed and Inspector against mock mode.
- **Phase 2:** switch to live data and SSE as soon as Aaditya's endpoints and fixture events exist; action ledger and handoff board, Cypher drawer, prompt diff, first pass of the wave.
- **Phase 3:** containment report and dry run, as-of slider and belief diff, Memory, Brain, Tenants and Benchmark views, presentation mode; rehearse the demo end to end and log every glitch; dry-run the submission form.
- **Phase 4 (Oct 18 until noon IST):** polish and freeze; README with real numbers from Janani's `make proof`; record the 90-second video; write the blog post; submit.
Daily: contract check and a 15-minute sync with Janani and Aaditya at the start and end of each working day. Merge freeze at noon IST on Oct 18 (fixes only afterwards).

## 15. Done when
- The four demo steps run from the Scenario controls in live Replay mode three times in a row with no console errors.
- `npm run contract:check` passes against the live backend.
- Every Track 02 and Track 03 focus in the README table links to something clickable or a command.
- `docs/FOCUS_CHECKLIST.md` is updated for the visible moments (claim race, denied read, restart and resume, as-of, supersede and stale, cited answers), each marked "verified" only after you ran it.

## 16. Working rules
- Never invent data. If an endpoint is missing or wrong, raise it with the owner (Janani for graph and query-log fields, Aaditya for endpoints and events) and show an honest error state meanwhile.
- Keep all user-facing strings in `src/copy/`.
- Keep every component small enough that Hamza can explain it.
- All data synthetic. No secrets in the repo.
- Commits are made under Hamza's own GitHub identity only; never add Claude or any AI as author, co-author or in a trailer (AI use is disclosed in the README). Use the GitHub CLI (`gh`) when it is authenticated.

## 17. First reply
Before building, reply in at most 12 lines: confirm the contract-first process, the design plan changes you made after reviewing against the generic defaults, and list open questions for Janani and Aaditya (start with the six open items in Section 5). Then start Phase 0.
