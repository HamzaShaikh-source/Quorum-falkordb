# Where FalkorDB decides, and where it does not

Quorum's claim is narrow and checkable: **the graph traversals that decide what is safe run as Cypher inside FalkorDB.** This page says exactly which, shows how to verify it on a running hub, and names what still runs in the API process.

## What runs in FalkorDB

| Decision | Statement (simplified) | Code |
| --- | --- | --- |
| Quarantine cascade: which beliefs, actions and conclusions are built on a wrong fact, and how many hops away | `MATCH (a:QNode)-[:LEADS_TO\|SUPPORTS\|FROM_SOURCE]->(b:QNode) WHERE a.id IN $frontier RETURN DISTINCT b.id`, one statement per hop | `GraphStore.closure_in_falkordb`, `cascade_quarantine` |
| Status flip | `UNWIND $rows AS r MATCH (n:QNode {id: r.id}) SET n.status = 'QUARANTINED', n.depth = r.depth` | `cascade_quarantine` |
| Execute guard: is every ancestor of this action healthy? | the same traversal, walking incoming edges | `execute_guard` → `get_ancestors` |
| Blast-radius preview | the same traversal, outgoing, `LEADS_TO\|SUPPORTS` | `get_descendants` |
| Provenance between two facts | `shortestPath((a)-[:LEADS_TO\|SUPPORTS\|FROM_SOURCE*]-(b))` | `context_pack.find_path` |
| Semantic retrieval | `CALL db.idx.vector.queryNodes('QNode','embedding',$k,vecf32($q))` | `routes/observe.semantic_check` |
| Durable state | one `MERGE (:QCheckpoint)` statement plus `UNWIND … MERGE` projections of nodes and edges | `Persister.sync` |

The cascade is a level-synchronous breadth-first search driven by the database: one expansion statement per hop, so cost grows with the edges touched, not with the number of paths (a diamond-shaped graph does not blow up). Hop depth comes back with the result.

## What stays in the API process, honestly

- **The atomic check-and-set.** "Is this action still healthy, and has it not run yet?" is decided and recorded under a lock in the single API writer. FalkorDB answers the *ancestry* question; the lock makes the *decision* atomic. This is why the supported deployment is one writer per FalkorDB (see the note at the top of the README).
- **Ranking blend.** Keyword (IDF) scoring is computed in the API and blended with the vector score.
- **Fallback.** If FalkorDB errors mid-call, the traversal falls back to the same walk in process so a request does not fail. The fallback is counted and reported (below), and the hub refuses to start without a database.

## Verify it yourself

```bash
curl -H "Authorization: Bearer $QUORUM_ADMIN_TOKEN" http://localhost:8000/api/falkordb/proof
```

It returns the statements above, how many traversals each engine has answered since start-up (`traversals_answered_by: {falkordb, process}`), the engine behind the last cascade, node and edge counts read from the database, and a live comparison: the closure of the busiest fact computed in FalkorDB and in process, with `identical: true` when they agree on every id and hop depth.

The same check runs in the test suite on five random graphs with shared parents and diamonds (`backend/tests/test_falkordb_native.py`), including the fallback path.

## Does the graph change the outcome?

`bench/ablation_graph.py` runs 24 generated poisoned-fact scenarios through two arms holding the same nodes and receiving the same correction ("fact F0 is wrong"). The *flat* arm has no causal edges (what a vector or flat store knows); the *quorum* arm has them in FalkorDB.

| | flat memory | Quorum |
| --- | --- | --- |
| staged actions built on the wrong fact that still ran | 62 of 62 | 0 of 62 |
| conclusions built on it still retrievable | 170 | 0 |
| independent actions blocked by mistake | not applicable | 0 |
| cascade through FalkorDB, end to end (median / max) | n/a | 71 ms / 117 ms on a laptop |

Limits: the scenarios are generated (seeded, reproducible), "flat" is defined as "no edges", and the timing is this machine's, including persistence. The table shows that provenance changes the outcome, not how Quorum compares with any named product. Results: `bench/ablation_results.json`.
