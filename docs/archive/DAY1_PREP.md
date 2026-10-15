# Day-1 FalkorDB Verification Preparation

**Author:** Janani (Person 1 - Graph & FalkorDB)  
**Status:** Verification Plan (Prepared for execution on Oct 15, Day 1 of Hackathon)  
**Rule:** Run every single item on live FalkorDB container on Day 1. Document the exact working syntax, any errors encountered, and verified fixes.

---

## 1. Syntax & Graph Engine Verification Checklist

| Item # | Feature / Syntax to Verify | Test Cypher Statement / Command | Status | Working Syntax / Notes |
|---|---|---|---|---|
| **V-01** | Vector Index creation syntax | `CREATE VECTOR INDEX FOR (f:Fact) ON (f.embedding) OPTIONS {dimension: 384, similarityFunction: 'cosine'}` | `PENDING OCT 15` | Test exact options dictionary syntax and data type. |
| **V-02** | Vector query (`queryNodes`) & distance function | Vector search with `db.idx.vector.queryNodes` or `CALL db.idx.vector.queryNodes('Fact', 'embedding', 5, vecf32($vec))` | `PENDING OCT 15` | Verify whether cosine distance is `vecf32.distance.cosine` or `vec.cosineDistance`. |
| **V-03** | `algo.pageRank` availability | `CALL algo.pageRank('MemoryNode', 'LEADS_TO')` | `PENDING OCT 15` | Optional stretch goal. Verify if built into image or requires plugin. |
| **V-04** | `MERGE ... ON CREATE SET` | `MERGE (c:Compensation {id: 'comp_test'}) ON CREATE SET c.status = 'PENDING_COMPENSATION'` | `PENDING OCT 15` | Verify idempotency and property assignment. |
| **V-05** | `OPTIONAL MATCH ... WHERE` | `MATCH (a:Action {id: $id}) OPTIONAL MATCH (x)-[:LEADS_TO]->(a) WHERE x.status <> 'HEALTHY' RETURN a, count(x)` | `PENDING OCT 15` | Verify null handling when no matches exist. |
| **V-06** | `UNWIND` after `collect` | `MATCH (p:MemoryNode) WITH collect(p) AS parents UNWIND parents AS p CREATE (p)-[:LEADS_TO]->(c)` | `PENDING OCT 15` | Verify list unwinding inside create query. |
| **V-07** | Relationship property filters | `MATCH (a)-[r:LEADS_TO {active: true}]->(b) RETURN r` | `PENDING OCT 15` | Verify inline relationship property match. |
| **V-08** | String predicate `STARTS WITH` | `MATCH (a:Action) WHERE a.id STARTS WITH 'act_' RETURN count(a)` | `PENDING OCT 15` | Verify prefix search syntax. |
| **V-09** | Built-in `timestamp()` | `RETURN timestamp()` | `PENDING OCT 15` | Verify returns milliseconds since Unix epoch as integer. |
| **V-10** | `coalesce` in `WHERE` clause | `WHERE coalesce(x.status, 'MISSING') <> 'HEALTHY'` | `PENDING OCT 15` | Critical for execute guard on missing nodes. |
| **V-11** | Pattern predicate `NOT (()-[:LEADS_TO]->(leaf))` | `MATCH (leaf:Fact)-[:LEADS_TO*]->(a:Action) WHERE NOT (()-[:LEADS_TO]->(leaf)) RETURN leaf` | `PENDING OCT 15` | Quorum gate leaf detection syntax. |
| **V-12** | Untyped-node matches | `MATCH (m:MemoryNode)-[:LEADS_TO]->(dest) RETURN dest` | `PENDING OCT 15` | Heterogeneous target traversal. |
| **V-13** | Edge alternation `[:A|B]` and `[:A|B*]` | `MATCH (x)-[:LEADS_TO|SUPPORTS*]->(a:Action) RETURN count(x)` | `PENDING OCT 15` | Core pattern for execute guard and cascade. |
| **V-14** | `count(DISTINCT x)` aggregation | `WITH a, count(DISTINCT x) AS bad WHERE bad = 0` | `PENDING OCT 15` | Diamond DAG path duplicate handling. |

---

## 2. Infrastructure & Security Verification

| Item # | Verification Target | Procedure / Command | Status | Result / Resolution |
|---|---|---|---|---|
| **I-01** | FalkorDB Data Persistence | `docker compose down && docker compose up` with test keys/nodes | `PENDING OCT 15` | Determine real container volume path (`/data` vs `/var/lib/falkordb/data`). |
| **I-02** | Redis ACL Graph Isolation | Connect as user `tenant_globex` and attempt `GRAPH.QUERY tenant_acme:commons "MATCH (n) RETURN n"` | `PENDING OCT 15` | Must return `NOPERM` / permission denied. |
| **I-03** | `GRAPH.LIST` Leakage Check | Run `GRAPH.LIST` under `tenant_globex` ACL user | `PENDING OCT 15` | Check if other tenants' graph names are exposed. If so, apply gateway-level filtering fallback. |

---

## 3. Concurrency & Performance Verification

| Item # | Test Target | Test Methodology | Status | Notes |
|---|---|---|---|---|
| **C-01** | Atomic Execute vs Quarantine Race | Run concurrent worker threads hammering `execute_guard` while a poison cascade quarantines the root fact | `PENDING OCT 15` | Invariant: Zero actions executed with quarantined ancestors. |
| **C-02** | Query Execution PROFILE | `PROFILE MATCH (a:Action {id:$id}) ...` on synthetic graph | `PENDING OCT 15` | Verify unbounded variable-length traversal timings on diamond DAG. |
| **C-03** | Handoff Claim Race | 30 concurrent threads claiming the same `Handoff` node | `PENDING OCT 15` | Invariant: Exactly one winner, 29 failed claims. |
