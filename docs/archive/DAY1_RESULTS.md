# Day-1 FalkorDB Verification Results

**Author:** Janani (Person 1 - Graph & FalkorDB)  
**Status:** VERIFIED ON LIVE CONTAINER (`falkordb/falkordb:latest`)  
**Test Suite:** `scripts/test_day1.py`, `scripts/test_acl.py`, `scripts/test_persistence.py`

This document records the exact working syntax, test results, and verified fixes for every item in Brief Section 10. The entire team can now build upon these confirmed Cypher patterns.

---

## 1. Cypher Syntax & Engine Results

### V-01: Vector Index Creation
- **Status:** PASSED
- **Working Syntax:**
  ```cypher
  CREATE VECTOR INDEX FOR (f:Fact) ON (f.embedding)
  OPTIONS {dimension: 384, similarityFunction: 'cosine'}
  ```
- **Notes:** Dimensions must match embedding model (`all-MiniLM-L6-v2` = 384 dims). Similarity function `'cosine'` is supported natively.

### V-02: Vector Query & Distance Function
- **Status:** PASSED
- **Working Query Syntax:**
  ```cypher
  CALL db.idx.vector.queryNodes('Fact', 'embedding', 5, vecf32($vec))
  YIELD node, score
  RETURN node.id, score
  ```
- **Confirmed Distance Function Name:** **`vec.cosineDistance`**
  *(Note: The unconfirmed guess `vecf32.distance.cosine` in the original spec is replaced by the verified `vec.cosineDistance`)*.
- **Example Usage:**
  ```cypher
  RETURN vec.cosineDistance(vecf32($v1), vecf32($v2)) AS distance
  ```

### V-03: `algo.pageRank`
- **Status:** Procedure exists; requires graph projection parameters.
- **Notes:** Marked as optional stretch per Brief Section 5.1. Core trust hierarchy relies on root > corroborated > tier.

### V-04: `MERGE ... ON CREATE SET`
- **Status:** PASSED
- **Working Syntax:**
  ```cypher
  MERGE (c:Compensation {id: $comp_id})
  ON CREATE SET c.status = 'PENDING_COMPENSATION', c.attempts = 0
  RETURN c.id, c.status
  ```

### V-05: `OPTIONAL MATCH ... WHERE`
- **Status:** PASSED
- **Working Syntax:**
  ```cypher
  MATCH (a:Action {id: $id})
  OPTIONAL MATCH (x:MemoryNode)-[:LEADS_TO*]->(a)
  WHERE coalesce(x.status, 'MISSING') <> 'HEALTHY'
  RETURN a.id, count(DISTINCT x) AS bad
  ```

### V-06: `UNWIND` after `collect` (Atomic Derivation Guard)
- **Status:** PASSED
- **Working Syntax:**
  ```cypher
  MATCH (p:MemoryNode) WHERE p.id IN $parent_ids
  WITH collect(p) AS parents
  WHERE size(parents) = size($parent_ids) AND ALL(x IN parents WHERE x.status = 'HEALTHY')
  CREATE (c:Conclusion:MemoryNode {
      id: $id, text: $text, status: 'HEALTHY', stale: false, created_at: timestamp()
  })
  WITH c, parents
  UNWIND parents AS p
  CREATE (p)-[:LEADS_TO]->(c)
  RETURN c.id
  ```

### V-07: Relationship Property Filters
- **Status:** PASSED
- **Working Syntax:**
  ```cypher
  MATCH (a)-[r:LEADS_TO {weight: 1.5}]->(b)
  RETURN r.weight
  ```

### V-08: String Predicate `STARTS WITH`
- **Status:** PASSED
- **Working Syntax:**
  ```cypher
  MATCH (a:Action) WHERE a.id STARTS WITH 'act_' RETURN count(a)
  ```

### V-09: Built-in `timestamp()`
- **Status:** PASSED
- **Return Type:** Integer (milliseconds since Unix epoch, e.g. `1791786743817`).

### V-10: `coalesce` in `WHERE` Clause
- **Status:** PASSED
- **Working Syntax:**
  ```cypher
  WHERE coalesce(x.status, 'MISSING') <> 'HEALTHY'
  ```

### V-11: Pattern Predicate `NOT (()-[:LEADS_TO]->(leaf))`
- **Status:** PASSED
- **Working Syntax:**
  ```cypher
  MATCH (leaf:Fact)-[:LEADS_TO*]->(a:Action {id: $id})
  WHERE NOT (()-[:LEADS_TO]->(leaf))
  RETURN DISTINCT leaf
  ```

### V-13: Relationship Alternation `[:A|B]` and `[:A|B*]`
- **Status:** PASSED
- **Working Syntax:**
  ```cypher
  MATCH (x)-[:LEADS_TO|SUPPORTS*]->(a:Action {id: $id})
  RETURN count(DISTINCT x)
  ```
- **Notes:** Both single-hop and variable-length alternations work seamlessly in FalkorDB.

### V-14: `count(DISTINCT x)` on Diamond DAG
- **Status:** PASSED
- **Result:** Given Diamond DAG `Root -> (B, C) -> D`, total paths returned = 4, but `count(DISTINCT x)` correctly returns 3. Prevents double-counting bad ancestors in execute guards.

---

## 2. Infrastructure & Security Results

### I-01: Container Data Persistence
- **Status:** PASSED
- **Verification:** Created test node in `persist_test` graph, executed `SAVE`, ran `docker restart falkordb`, re-queried and confirmed node was preserved.
- **Container Path Details:**
  - Real container path: `/var/lib/falkordb/data`
  - `/data` is a symlink pointing to `/var/lib/falkordb/data`
  - Docker volume mount: `falkordb_data:/var/lib/falkordb/data` or `falkordb_data:/data` are both valid.

### I-02: Redis ACL Tenant Isolation
- **Status:** PASSED
- **Configuration:**
  ```
  user tenant_acme on >acme_pass ~tenant_acme:* &* +@all
  user tenant_globex on >globex_pass ~tenant_globex:* &* +@all
  ```
- **Result:** When `tenant_globex` attempts to query `tenant_acme:commons`, FalkorDB throws:
  `NoPermissionError: No permissions to access a key`

### I-03: `GRAPH.LIST` Multi-Tenant Leakage Check
- **Status:** LEAKAGE CONFIRMED -> GATEWAY FALLBACK IMPLEMENTED
- **Finding:** Running `GRAPH.LIST` under user `tenant_globex` returns all graph names across the entire instance (`['tenant_globex:commons', 'tenant_acme:commons', ...]`).
- **Mitigation:** The application gateway MUST filter `GRAPH.LIST` responses using prefix `name.startswith(f"tenant_{tenant_id}:")` before returning results to consumers.

---

## 3. Notable Edge Cases Discovered & Resolved

1. **`g.delete()` on Non-Existent Graph:**
   - Raises `redis.exceptions.ResponseError: Invalid graph operation on empty key`.
   - **Resolution:** Wrap initial deletions in `try/except ResponseError` or query existence first.
