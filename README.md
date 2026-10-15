> **Supported deployment: one API writer process with embedded MCP.** Execute guards and handoff claims run under process locks. One FalkorDB checkpoint statement stores authoritative recovery state; graph projections are independent, replayable writes. Separate API workers/standalone MCP writers are unsupported. Historical helper benchmarks below are not production API latency guarantees. See [audit remediation notes](docs/AUDIT_REMEDIATION.md).

# Quorum: Graph-Native Correctable Memory & Coordination for Multi-Agent Systems

<div align="center">

**Git revert for AI agent beliefs.**  
*Quorum quarantines a poisoned fact and its causal descendants. Healthy context excludes them until authorized restoration.*

[![CI](https://github.com/HamzaShaikh-source/Quorum-falkordb/actions/workflows/ci.yml/badge.svg)](https://github.com/HamzaShaikh-source/Quorum-falkordb/actions/workflows/ci.yml)
[![Backend Tests](https://img.shields.io/badge/pytest-CI%20gate%20%E2%89%A590%25%20coverage-brightgreen?style=flat-square)](backend/tests)
[![Frontend Tests](https://img.shields.io/badge/vitest-114%20passed-brightgreen?style=flat-square)](frontend)
[![Accessibility](https://img.shields.io/badge/axe--core-0%20violations%20(22%20screens)-brightgreen?style=flat-square)](frontend/scripts/a11y.mjs)
[![Types](https://img.shields.io/badge/mypy%20%2B%20ruff-clean-brightgreen?style=flat-square)](mypy.ini)
[![Benchmark](https://img.shields.io/badge/historical%20helper%20throughput-757.6%20ops%2Fsec-blue?style=flat-square)](bench/results.json)
[![Containment](https://img.shields.io/badge/historical%20helper%20traversal-0.4ms-blue?style=flat-square)](docs/PROOF.md)
[![Leakage](https://img.shields.io/badge/historical%20dataset%20leakage-0.0%25-brightgreen?style=flat-square)](bench/results.json)
[![Hackathon](https://img.shields.io/badge/Graph%20Hacks-Oct%2015--18%2C%202026-orange?style=flat-square)](docs/QUORUM_BRIEF.md)

</div>

---

## In one minute

Quorum is shared memory for AI agents where a wrong fact and its causal descendants can be quarantined, excluding them from healthy context. Latency depends on graph size and persistence; historical helper traversal timings are not end-to-end API timings. Run `docker compose up -d`, open http://localhost:5173, and follow the **Get started** card on the Connect page. Who it is for, the story, and what the numbers do and do not say: [`docs/IMPACT.md`](docs/IMPACT.md). Security model: [`SECURITY.md`](SECURITY.md). A real deployment (password-protected FalkorDB, TLS, tokens, sign-in): [`docs/SETUP.md`](docs/SETUP.md#production-in-one-command).

A fresh install is **empty on purpose** (no sample data). The guided poisoned-webhook story is opt-in: `QUORUM_DEMO=1 docker compose up -d`.

## Executive Summary

When an autonomous agent ingests an erroneous or malicious fact, standard RAG architectures (flat vector stores and naïve graph search) permanently contaminate downstream memory. As agents derive conclusions, execute external actions, and coordinate across handoffs, the poisoned context cascades silently.

**Quorum** solves this by storing all agent beliefs, derived conclusions, and staged actions inside a **causal Directed Acyclic Graph (DAG) on FalkorDB**. Every thought and action traces its provenance back to verifiable sources. When a root belief is invalidated, Quorum traverses downstream edges, excludes quarantined beliefs from retrieval, guards pending executions in its single API process, and persists quarantine state with compensation intent in one FalkorDB statement. A lifecycle worker recovers delivery using expiring leases and stable idempotency keys; latency depends on graph size and persistence costs.

---

## System Architecture & Defense Topology

```
                  ┌────────────────────────────────────────┐
                  │       Ingest Channels & Sources        │
                  │   (Policy Docs, Webhooks, Slack Chats) │
                  └───────────────────┬────────────────────┘
                                      │ Origin Verified
                                      ▼
                        ┌───────────────────────────┐
                        │      Quorum Gate (L1)     │◄── Root Backed / 2+ Corroborated Sources
                        │ (Prevents Unsafe Staging) │
                        └─────────────┬─────────────┘
                                      │ Gate PASSED
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                          FalkorDB Commons Graph Engine                          │
│                                                                                 │
│   [:FROM_SOURCE]           [:LEADS_TO]                 [:LEADS_TO]              │
│  [Source Node] ──► [Fact: Root 30d] ──► [Conclusion: Eligible] ──► [Action: Refund]  │
│                           ▲                                      │              │
│              [:SUPERSEDES]│                                      ▼              │
│                    [Fact: Policy 45d]                 [Compensation Record]     │
│                                                                  │              │
│                           Quarantine Red Wave (L3)               │              │
│                 ◄─── Traverses [:LEADS_TO|SUPPORTS*] ───         │              │
└─────────────────────────────────────┬────────────────────────────┼──────────────┘
                                      │                            │
                                      ▼                            ▼
                      ┌──────────────────────────────┐   ┌───────────────────────┐
                      │   Atomic Execute Guard (L2)  │   │   Outbox Saga Worker  │
                      │  (Single Cypher Check-&-Set) │   │ (Automated Reversals) │
                      └──────────────┬───────────────┘   └───────────┬───────────┘
                                     │                               │
                                     ▼                               ▼
                      ┌──────────────────────────────────────────────────────────┐
                      │          Mock Helpdesk API & Tombstone Protection        │
                      │         (Idempotent Execution / 409 Conflict Races)      │
                      └──────────────────────────────┬───────────────────────────┘
                                                     │
                                                     ▼
                                      ┌─────────────────────────────┐
                                      │  Damage Control Engine (L4) │
                                      │  (Auto-Retraction Notices)  │
                                      └─────────────────────────────┘
```

---

## The Four Layers of Defense

Quorum enforces four deterministic, non-LLM safety layers:

1. **PREVENT (Layer 1 — The Quorum Gate):**
   - Evaluated during `stage_action`.
   - Irreversible and financial operations require either root-tier policy backing or independent corroboration from 2+ distinct agent origins (`SUPPORTS` edges).
   - If uncorroborated, stale, or quarantined, the action is **BLOCKED** and routed to a human `REVIEW` handoff queue. Reversible/compensable actions are permitted.

2. **GUARD (Layer 2 — Atomic Execute Guard & Tombstones):**
   - The in-process lock around `execute_guard` verifies that the action is `HEALTHY`, gate `PASSED`, not yet executed, and has **zero non-healthy causal ancestors** across both `[:LEADS_TO|SUPPORTS*]` paths.
   - If an action races against a quarantine wave, it is aborted with zero rows returned.
   - If an external call was in flight, the **Helpdesk Tombstone Engine** records a tombstone for compensations arriving first; any subsequent late execution attempt is strictly rejected with `409 Conflict`.

3. **REPAIR (Layer 3 — Quarantine Red Wave & Outbox Saga):**
   - Unbounded cascade follows `[:LEADS_TO|SUPPORTS*]`, marking all derived descendants `QUARANTINED` with their hop depth and root anchor.
   - Atomically increments the tenant's **Context Epoch**. Any agent making MCP calls with a stale epoch is forced to rebuild context.
   - Strikethroughs in the **Prompt Diff** visually and programmatically remove quarantined facts from LLM context.
   - An asynchronous **Outbox Saga Worker** leases pending compensation rows and calls external APIs with backoff and idempotency keys to reverse executed side effects.

4. **DAMAGE CONTROL (Layer 4 — Automated Retraction Notices):**
   - When an executed `IRREVERSIBLE` action (e.g. an email already sent to a customer) is hit by the Quarantine Cascade, Quorum automatically intervenes.
   - Generates a structured `CorrectionNotice` linked to the verified truth (`Finance Ops Policy Doc v3.0`) and drafts a ready-to-dispatch retraction email for 1-click human review in the Incident Command Room.


---

## Live Performance & Benchmark Metrics (PROOF PACK)

All metrics are dynamically measured and generated by `python bench/run.py` into [`docs/PROOF.md`](docs/PROOF.md) and [`bench/results.json`](bench/results.json).

```
======================================================================
QUORUM COMPREHENSIVE BENCHMARK & PROOF HARNESS (make proof)
======================================================================

[1/4] 30-Agent Concurrency Benchmark (30 threads, 70/30 read/write):
      Throughput: 757.6 ops/sec | p50: 31.5ms | p95: 78.01ms | p99: 100.0ms
      Invariant Violations: 0

[2/4] Handoff Claim Race (100 handoffs x 30 concurrent claimers):
      Single-Winner Guarantee: 100.0% (100/100 clean claims, 0 double-claims)

[3/4] Atomic Execute-vs-Quarantine Race (50 actions under concurrent audit):
      Executed before quarantine: 1 | Refused after: 49 | Invariant Violations: 0

[4/4] Poison Benchmark across 3 Causal Depths (Fixed Seeds, No LLM):
      Depth 1: Containment: 0.4ms | Quorum Leakage: 0.0% vs Baselines: 100.0%
      Depth 2: Containment: 4.53ms | Quorum Leakage: 0.0% vs Baselines: 100.0%
      Depth 3: Containment: 8.94ms | Quorum Leakage: 0.0% vs Baselines: 100.0%
```

Measured on FalkorDB 8.10.2, on one developer laptop, by the harness above. The baselines are the harness's own two models (flat store with a TTL, and delete-the-root-fact-only); they are models, not products. Numbers depend on the machine: re-run `python bench/run.py` for yours.

| Metric | Flat store + TTL (model) | Delete root fact only (model) | Quorum |
|---|---|---|---|
| **Poison leakage at depth 1 / 2 / 3** | 100.0% / 100.0% / 100.0% | 100.0% / 100.0% / 100.0% | **0.0% / 0.0% / 0.0%** |
| **Time to containment, depth 1 / 2 / 3** | not measured | not measured | **0.4 / 4.53 / 8.94 ms** |
| **Throughput, 30 agents** | not measured | not measured | **757.6 ops/sec** |

---

## Services, Transports & Port Mapping

| Service | Host Port | Transport / Protocol | Description |
|---|---|---|---|
| **Frontend Console & Landing** | `3000` / `5173` | HTTP (React + Vite + Cytoscape) | Dual interface: Landing page (`#/`) and Incident Command Room (`#/app`) |
| **Backend REST & SSE** | `8000` | HTTP / Server-Sent Events | FastAPI core causal API, event streaming, scenario driver |
| **MCP Server** (embedded in the single backend writer; stdio through the bridge) | `8000` (`/mcp`) | Streamable HTTP (`/mcp`) & Stdio | Model Context Protocol server exposing 15 agent, token-saving, and auditor tools |
| **Mock Helpdesk API** | `8002` | HTTP / JSON | External ticketing and refund API with atomic Tombstone protection |
| **FalkorDB Graph Engine** | `6379` | Redis / Cypher | Graph database engine with vector indexing and per-tenant Redis ACLs |
| **FalkorDB Web Browser** | `8080` | HTTP | Interactive FalkorDB graph visualizer |

---

## The Two User Interfaces

### 1. The Marketing Landing Page (`#/`)
- Designed on a crisp editorial paper stock (`#f3f5f2`).
- Features a **live interactive hero**, scroll-spy problem statement, and an interactive **Click-to-Break Playground** allowing judges to inject bad facts and watch traditional RAG break live in their browser.
- Displays the **Defense Carousel**, console feature gallery, live proof metrics, and a 1-click **"Watch the 90-second story"** button that jumps directly into the Incident Command Room and starts Autopilot.

### 2. The Console (`#/app`)
- Designed as a printed field manual: ink on paper with one mint accent. Every tab is labeled and every status chip is in plain words.
- **Interactive Cytoscape DAG Canvas:** Real-time visualization of `Fact`, `Conclusion`, `Action`, and `Handoff` nodes with layered hierarchical layout.
- **Quarantine Red Wave:** Live animated ripple coloring poisoned nodes crimson by causal hop depth.
- **Prompt Diff Drawer:** Strikethrough comparison showing exact tokens stripped from agent memory.
- **Action Ledger & Handoff Board:** Live tracking of concurrent claim races, lease countdowns, and human review queues.
- **Time-Travel Slider:** Drag backwards in time to reconstruct the exact graph state at any millisecond (`as_of`).
- **AI at work (Overview):** a live feed of everything connected agents are reading, writing or being refused, each step with a plain-language purpose, so you can see exactly what the AI is using the memory for.
- **Guide (press `G`):** what the AI does here (read from the hub's tool catalog), a searchable **A to Z glossary** of every term, and the shortcuts. Each glossary entry links to the screen where you can see it.
- **Show Cypher Drawer:** Live inspect the exact parameterised Cypher statements executed for every operation.

---

## Track Focus Breakdown & Verification

### Track 02: Agent Memory and Coordination (~80% Effort)
| Focus | Description | Implementation | Where to Verify |
|---|---|---|---|
| **T02-1: Episodic, Semantic & Procedural Memory** | Multi-type memory: Messages, Facts, Conclusions, and Laplace-smoothed procedure ranking (`(successes+1)/(uses+2)`). | `backend/app/services/procedural.py`, `graph/queries.py` | `pytest backend/tests/test_safety_engine.py` & `docs/screens/10-memory.png` |
| **T02-2: Persistent Conversation History** | Private raw graphs per agent (`tenant_<id>:agent_<n>:raw`), kill-and-resume 2-query flow, deterministic session summaries. | `backend/app/services/store.py`, `backend/app/routes/meta.py` | `pytest backend/tests/test_api_contract.py` & `docs/screens/10-memory.png` |
| **T02-3: Shared State & Handoffs** | Atomic handoffs with lease timeouts, role-based claims (`TASK` for agents, `REVIEW` for humans). Exactly one winner. | `backend/app/routes/handoffs.py`, `graph/queries.py` | `python bench/run.py` (Suite 2: 100/100 claims) & `docs/screens/07-ledger.png` |
| **T02-4: High Concurrency** | 30 concurrent agent threads with zero orphan invariants under heavy read/write load. | FalkorDB query pool, atomic store | `python bench/run.py` (Suite 1: 757.6 ops/sec on FalkorDB) & `docs/screens/13-benchmark.png` |
| **T02-5: Per-Tenant Memory Isolation** | Per-tenant Redis ACL users (`~tenant_<id>:*`); forbidden cross-tenant reads return 403. | `scripts/users.acl`, `tests/test_tenant_isolation.py` | `GET /tenants/denied-demo` & `docs/screens/12-tenants.png` |

### Track 03: Company Brain (~20% Effort)
| Focus | Description | Implementation | Where to Verify |
|---|---|---|---|
| **T03-1: Docs, Tickets & Chats in One Graph** | Unified enterprise knowledge: `Source` (DOC, TICKET, CHAT) -> `Fact` mapped in commons graph. | `scripts/seed.py`, `backend/app/services/fixtures.py` | `python scripts/seed.py` & `docs/screens/05-contained.png` |
| **T03-2: Who Owns What, Who Knows What** | Ownership & expertise topology: `Team-[:OWNS]->Source`, `Person-[:AUTHORED\|RESOLVED]->Source-[:ABOUT]->Topic`. | `backend/app/routes/brain.py`, `graph/queries.py` | `GET /who-knows?topic=refund_policy` & `docs/screens/11-brain.png` |
| **T03-3: Tracing Decisions to Source** | Full causal audit trail: `Decision <- LEADS_TO <- Fact -> Source` returning quotes, URLs, and owner teams. | `backend/app/routes/brain.py` | `GET /decisions/{id}/trace` & `docs/screens/11-brain.png` |
| **T03-4: Keeping Knowledge Current** | Living knowledge: `SUPERSEDES` by key, stale propagation (`stale=true`), and review handoffs to owning teams. | `backend/app/routes/scenario.py` | `POST /scenario/supersede` & `docs/screens/09-supersede.png` |
| **T03-5: Answers with Clear Sources** | Grounded answers: every answer carries citations and freshness. Untraceable queries are strictly refused. | `backend/app/routes/brain.py` (`/brain/ask`) | `POST /brain/ask` & `docs/screens/11-brain.png` |

---

## Quickstart & Reproduction Guide

> **FalkorDB is required.** Quorum stores every belief, handoff and action in FalkorDB. Without it the API answers `503 FALKORDB_UNAVAILABLE`, the MCP server refuses tool calls and the console shows a locked setup screen with the steps to fix it. There is no sample-data fallback in a normal build. Full guide: [`docs/SETUP.md`](docs/SETUP.md).

```bash
docker compose up -d
python scripts/doctor.py      # checks FalkorDB, backend and MCP, and prints the fix for anything missing
```

Then open:
- **Console:** `http://localhost:5173` (it opens by itself once FalkorDB is ready)
- Backend API docs: `http://localhost:8000/docs`
- MCP server (embedded in the backend): `http://localhost:8000/mcp`
- FalkorDB Browser (optional): `docker compose --profile tools up -d falkordb-browser`, then `http://localhost:8080`

Connect an AI in one line (the console's **Connect** page has the same for Cursor, VS Code, Claude Desktop and others):

```bash
claude mcp add --transport http quorum http://localhost:8000/mcp
```

### Without Docker for the app (FalkorDB still runs in Docker)

```bash
docker compose up -d falkordb
pip install -r backend/requirements.txt
QUORUM_ENV=development QUORUM_EMBED_MCP=1 python -m uvicorn app.main:app --app-dir backend --port 8000
cd frontend && npm ci && npm run build && npx vite preview --port 5173
```

### Tests

```bash
python -m pytest backend/tests tests            # requires real FalkorDB; skipped database tests do not count as verification
cd frontend && npm run verify                   # types, lint, unit tests, contrast, contract, builds
cd frontend && npm run coverage                 # logic coverage gate (96% lines)
cd frontend && npm run qa:a11y                  # axe-core on every screen at desktop and phone width
ruff check backend bench scripts mcp tests agents && mypy   # lint and types
cd frontend && npm run qa                       # console on sample data (npm run build:mock, npm run preview:mock)
cd frontend && npm run qa:live                  # console against the live backend with an MCP client as an agent
cd frontend && node scripts/resilience.mjs      # stops FalkorDB under a running product, checks the lock and the recovery
cd frontend && node scripts/claude_e2e.mjs      # real Claude Code sessions using Quorum as shared memory
```

See [`docs/LIVE_TESTING.md`](docs/LIVE_TESTING.md).

---

## Compliance, AI Usage & Transparency

- **Hackathon Window Compliance:** The Quorum team follows Hackathon Rule 8. Scaffolding, architecture design, and contracts were created during planning; core implementation, integration, benchmarks, and verification are conducted cleanly within the event framework.
- **AI Tool Usage:** Modern AI coding tools (Antigravity, Claude Code) were utilized for planning, scaffolding, contract generation, and automated testing. All code was reviewed, debugged, and committed by the human team members under their personal git identities without automated co-author trailers.
- **Data Guarantee:** All data used in the ACME corporation scenarios, synthetic company brain corpus, and benchmark load generation is 100% synthetic. Zero real-world customer PII or proprietary corporate data was used.
- **Determinism:** Quorum's replay mode runs 100% offline with zero external network or LLM API key dependencies.

---

<div align="center">
Built with ❤️ for the <b>Graph Hacks: Context for AI Agents</b> Hackathon (Oct 2026).
</div>

## Context, Connect and Team (console)

- **Context** shows the memory as a mind map for people and as a compact map plus packed answer for agents, with live token numbers against a top-8 retriever, pasted notes and raw JSON. In live mode the numbers come from the backend with the same code agents call, and the page shows what was **measured with real Claude Code sessions**: 78% fewer tool-result tokens and 18% lower session cost on real-sized documents, same answers (`docs/TOKEN_SAVING.md`).
- **Connect** shows a real MCP handshake, gives paste-ready setup for Claude Code, Claude Desktop, Cursor, VS Code, Windsurf, Antigravity and any MCP client, lists the 15 tools (read from the hub), and shows every real tool call (who, what, tokens used vs a top-8 retriever) plus per-member tool-call sessions.
- **Activity** is the audit log in plain sentences (who did what, when, what it cost), filterable, with a CSV export, for people who do not read graphs.
- **Team** manages plan, teams, seats, members and links on the server. Each member gets their own token (shown once, stored as a hash) and presence comes from their real calls. Facts saved with `scope="team"` stay private to the team until a link shares them.

Documentation index: [`docs/README.md`](docs/README.md). Details: [`docs/MCP.md`](docs/MCP.md) (clients, header auth, rate limits, the stdio bridge, passive capture, semantic ranking, HTTPS), [`docs/TOKEN_SAVING.md`](docs/TOKEN_SAVING.md), [`docs/CONTRACT_PROPOSALS.md`](docs/CONTRACT_PROPOSALS.md) (what the backend now provides and what is still open).
