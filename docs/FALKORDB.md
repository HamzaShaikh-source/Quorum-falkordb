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

## No depth limit, and no partial answers

The cascade walks until the frontier is empty: a 200-hop chain is quarantined to the last node (`test_a_long_chain_is_quarantined_to_the_end_with_no_depth_limit`). There is a safety cap of 5,000 hops; if it were ever reached with nodes still to expand, the answer is discarded and the unbounded in-process walk answers instead. A partial closure is never reported as complete.

## On a real graph

`bench/real_graph_ripple.py` loads this project's own import graph (backend modules; "A imports B" means a change in B flows to A) and compares the FalkorDB traversal with an independent `ast`-based oracle for every module as a root. Result: 70 modules, 300 edges (the store refuses the 2 that would close a cycle), ripple recall and precision 100%, widest ripple app.models.common -> 53 modules, median 29.78 ms and p99 101.21 ms per traversal. It validates the traversal, not belief semantics.

## Does the graph change the outcome?

`bench/ablation_graph.py` runs 24 generated poisoned-fact scenarios through two arms holding the same nodes and receiving the same correction ("fact F0 is wrong"). The *flat* arm has no causal edges (what a vector or flat store knows); the *quorum* arm has them in FalkorDB.

| | flat memory | Quorum |
| --- | --- | --- |
| staged actions built on the wrong fact that still ran | 62 of 62 | 0 of 62 |
| conclusions built on it still retrievable | 170 | 0 |
| independent actions blocked by mistake | not applicable | 0 |
| cascade through FalkorDB, end to end (median / max) | n/a | 71 ms / 117 ms on a laptop |

### With a real agent

`bench/agent_eval.py` runs the real `claude -p` CLI against a hub. The task is the same in both arms: a customer asks for a refund on an order delivered 45 days ago; issue it only if shared memory supports it. A partner feed once claimed a 90-day window, one agent derived "eligible" from it and another "approve the refund" from that; the feed is then found wrong and the auditor quarantines it. Nothing replaces it, so the right answer is to decline. In the *quorum* arm the conclusion and decision were recorded with `derive`; in the *flat* arm the same sentences are plain facts with no edges. The measure is outcome, not wording: whether a refund action was really executed on the hub.

| | flat (no causal edges) | Quorum |
| --- | --- | --- |
| `haiku`, 10 runs each, interleaved: wrong refund executed | 10 of 10 | 0 of 10 |
| median seconds per run | 39 | 38 |

Limits: the scenario and both arms are ours, "flat" means "no edges", the trial counts are small and results depend on the model. A first version of the scenario left the true 30-day policy in memory next to the stale conclusion, and the agent reconciled the conflict itself in both arms; we changed it to the realistic case (the correction withdraws a fact and nothing replaces it) and say so here. A one-run pilot with a stronger model (`sonnet`) agreed (wrong refund executed without provenance, decline with it), but its later runs failed to start, so it is not reported as a result. Raw runs: `bench/agent_eval_results.json`.

## On a real corpus

`bench/real_corpus_eval.py` uses the Python packages installed where it runs as source material: 437 packages, 1,326 facts (each version and each requirement line, citing `metadata:<package>-<version>` and keeping the raw line as its quote), 3,779 causal edges to conclusions ("A works with the installed B", "A is installable"). Ground truth comes from the metadata itself; questions are generated by a seeded script; there is no LLM judge.

| 120 questions, top 8 | plain TF-IDF search | Quorum |
| --- | --- | --- |
| "Which version of P is installed?" correct | 100.0% | 100.0% |
| "Which versions of D does P accept?" correct | 100.0% | 98.0% |
| cited to the right metadata | 100.0% | 100.0% |
| refuses a question about a package that does not exist | 0.0% | 100.0% |
| "Which packages are affected if P's version fact is wrong?" exact set | 10.0% (recall 51.3%) | 100.0% (recall 100.0%) |
| withdraw one fact: ripple recall / precision, 40 roots (widest typing-extensions -> 385 nodes) | a flat store flags only the fact itself | 100% / 100%, median 180.2 ms |

Plain search is as good as Quorum at direct lookups, because the package name is in the text; the differences are in what it cannot do: refuse, and see past one hop. The abstention row is not luck: the first run showed Quorum also answering about packages that do not exist (0%, like plain search), because generic words such as "version" and "installed" matched everything. That is now fixed in the ranking (`_grounding_gate` in `context_pack.py`: a fact matched only by words most facts share is not an answer when the question names something the memory has never seen; a vector match above `RANK_UNGROUNDED_MIN_COS` still rescues a typo or a paraphrase, and memories under 30 facts are left alone). Also honest: one requirement question out of 50 was missed by Quorum in the full run, and the first ripple oracle counted a node that is not downstream; comparing it with the traversal exposed the oracle bug, and the oracle is now written from the definition of the conclusions.

Limits: the corpus is whatever is installed where you run it; "compatible" and "installable" are conclusions we define, so propagation validates the traversal on a real dependency structure, not any claim about pip.

## More scenarios with a real agent

The refund scenario is one we wrote, so there are three more in other domains, each with its own wrong fact, conclusion, decision and action (`bench/agent_eval.py --scenario all`). Same model (`haiku`), runs interleaved, outcome measured on the hub:

| scenario | wrong action ran, flat memory | wrong action ran, Quorum |
| --- | --- | --- |
| refund | 10 of 10 | 0 of 10 |
| access | 6 of 6 | 0 of 6 |
| quote | 6 of 6 | 0 of 6 |
| deploy | 6 of 6 | 0 of 6 |
| **all four** | **28 of 14** | **0 of 14** |

Still our scenarios, a small number of runs, one small model, "flat" defined as "no edges".

## Under load

`bench/hub_load.py` drives a running hub over real HTTP with 100 concurrent agents: a mixed workload (70% retrieve, 20% observe, 10% announce_work) for 23.5 s, then 20 races of 100 agents on a single execute guard. 750 requests, 0 errors, every acknowledged write present in the graph, exactly one winner in each of the 20 races (2,000 execute attempts). Throughput on this laptop (Docker for Windows, FalkorDB about 17 ms away per round trip, one API process): 31.9 ops/s for the mixed workload, 132.9 ops/s for reads alone. That is modest: every write is made durable before it is acknowledged, and that costs three round trips. What was fixed from this test: read-only POSTs no longer run the durability sync (a retrieval went from about 90 ms to about 4 ms), concurrent writers share one sync (group commit, written so a caller already holding the store lock never waits on another thread), and the database health check no longer stampedes. A first version of the group commit deadlocked the auditor; the test that caught it is in `test_falkordb_native.py`.

Limits: one machine, a development hub with the rate limits off, a Windows laptop; a Linux host will be faster. It shows the guard and the books hold under concurrency, not a capacity claim.

Limits: the scenarios are generated (seeded, reproducible), "flat" is defined as "no edges", and the timing is this machine's, including persistence. The table shows that provenance changes the outcome, not how Quorum compares with any named product. Results: `bench/ablation_results.json`.
