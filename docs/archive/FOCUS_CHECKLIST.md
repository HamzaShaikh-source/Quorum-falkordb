# Quorum Acceptance Focus Checklist

This checklist tracks every core focus item from `docs/QUORUM_BRIEF.md` for Track 02 and Track 03.
**Rule:** Status progresses from `not started` -> `built` -> `verified`. Every item marked `verified` is backed by automated tests, benchmark data, or live screenshots.

---

## Track 02: Agent Memory and Coordination (~80% Effort)

| Focus | Description | Feature Implementation | Status | Verification Command / Proof | UI Evidence |
|---|---|---|---|---|---|
| **T02-1: Episodic, Semantic & Procedural Memory** | Multi-type memory in graph: Episodic (`Message` nodes + `Fact.source_ref`, `Fact.quote`), Semantic (`Fact`, `Conclusion`), Procedural (`Procedure` with ordered `steps[]`, `uses/successes/failures`). | `backend/app/services/procedural.py`, `graph/queries.py` | **verified** | `test_procedural_memory_laplace_ranking` passes. Laplace-smoothed ranking drops post-quarantine and promotes healthy alternatives. | `docs/screens/10-memory.png` |
| **T02-2: Persistent Conversation History** | Multi-session recall: `Session -> Message` (NEXT-linked) in private raw graphs; deterministic summary on close. Restarted agents resume via 2-query flow (raw graph then commons). | `backend/app/services/store.py`, `backend/app/routes/meta.py` | **verified** | `test_private_sessions_and_deterministic_summary` passes. Kill-and-resume 2-query flow and deterministic summary generation. | `docs/screens/10-memory.png` |
| **T02-3: Shared State and Handoffs** | Atomic handoffs: `Handoff` nodes with atomic claim, lease, timeout recovery, `CONTEXT_ROOT` anchor. Exactly one winner under concurrent claims. | `backend/app/routes/handoffs.py`, `backend/app/services/mcp_tools.py` | **verified** | `python bench/run.py` Suite 2: 100 handoffs x 30 claimers = exactly 100% single-winner claims (0 collisions). | `docs/screens/07-ledger.png` |
| **T02-4: High-Concurrency Reads/Writes** | High-throughput multi-agent execution with zero orphan invariants under heavy concurrent load. | In-memory atomic store & FalkorDB query engine | **verified** | `python bench/run.py` Suite 1 on FalkorDB 8.10.2: 758 ops/sec, p50 31.5ms, p95 78.01ms, p99 100.0ms, 0 orphan violations. | `docs/screens/13-benchmark.png` |
| **T02-5: Per-Tenant Memory & Updates Over Time** | Strict tenant isolation via Redis ACL (`~tenant_<id>:*`); memory updates over time (SUPERSEDES, as-of recall, quarantine, restore). | Redis ACL config (`scripts/users.acl`), `backend/app/routes/events.py` | **verified** | `pytest tests/test_tenant_isolation.py` and `GET /tenants/denied-demo` return strict tenant isolation. | `docs/screens/12-tenants.png`, `docs/screens/08-past.png` |

---

## Track 03: Company Brain (~20% Effort)

| Focus | Description | Feature Implementation | Status | Verification Command / Proof | UI Evidence |
|---|---|---|---|---|---|
| **T03-1: Docs, Tickets & Chats in One Graph** | Unified enterprise knowledge: `Source` (DOC, TICKET, CHAT) -> `Fact` in commons graph from synthetic seed corpus (~25 items, 12 people, 4 teams, 3 projects, 3 systems). | `scripts/seed.py`, `backend/app/services/fixtures.py` | **verified** | `python scripts/seed.py` seeds docs, tickets, and Slack chats co-located in the same graph with 0 external network requests. | `docs/screens/05-contained.png` |
| **T03-2: Who Owns What, Who Knows What** | Ownership & expertise topology: `Team-[:OWNS]->Source`, `Person-[:AUTHORED\|RESOLVED]->Source-[:ABOUT]->Topic`. "Knows" = evidence count. | `backend/app/routes/brain.py`, `graph/queries.py` | **verified** | `queries.who_knows('acme', 'top_refunds')` and `GET /who-knows?topic=refund_policy` return leaderboard of topic experts ranked by authored evidence count. | `docs/screens/11-brain.png` |
| **T03-3: Tracing Decisions to Source** | Full causal audit trail: `Decision <- LEADS_TO <- Fact -> Source` returning quote, URL, version, and owner team. | `backend/app/routes/brain.py`, `backend/app/routes/graph.py` | **verified** | `queries.provenance('acme', 'dec_standard_window_30d')` and `GET /decisions/{id}/trace` return claim, quote, URL, version, and owner team in a single causal query. | `docs/screens/11-brain.png` |
| **T03-4: Keeping Knowledge Current** | Living knowledge: `SUPERSEDES` by fact key, stale propagation (`stale=true` on conclusions/decisions/actions), REVIEW handoffs to owning teams. | `backend/app/routes/scenario.py` (step `supersede`), `graph/queries.py` | **verified** | `pytest tests/test_supersede_stress.py -v` (6/6 pass) & `pytest tests/test_new_features.py::TestKeepingKnowledgeCurrent -v` (3/3 pass): old fact→SUPERSEDED, downstream Conclusions/Decisions/Actions→stale=true, automatic REVIEW handoffs. | `docs/screens/09-supersede.png` |
| **T03-5: Answers with Clear Sources** | Grounded answers: every answer carries citations, owning team, and freshness. Untraceable answers are strictly refused. | `backend/app/routes/brain.py` (`/brain/ask`), `graph/queries.py` | **verified** | `test_brain_ask_endpoint` & `tests/test_new_features.py::TestAnswersWithClearSources`: Valid query returns cited answer with owner team; ungrounded query triggers refusal response. | `docs/screens/11-brain.png` |

---

## The Three Defense Invariants (`make proof`)

| Invariant | Description | Verification Method | Result |
|---|---|---|---|
| **Quorum Gate** | Irreversible actions require root-backed or 2+ independent corroborated sources. Stale or quarantined ancestors block action staging. | `test_quorum_gate_stale_or_uncorroborated_block` | **PASSED** (blocked uncorroborated action and created REVIEW handoff) |
| **Atomic Execute Guard & Tombstones** | Single check-and-set query prevents action execution if any causal ancestor loses `HEALTHY` status. In-flight race conditions blocked by Helpdesk Tombstones. | `test_execute_guard_hammered_against_concurrent_quarantines`, `test_tombstone_race_condition_409` | **PASSED** (0/50 quarantined actions executed, 409 Conflict rejection on tombstone race) |
| **Quarantine Red Wave Cascade** | Causal invalidation wave follows `[:LEADS_TO\|SUPPORTS*]`. Quarantines all downstream nodes, increments Context Epoch, and enqueues Outbox compensations. | `python bench/run.py` Suite 4 (Poison Benchmark across depths 1-3) | **PASSED** (0.4ms containment, 0.0% Quorum leakage vs 100.0% flat vector store and Graph RAG baselines) |

---

## Visible Moments (The 90-Second Demo)

| Moment | Status | UI Evidence | Automated Verification |
|---|---|---|---|
| **Poison arrives and agents act** | **verified** | `docs/screens/03-agents-acted.png` | `POST /scenario/inject_poison` -> `POST /scenario/run_agents` |
| **Quarantine wave by hop depth, containment report** | **verified** | `docs/screens/04-wave.png`, `docs/screens/05-contained.png` | `POST /scenario/audit` -> `GET /blast/fact_poison_webhook_01/report` |
| **Prompt diff with removed lines struck through** | **verified** | `docs/screens/06-prompt-diff.png` | `GET /prompt-diff` returns clean vs quarantined diff |
| **Execute guard refusal in History** | **verified** | `docs/screens/07-ledger.png` | History entry: "Refused by atomic execute guard: ancestor quarantined" |
| **As-of slider and belief diff** | **verified** | `docs/screens/08-past.png` | `GET /graph?as_of=...` returns time-travel causal snapshot |
| **Claim race** | **verified** | `docs/screens/07-ledger.png` | `POST /handoffs/{id}/claim` (100% single-winner under 30 threads) |
| **Denied read** | **verified** | `docs/screens/12-tenants.png` | `GET /tenants/denied-demo` shows Globex tenant 403 denied on Acme commons |
| **Supersede and stale** | **verified** | `docs/screens/09-supersede.png` | `POST /scenario/supersede` flags downstream decisions stale |
| **Autopilot, one take** | **verified** | `docs/screens/14-presentation.png` | 60-second end-to-end automated replay narrative in browser console |
| **All Test Suites** | **verified** | `bench/results.json` | 39/39 passing in `backend/tests`, 2/2 in `tests/test_mock_helpdesk.py` |
