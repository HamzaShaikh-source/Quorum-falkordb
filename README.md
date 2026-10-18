# 🛡️ Quorum: Git-Revert for AI Agent Beliefs on FalkorDB

[![Track 02](https://img.shields.io/badge/Track%2002-Agent%20Memory%20%26%20Coordination-blueviolet?style=for-the-badge)](https://www.wemakedevs.org/hackathons/falkordb)
[![Track 03](https://img.shields.io/badge/Track%2003-Company%20Brain-blue?style=for-the-badge)](https://www.wemakedevs.org/hackathons/falkordb)
[![Graph](https://img.shields.io/badge/Graph-FalkorDB-crimson?style=for-the-badge)](https://falkordb.com/)
[![MCP tools](https://img.shields.io/badge/MCP%20tools-28-14181a?style=for-the-badge)](docs/MCP.md)
[![Live Demo](https://img.shields.io/badge/Live%20Demo-34.100.151.44:5173-success?style=for-the-badge)](http://34.100.151.44:5173/)
[![YouTube](https://img.shields.io/badge/YouTube-Demo%20Video-red?style=for-the-badge&logo=youtube)](https://youtu.be/lrZKJ4tmo-o)

> **When an AI agent acts on a hallucination, poisoned webhook, or outdated policy, vector databases leave you stranded. Quorum brings Git-like rollback, epistemic causal tracking, and automated compensation to multi-agent systems using FalkorDB.**

---

## ⚡ Quick Links for Evaluators
- 🌐 **Live Demo Sandbox:** [http://34.100.151.44:5173/](http://34.100.151.44:5173/) *(Instant 1-Click Access — pre-authenticated as Admin)*
- 🎬 **Demo Video:** [Watch on YouTube (2 Minutes)](https://youtu.be/lrZKJ4tmo-o)
- 🔌 **Live Backend API & MCP Server:** `http://34.100.151.44:8000` (FastAPI docs at `/docs`, embedded MCP at `/mcp` with 28 MCP tools)
- 📊 **Empirical Proof & Benchmark Suite:** [`docs/PROOF.md`](docs/PROOF.md)

---

## 🗺️ 1. Epistemic Graph Schema & Architecture

FalkorDB is not a passive cache in Quorum—it is the **epistemic causal ground truth**. Agents cannot execute stateful actions without citing valid graph facts, and invalidating a root node initiates sub-millisecond topological graph cascades.

```mermaid
graph TD
    S[Source: Ingestion Webhook / Document] -->|PROV_SOURCE| E[Event: Policy Change]
    E -->|EXTRACTED_FROM| F1[Fact: Return Window = 90d]
    F0[Fact: Baseline Return = 30d] -.->|SUPERSEDES| F1
    F1 -->|SUPPORTS| B[Belief: High-Risk Policy Active]
    B -->|LEADS_TO| D[Decision: Issue Refund #4092]
    D -->|PRODUCES| A[Action: Helpdesk Call]
    A -->|EMITS| O[OutboxSaga: Refund Clawback]

    classDef invalid fill:#ff4d4f,stroke:#a8071a,stroke-width:2px,color:#fff;
    classDef valid fill:#52c41a,stroke:#237804,stroke-width:2px,color:#fff;
    classDef source fill:#1890ff,stroke:#0050b3,stroke-width:2px,color:#fff;
    class F1,B,D,A invalid;
    class F0 valid;
    class S,E,O source;
```

### Graph Primitives

| Node Label | Purpose | Key Properties |
| :--- | :--- | :--- |
| `(:Source)` | Ingestion origin & audit anchor | source_id, uri, tenant_id, verified_at |
| `(:Event)` | Temporal context or policy update | event_id, type, occurred_at |
| `(:Fact)` | Discrete atomic belief unit | fact_id, claim, validity_state (valid, quarantined, invalid), confidence |
| `(:Belief)` | Synthesized agent context | belief_id, tenant_id, strength, stale |
| `(:Decision)` | Agent reasoning trace citing facts | decision_id, agent_id, cited_fact_ids, rationale |
| `(:Action)` | Stateful external side-effect | action_id, target_system, status, idempotency_key |
| `(:OutboxSaga)` | Deterministic compensation hook | saga_id, action_id, compensation_endpoint, state |

---

## ⚙️ 2. How the Graph Does the Work (Cypher Deep-Dive)

Quorum uses pure FalkorDB Cypher traversals to execute epistemic governance in real time:

### A. Pre-Execution Epistemic Gating (Quorum Gate)
Before an agent can call an external tool (e.g. process refund, send email), the Quorum Gate inspects the causal chain of all cited facts. If any fact is quarantined or invalid, the action is blocked before execution:

```cypher
MATCH (d:Decision {id: $decision_id})-[:CITES]->(f:Fact)
WHERE f.validity_state IN ['quarantined', 'invalid'] OR f.stale = true
RETURN count(f) AS violations, collect(f.claim) AS tainted_claims
```

### B. Cascading Red-Wave Invalidation (Topological Rollback)
When an auditor quarantines a rogue fact (or when an event is superseded), Quorum traverses causal edges up to 10 hops deep in < 5ms, marking all downstream beliefs, decisions, and pending actions as invalidated:

```cypher
MATCH (f:Fact {id: $tainted_fact_id})-[:LEADS_TO|SUPPORTS*1..10]->(downstream)
SET downstream.validity_state = 'invalid',
    downstream.invalidated_at = timestamp(),
    downstream.invalidation_reason = $reason
RETURN labels(downstream) AS type, downstream.id AS id
```

### C. Outbox Saga Compensation & Retraction
For actions already executed externally, Quorum queries the downstream saga graph to trigger automated API compensations and compose Damage Control retraction notices:

```cypher
MATCH (d:Decision {validity_state: 'invalid'})-[:PRODUCES]->(a:Action)-[:EMITS]->(s:OutboxSaga)
WHERE s.state = 'pending'
RETURN s.saga_id, a.target_system, s.compensation_payload, a.idempotency_key
```

---

## 🛡️ 3. The 4-Layer Defense Engine

- **Quorum Gate:** Synchronous pre-flight epistemic policy check for every agent tool invocation.
- **Execute Guard:** Verifies execution permissions against tenant RBAC (admin, auditor, agent).
- **Outbox Sagas:** Guaranteed at-least-once compensation for external side-effects when underlying facts collapse.
- **Damage Control Room:** Human-in-the-loop review interface providing one-click customer retraction emails, audit trails, and graph time-travel.

---

## What your AI gets

When an AI agent connects to Quorum via MCP, it receives access to the full epistemic governance toolset across 28 MCP tools:

| Tool | Description |
| :--- | :--- |
| `announce_work` | Broadcasts active task intent to peer agents to prevent conflicting actions. |
| `claim_handoff` | Coordinates multi-agent handoffs with shared epistemic state across agents. |
| `delta` | Computes graph mutations and invalidated nodes between two timestamps. |
| `derive` | Derives a conclusion or belief supported by active facts. |
| `execute_action` | Executes a previously approved action with idempotency and saga registration. |
| `explain_blast_radius` | Calculates downstream impact and affected decisions for a belief. |
| `find_path` | Finds causal provenance paths between facts and actions. |
| `get_map` | Retrieves the local causal subgraph map for a given entity or root. |
| `get_node` | Inspects detailed graph properties, timestamps, and validity states of any node. |
| `observe` | Records an external system state or sensor observation. |
| `peer_retrieve` | Securely queries shared memory from an authorized peer Quorum instance. |
| `quarantine` | Quarantines a fact or belief and triggers a cascading red-wave invalidation. |
| `quorum_connect` | Establishes authorized peering connection with an external Quorum instance. |
| `quorum_connections` | Lists currently active and authorized peer hub connections. |
| `quorum_discover` | Discovers peer Quorum instances available across the local network. |
| `quorum_guide` | Interactive guide and client agreement reference for agent coordination. |
| `quorum_help` | Returns capability manifest and usage guidelines for connected agents. |
| `quorum_inbox` | Checks pending inter-hub connection requests and permission handoffs. |
| `quorum_manage` | Manages team member tokens, permission roles, and workspace seats. |
| `quorum_respond` | Approves or declines incoming inter-hub connection requests. |
| `quorum_start` | Initializes team workspace settings and onboarding configurations. |
| `quorum_status` | Reports active graph statistics, quarantine counts, and connection health. |
| `recall_as_of` | Performs time-travel belief retrieval as of a historical timestamp. |
| `remember_fact` | Saves an atomic fact with causal source provenance into FalkorDB. |
| `restore` | Restores a quarantined fact and reactivates healthy downstream descendants. |
| `retrieve_context` | Retrieves active, non-quarantined context subgraphs for safe prompt construction. |
| `review_handoff` | Inspects pending handoffs and verifies approval preconditions. |
| `stage_action` | Proposes a stateful action to the Quorum Gate along with cited fact IDs. |

---

## 🏆 4. Empirical Evaluation & Benchmark Results

Tested across a paired synthetic & real-agent benchmark against standard vector/RAG memory architectures (see [`docs/PROOF.md`](docs/PROOF.md)):

- **Preventing Unsafe Actions After Correction:** 28/28 (100%) prevented in Quorum vs. 0/28 (0%) in standard vector memory.
- **Topological Invalidation Speed:** < 4.2ms median cascade latency across 10-hop subgraphs on FalkorDB.
- **Concurrent Writer Durability:** Group-commit shared sync ensures zero lost updates under high concurrency.
- **Test Suite:** 669 backend unit & integration tests, 218 frontend tests passing (100% green).

---

## 🚀 5. Local Setup & Reproduction

### Prerequisites
- Docker & Docker Compose
- Python 3.11+
- Node.js 18+

### 1-Command Docker Launch

```bash
git clone https://github.com/HamzaShaikh-source/Quorum-falkordb.git
cd Quorum-falkordb
docker compose up -d
```

- Open console: `http://localhost:5173/`
- Backend API docs: `http://localhost:8000/docs`

---

## 👥 6. Team & Hackathon Disclosures

- **Hamza Shaikh** ([@HamzaShaikh-source](https://github.com/HamzaShaikh-source)) — Frontend & Cytoscape Console Lead
- **Aaditya Arunpal** ([@AsteroidHH](https://github.com/AsteroidHH)) — Backend, Epistemic Engine & MCP Lead
- **Janani** — FalkorDB & Cypher Graph Architecture Lead

**AI Usage Disclosure:** In compliance with Hackathon Rule 11, LLM assistance was utilized for test-case expansion, UI styling scaffolding, and drafting documentation. Core Cypher schemas, causal graph algorithms, group-commit logic, and defense gate architectures were developed by the team.
