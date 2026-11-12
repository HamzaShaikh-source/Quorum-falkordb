# Git Revert for AI Agent Beliefs: How We Built Quorum on FalkorDB to Eliminate Context Contamination

*Submitted to the **Graph Hacks: Context for AI Agents** Hackathon (WeMakeDevs x FalkorDB, Oct 2026).*  
*Tracks Entered: Track 02 (Agent Memory and Coordination) & Track 03 (Company Brain).*

---

## 1. The Poisoned Context Problem

When an autonomous multi-agent system operates in production, agents do not merely answer questions; they ingest webhooks, read company documentation, converse across team channels, derive conclusions, coordinate handoffs, and trigger irreversible external actions (e.g. issuing refunds, deploying code, modifying databases).

In standard Retrieval-Augmented Generation (RAG) architectures—whether based on flat vector databases or naive hybrid search—**memory is flat and non-causal**. If a rogue webhook, hallucinated statement, or outdated policy document injects an erroneous fact into the vector store:

1. **Context Contamination Cascades:** Downstream agents retrieve the poisoned embedding, treat it as ground truth, and derive new conclusions.
2. **Side Effects Escape:** Execution engines trigger real-world API mutations based on contaminated beliefs.
3. **Irreversible Invalidation:** Deleting the original bad vector from a vector store **does nothing** to clean up the dozens of derived conclusions, action records, and agent session prompts that now incorporate that belief. The bad data has metastasized.

We asked a simple question: **What if agent beliefs worked like Git?**  
What if you could run `git revert` on an erroneous belief, and the system would instantly, deterministically, and atomically roll back that fact, invalidate every downstream conclusion derived from it, halt any pending execution in flight, and dispatch automated compensations to reverse real-world mutations?

That is **Quorum**.

---

## 2. Graph-Native Architecture: Causal DAGs in FalkorDB

Rather than treating memory as an unorganized bucket of text embeddings, Quorum stores all memory as a **causal Directed Acyclic Graph (DAG)** inside **FalkorDB**:

```
                       ┌─────────────────────────┐
                       │   Source (Doc/Webhook)  │
                       └────────────┬────────────┘
                                    │ [:FROM_SOURCE]
                                    ▼
                          ┌──────────────────┐
                          │    Root Fact     │
                          └─────────┬────────┘
                                    │ [:LEADS_TO]
                                    ▼
                       ┌─────────────────────────┐
                       │   Derived Conclusion    │
                       └────────────┬────────────┘
                                    │ [:LEADS_TO]
                                    ▼
                         ┌─────────────────────┐
                         │    Staged Action    │
                         └──────────┬──────────┘
                                    │ [:COMPENSATED_BY]
                                    ▼
                       ┌─────────────────────────┐
                       │   Compensation Outbox   │
                       └─────────────────────────┘
```

Every node in Quorum belongs to a typed causal hierarchy:
- `Source`: Raw external artifact with origin verification (`DOC`, `TICKET`, `WEBHOOK`, `CHAT`).
- `Fact`: Atomic unit of episodic or semantic knowledge asserted by an authenticated agent or source.
- `Conclusion`: Causal deduction linked to parent facts via `[:LEADS_TO]` edges.
- `Action`: Staged operational side effect with reversibility metadata (`REVERSIBLE`, `COMPENSABLE`, `IRREVERSIBLE`).
- `Handoff`: Transactional coordination task with lease timeouts and role-based claims.

Because memory is represented as an explicit graph topology, invalidating a belief is not an ambiguous semantic search—**it is a graph traversal**.

---

## 3. The Three Layers of Defense

Quorum implements three deterministic, non-LLM safety layers designed to catch mistakes before, during, and after execution.

```
       [INCOMING FACT]
              │
              ▼
    Layer 1: PREVENT        ──► Quorum Gate (Blocks uncorroborated irreversible actions)
              │
              ▼
    Layer 2: GUARD          ──► Atomic Execute Guard (Single check-and-set prevents racing executions)
              │
              ▼
    Layer 3: REPAIR         ──► Quarantine Red Wave Cascade (0.4ms causal invalidation & Outbox Saga)
              │
              ▼
    Layer 4: DAMAGE CONTROL ──► Automated Retraction Engine (Drafts verified correction notices)
```

### Layer 1: PREVENT (The Quorum Gate)
Before an action can even be staged for execution, it must pass the **Quorum Gate**:
- Any operation classified as `IRREVERSIBLE` (e.g. issuing irreversible financial refunds or deleting records) requires either:
  1. Root-tier policy backing directly from an immutable policy fact; OR
  2. Independent corroboration from 2+ distinct agent origins (`[:SUPPORTS]` edges).
- If uncorroborated, stale, or resting on any quarantined ancestor, the action is **BLOCKED** and an interactive `REVIEW` handoff is routed to the human supervisor queue.

### Layer 2: GUARD (Atomic Execute Guard & Tombstones)
When an agent attempts to execute an action, Quorum executes a single transactional Cypher check-and-set:
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
If **any** ancestor in the causal chain has lost its `HEALTHY` status—even milliseconds prior—the query returns 0 rows, execution aborts, and the external API is never called.

Furthermore, to handle network races where a quarantine cascade arrives while an external API call is already in-flight over the wire, Quorum's mock helpdesk enforces **Tombstones**:
- If a compensation request arrives before the execute response returns, the idempotency key is permanently tombstoned.
- When the late execute finally attempts to commit, it is rejected with **`409 Conflict (Tombstoned)`**.

### Layer 3: REPAIR (The Quarantine Red Wave & Outbox Saga)
When an Auditor detects a fraudulent or superseded fact, it triggers the **Quarantine Cascade**:
- FalkorDB traverses downstream along `[:LEADS_TO|SUPPORTS*]`, marking all derived conclusions and actions `QUARANTINED` in order of causal hop depth.
- **Context Epoch Increment:** In the same transaction, `Meta.epoch` is incremented. All connected MCP agents holding stale epochs have their context caches invalidated.
- **Outbox Saga Worker:** Any action that completed external execution before the quarantine arrived is leased by the background saga worker, which dispatches compensating webhooks with idempotency keys and exponential backoff until the action is marked `COMPENSATED`.

### Layer 4: DAMAGE CONTROL (Automated Retraction Engine)
For irreversible side-effects that already reached the outside world before the fraud was detected (e.g., an email already dispatched to a customer):
- Quorum automatically drafts a structured `CorrectionNotice` linked to the verified root truth (`Finance Ops Policy Doc v3.0`).
- Generates a ready-to-dispatch correction email explaining the error and attaches it to an `ESCALATION` Handoff in the Incident Room for one-click human review and dispatch.


---

## 4. Why FalkorDB? Benchmark Numbers & Real Proof

Standard relational and graph-on-relational engines struggle with deep, unbounded variable-length path traversal under high concurrent write loads. FalkorDB's Redis-native architecture, graphBLAS linear algebra sparse matrices, and sub-millisecond query evaluation gave us performance numbers that flat vector databases cannot match.

All numbers below were measured using our automated benchmark harness (`python bench/run.py`):

```
======================================================================
QUORUM BENCHMARK & PROOF HARNESS (make proof)
======================================================================
[1/4] 30-Agent Concurrency Benchmark (70/30 read/write):
      Throughput: 757.6 ops/sec | p50: 31.5ms | p95: 78.01ms | p99: 100.0ms
      Invariant Violations: 0

[2/4] Handoff Claim Race (100 handoffs x 30 concurrent claimers):
      Single-Winner Guarantee: 100.0% (100/100 clean claims, 0 race collisions)

[3/4] Atomic Execute-vs-Quarantine Race (50 actions under concurrent audit):
      Executed before quarantine: 0 | Refused after: 50 | Invariant Violations: 0

[4/4] Poison Benchmark across 3 Causal Depths (Fixed Seeds, No LLM):
      Depth 1: Containment: 0.4ms | Quorum Leakage: 0.0% vs Baselines: 100.0%
      Depth 2: Containment: 0.4ms | Quorum Leakage: 0.0% vs Baselines: 100.0%
      Depth 3: Containment: 0.4ms | Quorum Leakage: 0.0% vs Baselines: 100.0%
```

### The Poison Leakage Benchmark
To prove the superiority of causal graphs over standard RAG, we built a comparative retrieval benchmark comparing Quorum against two industry-standard baselines:
1. **Baseline 1 (Flat Vector Store):** ChromaDB / FAISS cosine similarity retrieval.
2. **Baseline 2 (Graph RAG Baseline):** Traditional entity-linking knowledge graph without causal validity propagation.

When poisoned facts were injected at causal depths 1, 2, and 3:
- Both Baseline 1 and Baseline 2 returned the poisoned context **100.0% of the time** after invalidation, because semantic similarity still pulled in the bad facts.
- **Quorum scored 0.0% leakage across all depths**, achieving full containment in **0.4 milliseconds**.

---

## 5. Track 03: Company Brain in the Same Graph

In addition to agent coordination (Track 02), Quorum powers an enterprise **Company Brain** (Track 03) by connecting documentation, Slack chats, Jira tickets, and corporate policies into the same commons graph:

- **Ownership & Expertise:** Teams own sources (`(:Team)-[:OWNS]->(:Source)`), and employees author or resolve them (`(:Person)-[:AUTHORED]->(:Source)-[:ABOUT]->(:Topic)`). The `/who-knows?topic=...` endpoint evaluates evidence counts to return authoritative topic leadboards.
- **Provenance Tracing:** Any decision or policy can be traced in a single Cypher hop:  
  `MATCH (d:Decision {id: $id})<-[:LEADS_TO]-(f:Fact)-[:FROM_SOURCE]->(s:Source) RETURN f.quote, s.url, s.version`
- **Knowledge Evolution (`SUPERSEDES`):** When corporate policy changes (e.g. standard refund window changing from 30 to 45 days), `SUPERSEDES` edges flag all dependent operational decisions `stale=true` and queue review handoffs directly to the owning team.
- **Cited Answers with Strict Refusal:** The `/brain/ask` endpoint returns answers accompanied by citations and owner teams. Crucially, if a user asks a question whose answer cannot be causally grounded in verified healthy sources, Quorum **strictly refuses to answer**, eliminating hallucinated corporate advice.

---

## 6. The User Interfaces: Editorial Landing & Print Proof Console

Quorum features two user interfaces designed for both technical clarity and operational control:

1. **The Editorial Landing Page (`#/`):**
   - Built on a crisp drafting paper stock (`#f3f5f2`).
   - Features an interactive **Click-to-Break Playground** where visitors can inject poison and watch traditional RAG leak contaminated memory live.
   - Includes a 1-click **"Watch the 90-second story"** button that launches the incident console and triggers the automated Autopilot walkthrough.

2. **The Incident Command Room Console (`#/app`):**
   - Styled as a dark forensic investigation suite (`#0a1017`).
   - Interactive **Cytoscape.js DAG Canvas** with animated Quarantine Red Wave pulses.
   - **Command Palette (`Ctrl+K`):** Instant keyboard-driven navigation and action execution.
   - **Prompt Diff Drawer:** Strikethroughs showing exact tokens purged from agent memory.
   - **Time-Travel Slider (`as_of`):** Scrub backwards in time to reconstruct the exact graph state at any past timestamp.
   - **Show Cypher Drawer:** Real-time query log showing parameterised Cypher statements, execution timings, and row counts.

---

## 7. How to Reproduce Our Results

Quorum is 100% offline-first and reproducible. No API keys, cloud subscriptions, or external network connectivity are required to run the full benchmark suite and deterministic multi-agent replay:

```bash
# Clone the repository
git clone https://github.com/AsteroidHH/iFone.git
cd iFone

# Run via Docker Compose (Recommended)
docker compose up --build

# Or run tests and benchmarks locally:
python -m pytest backend/tests
python bench/run.py
```

- **Frontend Console & Landing:** `http://localhost:3000` (or `http://localhost:5173`)
- **Backend API Docs:** `http://localhost:8000/docs`
- **Standalone MCP Server:** `http://localhost:8001/mcp`
- **Mock Helpdesk:** `http://localhost:8002`
- **FalkorDB Engine:** `localhost:6379`
- **FalkorDB Browser:** `http://localhost:8080`

---

## 8. Conclusion

Autonomous agents cannot safely scale in enterprise production without correctable memory. When an agent acts on bad context, the damage must not be permanent.

By combining FalkorDB's graph performance with a 3-layer causal defense system, **Quorum brings version control, atomic isolation, and transactional rollbacks to agent beliefs**.

*Built with ❤️ by Janani, Aaditya, and Hamza for Graph Hacks 2026.*
