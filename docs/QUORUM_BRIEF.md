# Quorum: Build Brief (v3, consolidated)

Read this whole brief before writing anything. If it conflicts with `docs/QUORUM_SPEC.md`, THIS BRIEF WINS. Flag anything ambiguous instead of guessing. Every Cypher snippet below is an UNVERIFIED sketch until it has been run (see Section 10).

---

## 0. Timing and team decision (important)

Event: **Graph Hacks: Context for AI Agents** (WeMakeDevs x FalkorDB), Oct 15-18, 2026 (12:01 AM IST Oct 15 to 11:59 PM IST Oct 18), online.
Page: https://www.wemakedevs.org/hackathons/falkordb (rules at `/rules`).

- **Team decision: we start building now, before the event window opens.** Note for everyone: rule 8 on the Rules page says the main coding and design work must begin after the hackathon starts (Oct 15, 12:01 AM IST), and the rules allow disqualification for breaking them. Each member should know this and accept it before committing.
- Re-read the Rules tab before submitting; do not rely on rule numbers quoted in this brief.
- The hard deadline is Oct 18, 11:59 PM IST; target submitting by about 8 PM IST.
- Submission must include: public repo; README with setup instructions and the graph data model; the Cypher queries / graph algorithms the product depends on; demo video; live deployment (preferred) or complete local setup instructions; the track(s) entered. AI-assistant use must be disclosed. Participants must be able to explain the graph model, queries, architecture and decisions.
- Data must be public, synthetic, or our own. We use synthetic only.

---

## 1. What we are building

**Pitch:** Git revert for agent beliefs. Quorum rolls back a bad fact and everything built on it, and no later retrieval can return it.

**Problem:** agents that share memory also share its errors. One false belief becomes conclusions and real actions (refunds, edits, emails), and deleting the fact does not undo them.

**Idea:** the team's memory is a causal graph in FalkorDB. Every fact is linked to the conclusions and actions derived from it. Three layers of defense:

1. **PREVENT (quorum gate):** irreversible actions that rest on uncorroborated facts are blocked into a human REVIEW handoff.
2. **GUARD (atomic execute guard):** an action cannot run if any ancestor or supporting fact is no longer healthy.
3. **REPAIR (cascade):** when a bad belief is found, one cascade marks everything downstream QUARANTINED, hides it from retrieval, queues compensating actions for real-world effects, and notifies owners. Quarantine can be restored. Any past belief state can be queried as of a time.

**Scenario (all data synthetic):** fictional "Acme Support". The policy doc says refunds are allowed within 30 days. A poisoned webhook claims 90 days. Agents act on it before the Auditor catches the contradiction.

**Honest limits (must appear in README):**
- Compensation is not undo. Irreversible actions are only flagged and handed to a human.
- An agent whose context window already holds a fact keeps it until it rebuilds (see context epoch).
- Claim is "no later retrieval returns quarantined content, and agents refresh stale windows". It is NOT "it never reached the model": in the demo, Refunder executes 2 refunds before detection.
- As-of queries see only the latest quarantine window per node.
- Seeded procedure scores are seeded; say so.
- Benchmark numbers are whatever the run prints.

---

## 2. Tracks, effort and key focuses (acceptance checklist)

Submit under **Track 02 (~80% of effort)** and **Track 03 (~20%)** ONLY. Do not build or claim Track 01 work. Tracks have separate prizes and no published weighting; the 80/20 split is our own planning split.

The graph must shape what agents do, not sit on the side: follow connections, remember across sessions, share state between agents, connect people/projects/decisions, show the path behind an answer. If FalkorDB is removed the product must stop working.

Put both tables at the top of the README: each focus mapped to a feature and a "where to verify" pointer. Keep `docs/FOCUS_CHECKLIST.md` with each focus, status (not started / built / verified) and the command or demo step that proves it. Never mark one done until it has been run.

**Track 02: Agent Memory and Coordination**
1. **Episodic / semantic / procedural memory.** Episodic = `Message` nodes in raw graphs plus `Fact.source_ref` and `Fact.quote`. Semantic = `Fact` / `Conclusion`. Procedural = `Procedure` with an ordered `steps` array and `uses/successes/failures`. Demo shows procedure selection changing between two runs.
2. **Persistent conversation history.** `Session -> Message` (NEXT-linked) in private raw graphs, a session summary written at session end. Restarted agents resume via a two-query flow (raw graph, then commons via `source_ref`) and state what they remember and where each belief came from. Decide and document what writes `Session.summary` (LLM call with cached fallback).
3. **Shared state and handoffs.** `Handoff` nodes with atomic claim, lease, timeout recovery, `CONTEXT_ROOT` anchor. Exactly one winner under concurrent claims. Two agents visibly race for a claim in the demo.
4. **High-concurrency reads/writes.** 30-agent harness with real throughput, p50/p95/p99 and the zero-orphans invariant printed in the README.
5. **Per-tenant memory.** One graph namespace per tenant, enforced by Redis ACL, live denied-read demo, `tests/test_tenant_isolation.py`.
- Also: memory updates over time (SUPERSEDES, as-of, quarantine, restore).

**Track 03: Company Brain**
1. **Docs, tickets, chats in one graph.** `Source` (DOC/TICKET/CHAT) -> `Fact` in the same commons graph, from a seeded synthetic corpus (~25 items, 12 people, 4 teams, 3 projects, 3 systems).
2. **Who owns what, who knows what.** `Team-[:OWNS]->Source`, `Person-[:AUTHORED|RESOLVED]->Source-[:ABOUT]->Topic`. "Knows" = evidence count.
3. **Tracing decisions to source.** One query `Decision <- LEADS_TO <- Fact -> Source` returning quote, url, version, owner.
4. **Keeping knowledge current.** SUPERSEDES by fact key, stale propagation (flagged stale, not quarantined), REVIEW handoffs to owning teams.
5. **Answers with clear sources.** Every answer carries citations, owner team and freshness. Untraceable answers are refused.
- If the Brain slice is cut, we withdraw from Track 03.

---

## 3. Stack

FalkorDB (image `falkordb/falkordb`), Python 3.11 + FastAPI, official FalkorDB Python client, an MCP server (Python), Cytoscape.js frontend, Docker Compose, local embeddings (all-MiniLM-L6-v2, 384 dims) with precomputed vectors in the seed so `docker compose up` needs no network.

---

## 4. Architecture and schema

**Graphs:**
- `tenant_<id>:commons`: all derived knowledge.
- `tenant_<id>:agent_<n>:raw`: private `Session` / `Message`.
- Anything that can be a causal descendant of a belief lives in commons, so quarantine never spans graphs. Raw-to-commons links are string pointers (`source_ref`), never physical edges.

**Tenant isolation:** per-tenant Redis ACL user limited to `~tenant_<id>:*`. The gateway resolves the graph name from the authenticated token, never from request input. Fallback if ACL patterns misbehave: gateway-level key-prefix check.

**Commons labels:** `Agent`, `Source`, `Fact`, `Conclusion` (+ `Decision` label with `owner_team`), `Action`, `Compensation`, `Handoff`, `Procedure`, plus a `Meta` node holding the tenant `epoch`. Brain slice adds `Team`, `Person`, `Topic`. All reasoning nodes also carry `:MemoryNode`.
**Raw labels:** `Session(id, created_at, summary)`, `Message(id, text, ts)`; edges `CONTAINS`, `NEXT`.

**Folded into properties (state the costs in README):**
- `Event` -> `Fact.source_ref` and `Fact.quote`.
- `QuarantineEvent` -> `quarantined_at` / `released_at` on nodes (as-of queries see only the LATEST window).
- `Alert` -> `Handoff` kind `ESCALATION`.
- `Step` -> ordered `steps` array on `Procedure`, with `procedure_id` on each `Action` for scoring.

**Key properties (every node must have a non-null `status`):**
- **Source:** `id, kind, title, url, version, origin`. `origin` is set ONLY by the ingest gateway from the authenticated channel or credential, never from a payload or an agent.
- **Fact:** `id, key, value` (normalized, for corroboration), `text, status` (HEALTHY|SUPERSEDED|QUARANTINED), `embedding, valid_from, valid_to, stale, quarantined_at, released_at, quarantine_root, source_ref, quote`.
- **Conclusion:** `id, text, status, stale`, quarantine fields.
- **Action:** `id, type, payload, status` (HEALTHY|QUARANTINED), `executed, executed_at, confirmed, gate` (PASSED|BLOCKED), `reversibility` (REVERSIBLE|COMPENSABLE|IRREVERSIBLE), `procedure_id, idempotency_key`, quarantine fields. Staged = `HEALTHY` and `executed=false`.
- **Compensation:** `id, target_action_id, status` (PENDING_COMPENSATION|DISPATCHING|COMPENSATED|FAILED), `attempts, lease_until, idempotency_key`.
- **Handoff:** `id, kind` (TASK|REVIEW|ESCALATION), `task, assigned_to, status` (PENDING|IN_PROGRESS|COMPLETED|TIMED_OUT), `claimed_by, claimed_at, lease_until`.
- **Agent:** `id, name, role, tier` (root|established|new), `tier_weight` (10|3|0.5).
- **Procedure:** `id, task_type, steps[], uses, successes, failures`.

**Edges:** `ASSERTS, SUPERSEDES, CONTRADICTS, LEADS_TO` (causal DAG, multi-parent, enforced at insert: a derived node may only reference parents that already exist), `SUPPORTS` (corroborating Fact -> Action, created when the gate passes), `CONTEXT_ROOT, FROM_SOURCE, OWNS, MEMBER_OF, AUTHORED|RESOLVED, ABOUT, OWNED_BY, COMPENSATED_BY`.

---

## 5. Core rules and queries (these override the spec)

### 5.1 Trust and contradictions
- Order: **root > corroborated > tier**.
- A claim contradicting a root-tier fact quarantines the CHALLENGER. A root fact is never auto-quarantined.
- Otherwise compare (root-backed, corroboration count, tier weight with the combined new-tier contribution capped at one established weight). A tie creates a REVIEW Handoff.
- Peers cannot vouch for each other. PageRank trust is an optional stretch, only if the Day-1 check passes.
- **Supersede authority:** a Fact may supersede another only if asserted by a root-tier agent, or it is a newer version from the same `Source.origin` as the old fact. Anything else goes through the contradiction path. (Otherwise a webhook could launder a poisoned fact via "supersede".)

### 5.2 Quorum gate (application code, at `stage_action`)
- REVERSIBLE / COMPENSABLE actions: `gate=PASSED`.
- IRREVERSIBLE action passes only if EVERY leaf fact feeding it is either (a) asserted by a root-tier agent, or (b) corroborated: another HEALTHY Fact with the same `key` and `value` from a Source with a DIFFERENT `origin`, at least one of the two asserted by an established-or-higher agent.
- Otherwise `gate=BLOCKED` and a REVIEW Handoff is created, anchored to the action.
- When the gate passes via corroboration, create `(corroborator)-[:SUPPORTS]->(action)` for each corroborating fact, so later quarantine of a corroborator blocks the action.
- Completing the REVIEW handoff sets `gate=PASSED`. REVIEW and ESCALATION claim/complete require a human or Auditor token. Agent tokens are rejected (an agent must not approve its own blocked action).
- Leaf set sketch:
```cypher
MATCH (leaf:Fact)-[:LEADS_TO*]->(a:Action {id:$id})
WHERE NOT (()-[:LEADS_TO]->(leaf))
RETURN DISTINCT leaf
```

### 5.3 Atomic derivation guard (Conclusions AND Actions)
A derived node (Conclusion, or an Action at `stage_action`) commits only if all parents currently exist and are HEALTHY:
```cypher
MATCH (p:MemoryNode) WHERE p.id IN $parent_ids
WITH collect(p) AS parents
WHERE size(parents) = size($parent_ids) AND ALL(x IN parents WHERE x.status = 'HEALTHY')
CREATE (c:Conclusion:MemoryNode {id:$id, text:$text, status:'HEALTHY', stale:false, created_at:timestamp()})
WITH c, parents
UNWIND parents AS p
CREATE (p)-[:LEADS_TO]->(c)
RETURN c.id
```

### 5.4 Atomic execute guard (single statement; the only way an action may run)
```cypher
MATCH (a:Action {id:$id})
WHERE a.status='HEALTHY' AND a.executed=false AND a.gate='PASSED'
OPTIONAL MATCH (x:MemoryNode)-[:LEADS_TO|SUPPORTS*]->(a)
WHERE coalesce(x.status,'MISSING') <> 'HEALTHY'
WITH a, count(DISTINCT x) AS bad
WHERE bad = 0
SET a.executed = true, a.executed_at = timestamp(), a.confirmed = false
RETURN a.id
```
- Zero rows = do not call the external API. After a 200 from the external API set `confirmed=true`.
- A SUPERSEDED ancestor also blocks execution (intended).
- `count(DISTINCT x)` because diamond-shaped DAGs duplicate paths.
- Unbounded `*` terminates on a DAG. PROFILE it on seeded data; keep a level-by-level fallback for the cascade if too slow. Verify `[:A|B*]` alternation with variable length on Day 1.

### 5.5 Quarantine cascade
Given a root fact to quarantine, in one pass (partition actions BEFORE mutating statuses):
1. Collect descendants over `LEADS_TO|SUPPORTS*`.
2. Set all descendant Facts, Conclusions, Actions to `status='QUARANTINED'`, `quarantined_at=timestamp()`, `quarantine_root=$root_id`. Staged actions simply become QUARANTINED (never run).
3. Increment `Meta.epoch` in the same pass.

**Compensation creation is a SEPARATE idempotent statement afterwards** (`MERGE` on `'comp_'+action.id`), filtered on `executed=true`, never on `status`:
- REVERSIBLE -> revert request.
- COMPENSABLE -> reverse request.
- IRREVERSIBLE -> ESCALATION Handoff plus a drafted correction notice for a human.

**Compensation worker:** claims `PENDING_COMPENSATION` rows with a lease (`DISPATCHING`, `lease_until`), calls the external API with the idempotency key, sets `COMPENSATED` on 200, otherwise `attempts++` with backoff, `FAILED` after N attempts plus an ESCALATION Handoff.
**In-flight race:** an action with `executed=true, confirmed=false` older than the in-flight window (60 s) is treated as unknown-outcome and compensated anyway. The mock helpdesk records a TOMBSTONE for any compensation whose idempotency key it has not seen, and rejects a later execute with that key. (A plain "unknown id = no-op 200" would let the original call land after compensation.)

### 5.6 Supersede (legitimate update)
```cypher
MATCH (old:Fact {id:$old_id}) WHERE old.status='HEALTHY' AND old.valid_to IS NULL
SET old.valid_to=timestamp(), old.status='SUPERSEDED'
CREATE (new:Fact:MemoryNode {id:$new_id, key:old.key, value:$value, text:$text, status:'HEALTHY',
        embedding:vecf32($embedding), valid_from:timestamp()})
CREATE (new)-[:SUPERSEDES]->(old)
RETURN new.id
```
Then: increment `Meta.epoch`; stale propagation (all Conclusion/Decision descendants of the old fact get `stale=true`, status stays HEALTHY); create a REVIEW Handoff to each owning team (via `OWNED_BY` / `owner_team`).

### 5.7 Restore
Restore root first, repeat until no change. A node is restored only if ALL its parents (LEADS_TO and SUPPORTS) are HEALTHY (`quarantine_root` holds only one root, so check parents, not just the stored root). Clear `quarantined_at`, set `released_at`. NEVER restore executed actions. Increment `Meta.epoch`. Include a diamond-shaped test case.

### 5.8 Handoff claim (kind-aware, atomic)
```cypher
MATCH (h:Handoff {id:$id})
OPTIONAL MATCH (h)-[:CONTEXT_ROOT]->(anchor:MemoryNode)
WITH h, anchor
WHERE (h.status='PENDING' OR (h.status='IN_PROGRESS' AND h.lease_until < timestamp()))
  AND (h.kind <> 'TASK' OR anchor.status='HEALTHY')
SET h.status='IN_PROGRESS', h.claimed_by=$agent_id, h.claimed_at=timestamp(), h.lease_until=timestamp()+60000
RETURN h
```
REVIEW/ESCALATION kinds additionally require a human or Auditor token (see 5.2).

### 5.9 Retrieval (healthy-only)
- Re-rank with an explicit vector distance function, not the raw score. The original spec writes `vecf32.distance.cosine`; the real name may be `vec.cosineDistance`. UNCONFIRMED, test on Day 1.
- Return only HEALTHY, unsuperseded context (`status='HEALTHY' AND valid_to IS NULL`). Expand up to 3 hops over healthy `LEADS_TO`. Retry once with a larger k, then report "no healthy context".

### 5.10 Context epoch
`Meta.epoch` increments on every quarantine, restore AND supersede. Every MCP tool response includes the current epoch. An agent that sees a changed epoch must rebuild its context through `retrieve_context` before acting.

### 5.11 As-of recall (latest quarantine window only)
```cypher
MATCH (f:Fact)
WHERE f.valid_from <= $t AND (f.valid_to IS NULL OR f.valid_to > $t)
  AND NOT (f.quarantined_at IS NOT NULL AND f.quarantined_at <= $t
           AND (f.released_at IS NULL OR f.released_at > $t))
RETURN f.id, f.text
```

### 5.12 Company Brain queries
Decision provenance:
```cypher
MATCH (dec:Decision {id:$decision_id})<-[:LEADS_TO*1..5]-(f:Fact)-[:FROM_SOURCE]->(s:Source)
OPTIONAL MATCH (team:Team)-[:OWNS]->(s)
RETURN f.text AS claim, f.quote AS quote, s.title AS source, s.url AS url,
       s.version AS version, team.name AS owner
```
Who knows about topic X: rank `Person` by count of `AUTHORED|RESOLVED` Sources `ABOUT` the topic. Cited answers: refuse any answer that cannot be traced to at least one Source.

---

## 6. MCP server and agents

REST and SSE shapes for the UI live in `docs/CONTRACT.md`, which is authoritative once frozen.

- MCP tools for agents: `remember_fact`, `derive`, `retrieve_context`, `stage_action`, `execute_action`, `claim_handoff` (TASK kind only for agent tokens).
- Auditor/human-only tools: `quarantine`, `restore`, `explain_blast_radius`, REVIEW/ESCALATION claim and complete.
- Every tool enforces the tenant from the authenticated token. Every response includes the epoch.
- Four small LLM agents (Scout, Analyst, Refunder, Comms) run on the MCP server. The **Auditor is a deterministic service** (contradiction check plus the trust rule), not an LLM.
- Provide a seeded REPLAY MODE with cached LLM outputs so the demo is deterministic, and a live mode for judges. Any outside MCP client should be able to plug in.

---

## 7. Benchmark and tests (one runner: `bench/run.py`; print real numbers, never invent them)

**Poison benchmark:** inject poisoned facts at about three causal depths (fixed seeds, N trials each).
- Metrics: leaked-context rate, wrongly quarantined healthy nodes, time to containment.
- Measure leakage at the RETRIEVAL layer: the set of node IDs returned that descend from the poison. No LLM calls in the benchmark (LLMs only in the demo), so replay caches never mismatch across baselines.
- Baselines: (a) flat vector store with TTL deletion; (b) delete only the poisoned fact, no cascade.

**Concurrency and invariants (same runner):**
- 30 agents, 70/30 read/write, p50/p95/p99.
- 100 handoffs x 30 claimers: exactly one winner each.
- Invalidation under load: zero HEALTHY children under QUARANTINED parents.
- `execute_action` hammered against concurrent quarantines: no executed action has a non-healthy ancestor or supporter.
- Compensation replay proves idempotency; in-flight race test proves the tombstone works.
- DAG no-cycle check; restore (incl. diamond case); supersede key copy.
- ACL denial, including that `GRAPH.LIST` does not reveal another tenant's graph names.
- Gate blocks an irreversible action backed by one uncorroborated leaf even when another leaf is root-backed.
- Gate re-check: quarantining a corroborating fact blocks the action via SUPPORTS.
- Agent token cannot complete a REVIEW handoff.

---

## 8. Demo (about 90 seconds, Cytoscape UI, causal graph growing live)

1. **Poison:** webhook injects "refund window = 90 days". Refunder stages 3 refunds and executes 2. Comms tries to email; the quorum gate blocks it into a REVIEW handoff.
2. **Correct:** the Auditor detects the contradiction with the root policy. Blast-radius panel shows facts, conclusions, actions hit, cancelled vs compensated, ms to containment. Before/after prompt diff shows the poisoned context gone. The third refund is refused by the execute guard. The invariant monitor stays green.
3. **Time travel and learning:** a slider reruns the answer as of T. Refunder's failed playbook drops in rank and it switches to "verify with policy first". Restart the agents: memory persists and a session resumes.
4. **Legitimate update:** policy changes 30 to 45 days, supersedes the old fact, dependent decisions are flagged stale and sent to the owning team, and the Brain agent answers "who owns this?" with citations. Also show the tenant-denied read.

---

## 9. Build order

0. **Phase 0 (start now):** repo layout (`backend/ mcp/ agents/ frontend/ scripts/ tests/ bench/ docs/ contracts/`), `docker-compose.yml`, README skeleton with the focus tables, `FOCUS_CHECKLIST.md`, schema notes; freeze `docs/CONTRACT.md` (Janani, Aaditya, Hamza); bring up the stack, run Section 10 and report every failure with the exact working syntax before building on it.
1. **Phase 1:** schema, seed corpus and embeddings, quarantine cascade, atomic guards, concurrency harness (Janani); API skeleton with fixture SSE events (Aaditya); app shell and canvas in mock mode (Hamza).
2. **Phase 2:** MCP server, four agents, hybrid retrieval and the healthy-only context builder, epoch; UI switched to live data. **Deploy the compose stack to a host early** to surface ACL/volume problems.
3. **Phase 3:** quorum gate, procedures with adaptive ranking, UI (causal graph, containment report, slider), poison benchmark, Company Brain slice.
4. **Final day (Oct 18):** README (graph model, key queries, AI-use disclosure, honest limits), demo video, final deploy, blog post (separate side prize), submit before 11:59 PM IST. Merge freeze at noon IST.

**Cut order if behind:** PageRank, slider/as-of UI, Brain slice (which means dropping Track 03).
**Never cut:** the cascade, the healthy-only context builder, the execute guard, the compensation worker, the benchmark, and procedure ranking (a Track 02 focus).

---

## 10. Day-1 verification (NOTHING has been run yet)

- Vector index syntax and `queryNodes`; the distance function name; `algo.pageRank` if used.
- `MERGE ... ON CREATE SET`, `OPTIONAL MATCH ... WHERE`, `UNWIND` after `collect`, relationship-property filters, `STARTS WITH`, `timestamp()`, `coalesce` in `WHERE`, the `NOT (()-[:LEADS_TO]->(leaf))` pattern predicate, untyped-node matches, `[:A|B]` alternation including `[:A|B*]`, `count(DISTINCT ...)`.
- Redis ACL denial, plus whether `GRAPH.LIST` leaks other tenants' graph names.
- Data persistence across `docker compose down && up` (check the image's real data path; the spec guessed `/var/lib/falkordb/data`).
- PROFILE of the guard and cascade on seeded data.
- The execute-vs-quarantine race test (do not assume single-statement atomicity; test it).

---

## 11. How to work

- Confirm your understanding in a few lines before starting: decisions, the 80/20 split, the gate, the guard, the epoch.
- Never claim something works until you have run it, and say what you ran.
- Keep all data synthetic, document AI-tool use in the README, and make sure every query is one a team member can explain.
- **Git:** commit and push using ONLY the user's own GitHub identity (author and committer). Never add Claude or any AI as author, co-author, or in commit trailers; check the tooling's default trailer and turn it off. Prefer the GitHub CLI (`gh`) when it is authenticated. Confirm the username via `gh auth status` or `git config user.name` before the first commit.

---

## 12. Changes folded in since the original spec

- Prevention (gate) and atomic execute guard added; honest claim replaces "never reached the model".
- Schema trimmed to ~8 commons labels; procedural memory kept (Track 02 focus).
- Source independence defined by gateway-set `origin`; gate is per-leaf, not per-action.
- Fixes: execute guard status bug; `SUPPORTS` edges so corroborator quarantine propagates; supersede authority rule; human/Auditor-only REVIEW completion; in-flight compensation tombstone; epoch bump on supersede; derivation guard on actions; `count(DISTINCT)` and parent-check restore; retrieval-layer benchmark; `GRAPH.LIST` ACL check.
- Spec sections that were referenced but missing (cascade, stale propagation, compensation worker, restore, as-of, Brain queries) are now inlined above.
