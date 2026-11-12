# Quorum Graph Data Model & Schema Notes

**Author:** Janani (Person 1 - Graph & FalkorDB)  
**Status:** Architecture Specification (Planning phase - Pre-Oct 15)  
**Target Graph Database:** FalkorDB (`falkordb/falkordb` container)

---

## 1. Graph Topology & Multi-Tenancy

Quorum isolates data per-tenant and partitions raw conversational memory from shared causal knowledge:

1. **`tenant_<id>:commons`**  
   Shared organizational and agent causal graph. All derived knowledge, facts, conclusions, decisions, actions, and compensations live here.  
   *Invariant:* Anything that can be a causal descendant of a belief lives in `commons`, ensuring quarantine cascades never need to span across graph databases.

2. **`tenant_<id>:agent_<n>:raw`**  
   Private agent memory containing conversational sessions and raw turns.  
   *Invariant:* Raw-to-commons links are logical string references (`Fact.source_ref`), never physical edges across graph instances.

3. **Tenant Isolation via Redis ACL:**  
   Every tenant is assigned a dedicated Redis ACL user restricted by pattern `~tenant_<id>:*`.  
   The gateway resolves tenant names from signed authentication tokens, never from request bodies.

---

## 2. Commons Graph Node Labels & Schemas

Every node in the graph MUST have a non-null `status` attribute (where applicable) and consistent millisecond epoch timestamps (`timestamp()`).

### `Source`
Represents an external input artifact (policy document, webhook payload, ticket, chat log).
- `id: string` (e.g. `src_policy_refund_v1`)
- `kind: string` (`'DOC' | 'TICKET' | 'CHAT' | 'WEBHOOK'`)
- `title: string`
- `url: string`
- `version: string`
- `origin: string` (**Security Critical:** Set ONLY by the ingest gateway from the authenticated credential/channel, NEVER from payload data or agent input)
- `created_at: int` (ms)

### `Fact` (`:Fact:MemoryNode`)
Atomic knowledge statement extracted or stated by agents.
- `id: string` (e.g. `fact_refund_window_30d`)
- `key: string` (normalized identifier for corroboration and superseding, e.g. `refund.window_days`)
- `value: string` (normalized value, e.g. `30`)
- `text: string` (human-readable statement)
- `status: string` (`'HEALTHY' | 'SUPERSEDED' | 'QUARANTINED'`)
- `embedding: vecf32` (384 dimensions, `all-MiniLM-L6-v2`)
- `valid_from: int` (ms)
- `valid_to: int | null` (ms, set when superseded)
- `stale: boolean` (default `false`)
- `quarantined_at: int | null`
- `released_at: int | null`
- `quarantine_root: string | null` (ID of the root quarantined fact)
- `source_ref: string | null` (pointer to raw message ID or source ID)
- `quote: string | null` (exact snippet from source)

### `Conclusion` (`:Conclusion:MemoryNode`)
Derived belief formed by combining one or more parent `Fact` or `Conclusion` nodes.
- `id: string` (e.g. `conc_claim_eligible`)
- `text: string`
- `status: string` (`'HEALTHY' | 'SUPERSEDED' | 'QUARANTINED'`)
- `stale: boolean` (set to `true` when an ancestor fact is superseded)
- `quarantined_at: int | null`
- `released_at: int | null`
- `quarantine_root: string | null`
- `created_at: int`

### `Decision` (`:Conclusion:Decision:MemoryNode`)
A specialized conclusion representing an explicit enterprise policy or operational decision.
- Inherits all `Conclusion` properties.
- `owner_team: string` (e.g. `Customer Operations`)

### `Action` (`:Action:MemoryNode`)
Real-world side effect or staged operation.
- `id: string` (e.g. `act_refund_tx982`)
- `type: string` (`'REFUND' | 'REVERT' | 'EMAIL'`)
- `payload: string` (JSON string)
- `status: string` (`'HEALTHY' | 'QUARANTINED'`)
- `executed: boolean` (starts `false`, set to `true` by atomic execute guard)
- `executed_at: int | null`
- `confirmed: boolean` (starts `false`, set to `true` upon 200 OK from external API)
- `gate: string` (`'PASSED' | 'BLOCKED'`)
- `reversibility: string` (`'REVERSIBLE' | 'COMPENSABLE' | 'IRREVERSIBLE'`)
- `procedure_id: string | null`
- `idempotency_key: string`
- `quarantined_at: int | null`
- `released_at: int | null`
- `quarantine_root: string | null`

### `Compensation`
Outbox record for compensating real-world actions after quarantine.
- `id: string` (`comp_` + `action.id`)
- `target_action_id: string`
- `status: string` (`'PENDING_COMPENSATION' | 'DISPATCHING' | 'COMPENSATED' | 'FAILED'`)
- `attempts: int` (default `0`)
- `lease_until: int | null`
- `idempotency_key: string`

### `Handoff`
Coordination anchor between agents or human reviews.
- `id: string`
- `kind: string` (`'TASK' | 'REVIEW' | 'ESCALATION'`)
- `task: string`
- `assigned_to: string | null`
- `status: string` (`'PENDING' | 'IN_PROGRESS' | 'COMPLETED' | 'TIMED_OUT'`)
- `claimed_by: string | null`
- `claimed_at: int | null`
- `lease_until: int | null`

### `Procedure`
Procedural memory for agent tool selection and ranking.
- `id: string`
- `task_type: string`
- `steps: list[string]`
- `uses: int`
- `successes: int`
- `failures: int`

### `Agent`
Agent registry with trust tiers.
- `id: string`
- `name: string`
- `role: string`
- `tier: string` (`'root' | 'established' | 'new'`)
- `tier_weight: float` (`10.0` for root, `3.0` for established, `0.5` for new)

### `Meta`
Global metadata singleton per tenant commons graph.
- `id: string` (`'meta'`)
- `epoch: int` (monotonically incrementing context epoch counter)

### Track 03 (Company Brain) Entities
- `Team(id, name)`
- `Person(id, name, email)`
- `Topic(id, name)`

---

## 3. Raw Graph Node Labels (Private Agent Memory)

- `Session(id, created_at, summary)`
- `Message(id, text, ts, role)`

Edges:
- `(:Session)-[:CONTAINS]->(:Message)`
- `(:Message)-[:NEXT]->(:Message)`

---

## 4. Commons Graph Relationship Types

| Edge Type | Source Node | Target Node | Purpose |
|---|---|---|---|
| `ASSERTS` | `Agent` | `Fact \| Conclusion` | Provenance of statement |
| `LEADS_TO` | `MemoryNode` | `MemoryNode` | Causal DAG edge (multi-parent, acyclic) |
| `SUPPORTS` | `Fact` | `Action` | Corroborating leaf facts attached during Quorum Gate pass |
| `SUPERSEDES` | `Fact` (new) | `Fact` (old) | Valid update of existing knowledge key |
| `CONTRADICTS` | `Fact` | `Fact` | Flagged discrepancy requiring Auditor resolution |
| `CONTEXT_ROOT` | `Handoff` | `MemoryNode` | Anchors review or task handoff to causal node |
| `FROM_SOURCE` | `Fact` | `Source` | Grounding link to document or webhook |
| `OWNS` | `Team` | `Source` | Enterprise ownership mapping |
| `MEMBER_OF` | `Person` | `Team` | Team roster |
| `AUTHORED` | `Person` | `Source` | Author attribution for docs/tickets |
| `RESOLVED` | `Person` | `Source` | Ticket resolution attribution |
| `ABOUT` | `Source` | `Topic` | Semantic topic linkage |
| `OWNED_BY` | `Conclusion \| Decision` | `Team` | Governance ownership |
| `COMPENSATED_BY` | `Action` | `Compensation` | Audit trace for compensating actions |

---

## 5. Indexing Strategy (To Be Created on Day 1)

1. **Vector Index (Embedding Search):**
   ```cypher
   CREATE VECTOR INDEX FOR (f:Fact) ON (f.embedding)
   OPTIONS {dimension: 384, similarityFunction: 'cosine'}
   ```
2. **Uniqueness / Primary Key Lookups:**
   ```cypher
   CREATE INDEX FOR (f:Fact) ON (f.id)
   CREATE INDEX FOR (c:Conclusion) ON (c.id)
   CREATE INDEX FOR (a:Action) ON (a.id)
   CREATE INDEX FOR (h:Handoff) ON (h.id)
   CREATE INDEX FOR (s:Source) ON (s.id)
   ```
3. **Lookup & Filter Indexes:**
   ```cypher
   CREATE INDEX FOR (f:Fact) ON (f.key)
   CREATE INDEX FOR (f:Fact) ON (f.status)
   CREATE INDEX FOR (c:Conclusion) ON (c.status)
   CREATE INDEX FOR (a:Action) ON (a.status)
   ```

---

## 6. Core Cypher Patterns (Day 1 Verification Candidates)

### 6.1 Atomic Derivation Guard (Insert-time Multi-parent Validation)
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

### 6.2 Atomic Execute Guard
Ensures action is `HEALTHY`, not yet executed, gate `PASSED`, and has zero non-healthy ancestors over both causal (`LEADS_TO`) and corroborating (`SUPPORTS`) paths:
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

### 6.3 Quarantine Cascade & Epoch Increment
Traverses downstream descendants over `[:LEADS_TO|SUPPORTS*]`, sets statuses to `QUARANTINED`, and bumps tenant epoch in the same transaction:
```cypher
MATCH (root:Fact {id: $root_id})
MATCH (root)-[:LEADS_TO|SUPPORTS*0..15]->(m:MemoryNode)
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
