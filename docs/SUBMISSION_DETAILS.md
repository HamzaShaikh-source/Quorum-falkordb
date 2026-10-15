# Quorum: WeMakeDevs & FalkorDB Hackathon Submission Pack

This document contains all official submission form fields, technical disclosures, graph model documentation, and project metadata for submission to the **Graph Hacks: Context for AI Agents** Hackathon (Oct 15-18, 2026).

---

## 1. Submission Overview & Form Fields

- **Project Name:** Quorum
- **Tagline / Short Pitch:** *Git revert for agent beliefs. Quorum rolls back a poisoned fact and everything built on it, and no later retrieval can return it.*
- **Tracks Entered:**
  - **Track 02: Agent Memory and Coordination** (Primary Focus — ~80% Effort)
  - **Track 03: Company Brain** (Secondary Focus — ~20% Effort)
- **Team Information:**
  - **Team Member 1 (Person 1):** Janani — Graph Architecture, FalkorDB Cypher Queries, Redis ACLs, Benchmark Harness.
  - **Team Member 2 (Person 2):** Aaditya (`AsteroidHH`) — Backend Architecture, FastAPI App, Standalone MCP Server, 4 Autonomous Agents Swarm, Outbox Saga Worker.
  - **Team Member 3 (Person 3):** Hamza — Frontend Architecture, React/Vite/Cytoscape Incident Command Console, Marketing Landing Page, Narrative & Storyboard.
- **Repository URL:** `https://github.com/AsteroidHH/iFone.git` (Active branch: `p2-backend-agents`)
- **Demo Video Link:** `[Link to 90-Second YouTube / Loom Demonstration]` (Story script in [`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md))
- **Technical Blog Post:** [`docs/SUBMISSION_POST.md`](docs/SUBMISSION_POST.md) (Eligible for Hackathon Side Prize)

---

## 2. Problem Statement & Solution

### The Problem
When autonomous agents share memory, standard vector stores and flat Graph RAG permanently leak poisoned facts once ingested. As agents derive conclusions, execute external actions, and coordinate across handoffs, contaminated context metastasizes. In standard RAG systems, deleting the original bad vector does nothing to clean up the dozens of downstream deductions, staged side effects, and agent session prompts that now incorporate that belief.

### The Quorum Solution
Quorum stores all memory, deductions, and actions inside a **causal Directed Acyclic Graph (DAG) in FalkorDB**. Every belief traces its causal provenance back to verifiable sources. When an error is detected:
1. **Quorum Gate (Prevent):** Blocks uncorroborated, stale, or quarantined actions before they can be staged.
2. **Atomic Execute Guard (Guard):** A single Cypher check-and-set prevents racing executions if any causal ancestor loses `HEALTHY` status, protected by Helpdesk Tombstones (409 Conflict).
3. **Quarantine Red Wave (Repair):** In under **0.4 milliseconds**, FalkorDB traces downstream causal edges, quarantines derived conclusions and actions, increments the Context Epoch, purges bad beliefs from agent prompts via live Prompt Diffs, and dispatches compensating webhooks via a transactional Outbox Saga.

---

## 3. The Graph Data Model

### Node Labels
| Label | Description | Key Properties |
|---|---|---|
| `:Source` | External verified source artifact | `id`, `kind` (`'DOC' \| 'TICKET' \| 'WEBHOOK' \| 'CHAT'`), `url`, `title`, `version` |
| `:Fact:MemoryNode` | Atomic belief or assertion | `id`, `key`, `value`, `status` (`'HEALTHY' \| 'QUARANTINED' \| 'SUPERSEDED'`), `origin`, `stale` |
| `:Conclusion:MemoryNode` | Causal deduction | `id`, `text`, `status`, `stale`, `depth`, `quarantine_root` |
| `:Decision:Conclusion` | Enterprise policy decision | `owner_team`, `text`, `status`, `stale` |
| `:Action:MemoryNode` | Real-world side effect | `type`, `status`, `executed`, `confirmed`, `gate`, `reversibility`, `idempotency_key` |
| `:Compensation` | Outbox compensation task | `target_action_id`, `status` (`'PENDING_COMPENSATION' \| 'COMPENSATED'`), `attempts` |
| `:Handoff` | Multi-agent coordination anchor | `kind` (`'TASK' \| 'REVIEW' \| 'ESCALATION'`), `status`, `claimed_by`, `lease_until` |
| `:Procedure` | Procedural memory record | `task_type`, `steps[]`, `uses`, `successes`, `failures`, `rank_score` |
| `:Agent` | Authenticated agent identity | `name`, `role`, `tier` (`'root' \| 'established' \| 'new'`), `tier_weight` |
| `:Meta` | Singleton tenant state | `epoch` (monotonically incrementing counter) |

### Key Relationship Types
| Relationship | Source -> Target | Semantic Meaning |
|---|---|---|
| `[:FROM_SOURCE]` | `Fact -> Source` | Verifiable provenance grounding |
| `[:LEADS_TO]` | `MemoryNode -> MemoryNode` | Causal forward dependency in DAG |
| `[:SUPPORTS]` | `Fact -> Action` | Multi-source corroboration attached by Quorum Gate |
| `[:SUPERSEDES]` | `Fact -> Fact` | Valid temporal update of existing policy key |
| `[:CONTEXT_ROOT]` | `Handoff -> MemoryNode` | Anchors review or task to causal context |
| `[:OWNS]` | `Team -> Source` | Enterprise organizational ownership |
| `[:AUTHORED]` | `Person -> Source` | Authorship attribution for docs and tickets |
| `[:ABOUT]` | `Source -> Topic` | Semantic topic association |

---

## 4. Key Cypher Queries & Graph Algorithms

### 1. Atomic Derivation Guard (Insert-time Multi-parent Validation)
Derived conclusions and staged actions commit only if all parent IDs exist and are currently `HEALTHY`:
```cypher
MATCH (p:MemoryNode) WHERE p.id IN $parent_ids
WITH collect(p) AS parents
WHERE size(parents) = size($parent_ids) AND ALL(x IN parents WHERE x.status = 'HEALTHY')
CREATE (c:Conclusion:MemoryNode {
    id: $id,
    text: $text,
    status: 'HEALTHY',
    stale: false,
    created_at: timestamp()
})
WITH c, parents
UNWIND parents AS p
CREATE (p)-[:LEADS_TO]->(c)
RETURN c.id
```

### 2. Atomic Execute Guard (Single Transaction Check-and-Set)
Ensures action is `HEALTHY`, not executed, gate `PASSED`, and has 0 non-healthy causal ancestors across both `[:LEADS_TO|SUPPORTS*]` paths:
```cypher
MATCH (a:Action {id: $id})
WHERE a.status = 'HEALTHY' AND a.executed = false AND a.gate = 'PASSED'
OPTIONAL MATCH (x:MemoryNode)-[:LEADS_TO|SUPPORTS*]->(a)
WHERE coalesce(x.status, 'MISSING') <> 'HEALTHY'
WITH a, count(DISTINCT x) AS bad
WHERE bad = 0
SET a.executed = true, a.executed_at = timestamp(), a.confirmed = false
RETURN a.id
```

### 3. Quarantine Red Wave Cascade & Epoch Increment
Traverses downstream descendants along `[:LEADS_TO|SUPPORTS*]`, sets statuses to `QUARANTINED`, and increments `Meta.epoch` in the same transaction:
```cypher
MATCH (root:Fact {id: $root_id})-[:LEADS_TO|SUPPORTS*0..15]->(m:MemoryNode)
WITH collect(DISTINCT m) AS affected
UNWIND affected AS node
SET node.status = 'QUARANTINED',
    node.quarantined_at = timestamp(),
    node.quarantine_root = $root_id
WITH count(node) AS total_quarantined
MATCH (meta:Meta {id: 'meta'})
SET meta.epoch = meta.epoch + 1
RETURN total_quarantined, meta.epoch
```

---

## 5. Live Benchmark & Verification Results (PROOF PACK)

All performance numbers are verified and reproducible via `python bench/run.py`:

| Benchmark Suite | Measured Metric | Quorum Result | Comparison / Invariant |
|---|---|---|---|
| **30-Agent Load Test** | Throughput & Latency | **757.6 ops/sec** on FalkorDB 8.10.2 | p50: 31.5ms, p95: 78.01ms, p99: 100.0ms |
| **Orphan Invariant** | Graph Integrity | **0 invariant violations** | 0 healthy nodes under quarantined parents |
| **Handoff Claim Race** | 100 handoffs x 30 claimers | **100.0% single winner** | Exactly 100 winners, 0 collisions / double claims |
| **Atomic Execute Race** | 50 actions under audit | **0 leaks** | 0 quarantined actions allowed to execute |
| **Poison Containment** | Depths 1, 2, and 3 | **0.4ms containment** | **0.0% Quorum leakage** vs **100.0% baseline vector leakage** |

---

## 6. Honest Limits & Engineering Trade-offs

1. **Cascade Depth Bounding:** While the causal DAG in theory allows arbitrary depth, production cascades bound traversal to `0..15` hops in a single Cypher transaction to guarantee sub-millisecond execution times. Extremely deep chains fall back to level-by-level BFS iteration.
2. **Replay Mode vs Live Mode:** Replay mode is 100% deterministic, offline, and self-contained with precomputed 384-dimensional embeddings and cached agent responses. Live mode requires an active LLM API key (`QUORUM_LLM_PROVIDER`, `QUORUM_LLM_MODEL`). If no API key is provided, the backend logs the fallback and returns `mode: replay` so judges always experience a functioning demo.
3. **Multi-Tenant Boundary:** Graph namespaces (`tenant_<id>:commons`) are isolated at the Redis ACL layer (`~tenant_<id>:*`). Because Redis `GRAPH.LIST` returns all graphs in the instance, our API gateway enforces tenant prefix filtering on top of Redis ACLs to prevent tenant discovery leakage.

---

## 7. AI Tool Usage & Compliance Disclosure

- **Hackathon Rules Compliance (Rule 8):** In strict accordance with hackathon rules, scaffolding, schema designs, and contracts were defined during planning. All implementation, benchmarks, UI refinements, and bug fixes were completed within the event guidelines.
- **AI Tooling:** Antigravity and Claude Code were used for scaffolding, planning, and rapid test generation. All code was reviewed, debugged, and committed by the human team members under their personal git identities with zero automated co-author trailers.
- **Data Integrity:** 100% of the Acme Corporation scenarios, customer support tickets, refund requests, and company brain corpus are synthetic. Zero proprietary or customer PII data is used.
