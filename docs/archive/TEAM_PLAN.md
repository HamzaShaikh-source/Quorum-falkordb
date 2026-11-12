# Quorum: Team Plan (3 people)

Team: **Janani** (Person 1, Graph and FalkorDB), **Aaditya** (Person 2, Backend and Agents), **Hamza** (Person 3, Frontend and Narrative).
Companion to `QUORUM_BRIEF.md` (the brief wins on any conflict). REST and SSE shapes live in `CONTRACT.md` (authoritative once frozen). Hard deadline: Oct 18, 2026, 11:59 PM IST; target submitting by about 8 PM IST.

**Team decision on timing:** we are building before the event window opens. Rule 8 on the hackathon Rules page says main coding and design work must begin after the event starts (Oct 15, 12:01 AM IST), and the rules allow disqualification for breaking them. Everyone should know this and accept it before committing. Re-read the Rules tab before submitting.

---

## 0. Split and the moves we made

| Move | From -> To | Why |
|---|---|---|
| `bench/run.py` skeleton, `make proof` plumbing, concurrency + poison benchmark | Aaditya -> **Janani** | DB-heavy; the poison benchmark measures retrieval-layer leakage with no LLM calls. Aaditya supplies gate/guard/saga/token tests as plug-in sections. |
| Mock helpdesk API (with tombstone logic) | Aaditya -> **Janani** | Tiny FastAPI app, close to the Docker setup. |
| Deployment (compose on a host) | unassigned -> **Janani** | She knows the infra and ACL/volume quirks. |
| Scenario script + cached replay fixtures | Aaditya -> **Hamza** | Hamza owns the narrative; Aaditya builds the runner that plays them. |
| Company Brain seed corpus + queries | unassigned -> **Janani** (seed/queries), **Aaditya** (`/brain/ask` + refusal), **Hamza** (Brain view) | Track 03 slice split across all three. |

---

## 1. Shared contracts

**Contract freeze (do this first):** Janani, Aaditya and Hamza sign off `CONTRACT.md` and `contracts/events.schema.json` before the UI is built against live data. After that, a shape change needs all three to agree and a version bump.

**Naming:** ids like `fact_...`, `conc_...`, `act_...`, `hand_...`; all timestamps in ms since epoch (`timestamp()`); tenant comes from the auth token, never request bodies.

**Query library (Janani delivers, Aaditya calls).** One Python module `graph/queries.py` with named functions returning plain dicts. Each call logs `{ts, request_id, name, cypher, ms, rows}` to a query log (powers the UI's "Show Cypher" drawer). Functions: `stage_action`, `derive`, `execute_guard`, `cascade`, `create_compensations`, `supersede`, `restore`, `claim_handoff`, `retrieve`, `as_of`, `provenance`, `who_knows`, `blast_preview` (read-only dry run), `invariant_check`, `bump_epoch`, `graph_snapshot`.

**REST and SSE:** see `CONTRACT.md`. Aaditya serves FastAPI response models that match it (OpenAPI at `/openapi.json`); Hamza generates TypeScript types from that and validates at runtime.

**Git:** each person commits under their own identity; no AI/Claude co-author trailers on anyone's commits (AI use is disclosed in the README instead). Short-lived branches, PR to `main`, merge freeze at noon IST on Oct 18 (fixes only afterwards).

**Daily syncs:** 15 minutes at the start and end of each working day. End-of-day checkpoint is a demo of whatever works.

**Phases and checkpoints:**
- **Phase 0:** contract frozen, stack up, Day-1 results written down, schema and seed loaded, UI renders a fixture graph in mock mode.
- **Phase 1:** core queries (derive, cascade, guard) working; API skeleton serving fixture SSE; UI canvas, History and Inspector in mock mode.
- **Phase 2:** demo steps 1-2 run end to end in replay mode; UI on live data; stack deployed to a host.
- **Phase 3:** full 4-step demo, benchmark numbers, gate, procedures, Brain slice.
- **Phase 4 (Oct 18):** freeze, README, video, blog post, submit.

---

## 2. JANANI: Graph and FalkorDB

**You own:** everything that touches FalkorDB directly, the proof harness, the mock external API, deployment, and the Track 03 data.

### Components
1. **Environment.** `docker-compose.yml` (FalkorDB + backend + frontend + mock helpdesk), persistent volume at the image's *real* data path, one ACL user per tenant (`~tenant_<id>:*`), a second tenant (`globex`) for the denied-read demo.
2. **Day-1 verification report** (`docs/DAY1_RESULTS.md`). Run every item in Brief Section 10 and record the exact working syntax. This unblocks everyone, so do it first.
3. **Schema and indexes.** Vector index (384 dims), lookup indexes on `id`, `key`, `status`; the insert-time DAG guard (parents must exist and be HEALTHY).
4. **Query library** (see above), with the unverified sketches in the brief turned into tested statements: execute guard (`count(DISTINCT)`, `[:LEADS_TO|SUPPORTS*]`), cascade (returns hop depth per node) + separate idempotent compensation creation, supersede + stale propagation, restore (all parents healthy), as-of, provenance, claim, hybrid retrieval, blast preview, invariants, epoch.
5. **Seed script.** Acme Support: 30-day policy root fact, poisoned webhook payload, 3 refund tickets, agents of each tier, procedures with seeded scores (flagged `seeded`); plus the Brain corpus (~25 items, 12 people, 4 teams, 3 projects, 3 systems) and precomputed 384-dim embeddings so `docker compose up` needs no network.
6. **Mock helpdesk API.** Refund, revert, email endpoints with idempotency keys and the **tombstone rule**: a compensation for an unseen key is recorded and a later execute with that key is rejected.
7. **`bench/run.py` and `make proof`.** Concurrency (30 agents, 70/30, p50/p95/p99), 100 handoffs x 30 claimers, invalidation under load, execute-vs-quarantine race, ACL denial including `GRAPH.LIST`, DAG no-cycle, restore incl. diamond case, and the poison benchmark (3 depths, fixed seeds, 2 baselines, retrieval-layer leakage). Prints real numbers and writes `bench/results.json` in the shape `CONTRACT.md` defines.
8. **Deployment.** Stack running on a host as soon as the schema and a first scenario work, final redeploy on Oct 18.

### Needs from others
- Aaditya: the query signatures he wants (agree at the start). Hamza: node fields the graph payload needs (they are in `CONTRACT.md`).

### Done when
- `make proof` runs from a clean `docker compose up` and prints real numbers, with zero HEALTHY nodes under QUARANTINED parents.
- Tenant `globex` is denied reading `tenant_acme:commons` (and cannot list its graph names).
- Data persists across `docker compose down && up`.
- Marked verified in `docs/FOCUS_CHECKLIST.md`: Track 02 #4 and #5, Track 03 #1-3.

### Risks
- A Day-1 syntax surprise (vector function name, `[:A|B*]`, ACL patterns) forces rewrites: report immediately with the working alternative.
- Unbounded traversal slow on larger seeds: PROFILE early and keep the level-by-level cascade fallback.

---

## 3. AADITYA: Backend and Agents

**You own:** the application layer on top of Janani's queries, plus the MCP server and the agents.

### Components
1. **FastAPI app.** Auth (token to tenant and role: agent / auditor / human), the REST endpoints and SSE stream from `CONTRACT.md` (pydantic models that match; OpenAPI at `/openapi.json`), `X-Request-Id` on every response, structured errors, query-log passthrough. Start with fixture SSE events so Hamza can build against realistic data.
2. **Ingest gateway.** Sets `Source.origin` from the authenticated channel or credential only, never from payloads. Sources: policy doc, webhook channel, chat channel.
3. **Quorum gate** (at `stage_action`). Per-leaf rule: root-backed or corroborated (same key and value, different origin, at least one established-or-higher agent). Creates `SUPPORTS` edges on pass, creates a REVIEW handoff on block. Reversible/compensable actions pass.
4. **Execute path.** `execute_action` runs Janani's atomic guard; on zero rows it never calls the external API; on success it calls the mock helpdesk and sets `confirmed`.
5. **Outbox saga worker.** Polls `PENDING_COMPENSATION`, leases rows, calls the helpdesk with idempotency keys, retries with backoff, escalates on failure; treats `executed=true, confirmed=false` older than 60 s as unknown-outcome. IRREVERSIBLE actions produce an ESCALATION handoff and a drafted correction notice.
6. **Auditor service (deterministic).** Contradiction detection, trust rule (root > corroborated > tier; new-tier cap; peers cannot vouch), supersede-authority rule, tie to REVIEW handoff, calls cascade and bumps the epoch.
7. **MCP server.** Agent tools: `remember_fact`, `derive`, `retrieve_context`, `stage_action`, `execute_action`, `claim_handoff` (TASK only). Auditor/human tools: `quarantine`, `restore`, `explain_blast_radius`, REVIEW completion. Tenant enforced from the token; every response carries the epoch.
8. **Agents and runner.** Scout, Analyst, Refunder, Comms, with the epoch-refresh behavior, procedure selection by Laplace-smoothed rank, two agents racing for a claim. **Replay mode** plays Hamza's scripted fixtures; **live mode** calls the LLM. Resume flow for restarted agents (raw graph, then commons via `source_ref`).
9. **Session summaries.** LLM call at session end with a cached fallback; document which one is used.
10. **Brain endpoint.** `/brain/ask` returns citations, owner team, freshness; refuses answers that can't be traced to a Source.
11. **Tests plugged into `make proof`.** Gate (incl. one-uncorroborated-leaf with a root-backed sibling, and corroborator-quarantine re-check), agent token cannot complete REVIEW, compensation idempotency and tombstone race, supersede-authority rule.

### Needs from others
- Janani: query library, helpdesk API, seed. Hamza: scripted scenario and replay fixtures, feedback on the contract.

### Done when
- Replay mode runs demo steps 1-4 deterministically with no network.
- An outside MCP client can connect and call the tools.
- Marked verified: Track 02 #1-3, Track 03 #4-5.

### Risks
- Largest column. If behind at the midpoint, drop live mode first (replay is what the video needs) and ask Janani to take over `/brain/ask`.

---

## 4. HAMZA: Frontend and Narrative

**You own:** everything the judges see, plus the story, the README, and the submission. Full instructions are in `P3_PROMPT.md`.

### Components
1. App shell and design system (light "forensic ledger on drafting paper" direction, Schibsted Grotesk + JetBrains Mono for code only).
2. Causal graph canvas (Cytoscape.js, layered DAG, quarantine wave driven by cascade hop depth).
3. History feed, Inspector (path, details, Show Cypher drawer), containment report with hover dry run.
4. Prompt diff, action ledger and handoff board (claim race, review queue).
5. Time slider and belief diff.
6. Secondary views: Memory, Brain, Tenants, Benchmark. Scenario controller and presentation mode.
7. Narrative and submission: 90-second shot list, replay fixtures (what each agent says and does per beat), README top tables (focus -> feature -> where to verify), graph model and key queries, AI-use disclosure, honest limits, blog post, WeMakeDevs submission form, `docs/FOCUS_CHECKLIST.md` upkeep.

### Needs from others
- Aaditya: endpoints and fixture SSE events. Janani: `bench/results.json`, query log, graph payload fields. Everyone: verified items for the checklist.

### Done when
- The 90-second demo runs from the scenario controls without a terminal, in live Replay mode, three times in a row.
- `npm run contract:check` passes against the live backend.
- Every Track 02/03 focus in the README table points to something clickable or a command.

---

## 5. Coverage check (who proves which focus)

| Focus | Built by | Verified by |
|---|---|---|
| T02-1 episodic/semantic/procedural | Janani schema, Aaditya agents | Hamza demo beat (procedure switch) |
| T02-2 persistent history | Janani raw graphs, Aaditya resume flow | Hamza restart beat |
| T02-3 shared state and handoffs | Janani claim query, Aaditya agents | Hamza race view |
| T02-4 high concurrency | Janani | Janani `make proof` numbers |
| T02-5 per-tenant memory | Janani ACL | Janani test + Hamza denied-read view |
| T03-1 docs/tickets/chats in one graph | Janani seed | Hamza Brain view |
| T03-2 who owns / who knows | Janani queries | Hamza leaderboard |
| T03-3 decisions to source | Janani query | Hamza decision trace |
| T03-4 knowledge stays current | Janani supersede, Aaditya handoffs | Hamza supersede beat |
| T03-5 cited answers | Aaditya `/brain/ask` | Hamza Ask box |

## 6. Cut order (if behind)

PageRank, then slider/as-of UI, then the Brain slice (which means withdrawing from Track 03). Never cut: cascade, healthy-only context builder, execute guard, compensation worker, benchmark, procedure ranking.
