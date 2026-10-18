# Hackathon tracks: where each focus is implemented and verified

Quorum is architected to address all three hackathon tracks natively:

## Track 01: Agents That Act on Connected Data
| Focus | Description | Implementation | Where to Verify |
|---|---|---|---|
| **T01-1: Multi-Hop Causal Reasoning** | Deep DAG traversal over `[:LEADS_TO\|SUPPORTS*]` to trace roots, leaf facts, and dependent conclusions. | `backend/app/graph/queries.py` (`get_leaf_facts`, `explain_blast_radius`) | `pytest backend/tests/test_auditor_qa.py` & [`PROOF.md`](PROOF.md) |
| **T01-2: Graph Queries as Agent Tools** | Native Cypher algorithms and graph queries exposed directly to agents (`find_path`, `get_node`, `delta`). | `backend/app/services/mcp_tools.py`, `backend/app/services/context_pack.py` | `pytest backend/tests/test_mcp_hardening.py` & [`MCP.md`](MCP.md) |
| **T01-3: Tool Selection Through MCP** | 28-tool Model Context Protocol server (15 memory and auditor tools, 2 coordination tools, 11 chat tools) over Streamable HTTP (`/mcp`) & stdio for Claude, Cursor, and Antigravity. | `backend/app/mcp_embed.py`, `mcp/server.py`, `mcp/bridge.py` | `GET /mcp` handshake & Connect tab in Console |
| **T01-4: Actions Based on Graph Evidence** | Quorum Gate (Layer 1) & Atomic Execute Guard (Layer 2) enforce graph-backed policy verification before execution. | `backend/app/services/gate.py`, `backend/app/services/executor.py` | `pytest backend/tests/test_safety_engine.py` & `screens/07-ledger.png` |
| **T01-5: Explainable Agent Decisions** | Live Cytoscape causal DAG, prompt diff strikethrough, parameterised Cypher drawer, and Plain-English Activity feed. | `frontend/src/graph/GraphCanvas.tsx`, `frontend/src/views/Console.tsx` | Interactive Console (`#/app`) & `screens/04-wave.png` |

## Track 02: Agent Memory and Coordination (~50% Effort)
| Focus | Description | Implementation | Where to Verify |
|---|---|---|---|
| **T02-1: Episodic, Semantic & Procedural Memory** | Multi-type memory: Messages, Facts, Conclusions, and Laplace-smoothed procedure ranking (`(successes+1)/(uses+2)`). | `backend/app/routes/procedures.py`, `graph/queries.py` | `pytest backend/tests/test_safety_engine.py` & `screens/10-memory.png` |
| **T02-2: Persistent Conversation History** | Private raw graphs per agent (`tenant_<id>:agent_<n>:raw`), kill-and-resume 2-query flow, deterministic session summaries. | `backend/app/graph/queries.py`, `backend/app/routes/meta.py` | `pytest backend/tests/test_api_contract.py` & `screens/10-memory.png` |
| **T02-3: Shared State & Handoffs** | Atomic handoffs with lease timeouts, role-based claims (`TASK` for agents, `REVIEW` for humans). Exactly one winner. | `backend/app/routes/handoffs.py`, `graph/queries.py` | `python bench/run.py` (Suite 2: 100/100 claims) & `screens/07-ledger.png` |
| **T02-4: High Concurrency** | 30 concurrent agent threads with zero orphan invariants under heavy read/write load. | FalkorDB query pool, atomic store | `python bench/run.py` (Suite 1: 1437.1 ops/sec on FalkorDB) & `screens/13-benchmark.png` |
| **T02-5: Per-Tenant Memory Isolation** | Per-tenant Redis ACL users (`~tenant_<id>:*`); forbidden cross-tenant reads return 403. | `scripts/users.acl`, `tests/test_tenant_isolation.py` | `GET /tenants/denied-demo` & `screens/12-tenants.png` |

## Track 03: Company Brain (~20% Effort)
| Focus | Description | Implementation | Where to Verify |
|---|---|---|---|
| **T03-1: Docs, Tickets & Chats in One Graph** | Unified enterprise knowledge: `Source` (DOC, TICKET, CHAT) -> `Fact` mapped in commons graph. | `scripts/seed.py`, `backend/app/services/fixtures.py` | `python scripts/seed.py` & `screens/05-contained.png` |
| **T03-2: Who Owns What, Who Knows What** | Ownership & expertise topology: `Team-[:OWNS]->Source`, `Person-[:AUTHORED\|RESOLVED]->Source-[:ABOUT]->Topic`. | `backend/app/routes/brain.py`, `graph/queries.py` | `GET /who-knows?topic=refund_policy` & `screens/11-brain.png` |
| **T03-3: Tracing Decisions to Source** | Full causal audit trail: `Decision <- LEADS_TO <- Fact -> Source` returning quotes, URLs, and owner teams. | `backend/app/routes/brain.py` | `GET /decisions/{id}/trace` & `screens/11-brain.png` |
| **T03-4: Keeping Knowledge Current** | Living knowledge: `SUPERSEDES` by key, stale propagation (`stale=true`), and review handoffs to owning teams. | `backend/app/routes/scenario.py` | `POST /scenario/supersede` & `screens/09-supersede.png` |
| **T03-5: Answers with Clear Sources** | Grounded answers: every answer carries citations and freshness. Untraceable queries are strictly refused. | `backend/app/routes/brain.py` (`/brain/ask`) | `POST /brain/ask` & `screens/11-brain.png` |

The one-page judging view is [`HACKATHON.md`](HACKATHON.md); the system overview is [`ARCHITECTURE.md`](ARCHITECTURE.md).
