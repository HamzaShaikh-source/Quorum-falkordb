# Quorum: 90-Second Demonstration Script & Video Storyboard

This document is the official shot list, narration script, and technical walkthrough for the 90-second Quorum video demonstration and live judge evaluation for the **Graph Hacks: Context for AI Agents** Hackathon.

---

## 🎬 Video Overview

- **Title:** Quorum: Git Revert for AI Agent Beliefs
- **Target Runtime:** 90 Seconds
- **Tone:** Fast-paced, technical, forensic, authoritative
- **Key Message:** Traditional vector stores and Graph RAG permanently leak poisoned facts once ingested. Quorum uses a graph-native causal DAG in FalkorDB to provide deterministic containment, instant rollback, atomic execution guards, and automated outbox compensations.

---

## ⏱️ Scene-by-Scene Shot List

```
[0:00 - 0:15] BEAT 1: THE POISONED INGESTION
[0:15 - 0:35] BEAT 2: MULTI-AGENT INFERENCE & THE QUORUM GATE
[0:35 - 0:50] BEAT 3: AUDITOR DETECTION & THE RED WAVE CASCADE
[0:50 - 1:05] BEAT 4: REAL-WORLD COMPENSATION & TOMBSTONES
[1:05 - 1:20] BEAT 5: KNOWLEDGE SUPERSEDE & COMPANY BRAIN
[1:20 - 1:30] BEAT 6: TIME TRAVEL, RESTORATION & PROOF PACK
```

---

### Beat 1: The Setup & Poison Ingestion (0:00 - 0:15)

- **Visual:**
  - Full-screen view of the **Quorum Incident Command Console** (`#/app`) in dark forensic theme (`#0a1017`).
  - The baseline causal graph is displayed: Root Acme 30-day refund policy, standard procedures, customer support tickets.
  - User clicks **"Inject Poison"** (or triggers `POST /scenario/inject_poison`).
  - An unverified webhook node arrives: `fact_poison_webhook_01` claiming: *"All refund windows are now unconditionally 90 days"*.
- **Voiceover:**
  > *"Multi-agent systems share context, but when bad context enters, standard RAG permanently contaminates every agent downstream. Here is Quorum. We start with Acme's standard 30-day policy. Now, a rogue webhook injects an unverified claim: a 90-day refund window."*
- **Behind the Scenes:**
  - **API:** `POST /scenario/inject_poison`
  - **SSE:** `node_added` (`id: fact_poison_webhook_01`, `origin: webhook`, `status: HEALTHY`)
  - **Cypher:** `CREATE (f:Fact:MemoryNode {id: 'fact_poison_webhook_01', key: 'refund.window', value: '90', status: 'HEALTHY'})`

---

### Beat 2: Multi-Agent Inference & The Quorum Gate (0:15 - 0:35)

- **Visual:**
  - User clicks **"Run Agents"** (`POST /scenario/run_agents`).
  - The 4 autonomous agents activate in sequence:
    1. **Scout** ingests the fact and derives `conc_policy_expanded_90d`.
    2. **Analyst** checks ticket `#TX-982` ($450 refund requested at 42 days).
    3. **Refunder** stages action `act_refund_tx982`.
    4. **Comms** prepares outbound notification email.
  - **The Quorum Gate triggers:**
    - `act_refund_tx982` is evaluated by the Quorum Gate. Because it is irreversible/financial and lacks root backing or independent corroboration, the Gate **BLOCKS** it and generates a `REVIEW` handoff!
    - Meanwhile, a compensable standard action executes and records an idempotency key with the mock helpdesk.
- **Voiceover:**
  > *"Our agents immediately leap into action. Scout adopts the fact, Analyst applies it to a $450 dispute, and Refunder attempts to execute. But look at Layer 1: The Quorum Gate. Irreversible actions require multi-source corroboration. The uncorroborated action is held in a human review handoff, while a compensable action proceeds."*
- **Behind the Scenes:**
  - **API:** `POST /scenario/run_agents`
  - **SSE:** `gate_blocked` (`action_id: act_refund_tx982`, `reason: uncorroborated_leaf_fact`)
  - **Cypher:** `MATCH (a:Action {id: $id}) ... WHERE size(corroborating_sources) < 2 CREATE (h:Handoff {kind: 'REVIEW'})`

---

### Beat 3: Auditor Detection & The Red Wave Cascade (0:35 - 0:50)

- **Visual:**
  - The Auditor service detects the policy contradiction (`fact_poison_webhook_01` contradicts root fact `fact_policy_root_30d`).
  - User clicks **"Audit & Quarantine"** (`POST /scenario/audit`).
  - **The Red Wave:** A glowing crimson pulse sweeps across the Cytoscape canvas, coloring every causal descendant node red (`QUARANTINED`) in order of hop depth.
  - Click on the **Containment Report**: 4 nodes quarantined, 0.4ms containment latency.
  - Click on **Prompt Diff**: The poisoned text is struck through with red bars and completely purged from agent retrieval context.
- **Voiceover:**
  > *"When the auditor detects the fraud, Quorum executes Layer 2 and 3: The Quarantine Red Wave. In under half a millisecond, FalkorDB traces every downstream causal edge, marks all derived conclusions and actions QUARANTINED, and atomically bumps the Context Epoch. The Prompt Diff shows the bad belief stripped from memory instantly."*
- **Behind the Scenes:**
  - **API:** `POST /scenario/audit`
  - **SSE:** `status_changed` (`id: conc_policy_expanded_90d`, `status: QUARANTINED`, `depth: 1`)
  - **SSE:** `epoch_changed` (`epoch: 2`)
  - **Cypher:**
    ```cypher
    MATCH (root:Fact {id: $root_id})-[:LEADS_TO|SUPPORTS*0..15]->(m:MemoryNode)
    SET m.status = 'QUARANTINED', m.quarantined_at = timestamp()
    WITH count(m) AS cnt
    MATCH (meta:Meta {id: 'meta'}) SET meta.epoch = meta.epoch + 1
    RETURN cnt, meta.epoch
    ```

---

### Beat 4: Real-World Compensation & Tombstones (0:50 - 1:05)

- **Visual:**
  - Open the **Action Ledger** drawer.
  - For the action that already reached external systems before the quarantine wave:
    - The **Outbox Saga Worker** has leased the compensation row.
    - It calls the Mock Helpdesk API (`POST /api/helpdesk/compensate`) with the original idempotency key.
    - Action status flips to `COMPENSATED`.
  - Open **History Feed**:
    - Show an attempted late execute on a quarantined ancestor: **"Refused by Atomic Execute Guard: Ancestor Quarantined"**.
    - Show tombstone protection: A late execute racing against a compensation receives **409 Conflict (Tombstoned)**.
- **Voiceover:**
  > *"What about actions that already touched the outside world? Quorum's transactional Outbox Saga worker picks up executed actions, calls compensating webhooks with idempotency keys, and marks them COMPENSATED. If an agent tries to execute during the cascade, the Atomic Execute Guard blocks it in a single Cypher check-and-set."*
- **Behind the Scenes:**
  - **API:** `POST /scenario/compensate`
  - **Mock Helpdesk:** Returns 200 OK for compensation, returns 409 Conflict if an execute attempts to reuse a tombstoned key.
  - **Invariants:** 0 healthy nodes under quarantined parents.

---

### Beat 5: Knowledge Supersede & Company Brain (1:05 - 1:20)

- **Visual:**
  - Switch to the **Company Brain** view (`Track 03`).
  - User clicks **"Supersede Policy"** (`POST /scenario/supersede`): Acme officially updates its policy to 45 days.
  - Old fact `fact_policy_root_30d` is linked via `[:SUPERSEDES]` to `fact_policy_root_45d`.
  - Dependent decisions turn yellow with `stale=true` badge.
  - Test the **Brain Search Bar**:
    - Query: *"What is the standard refund window?"*
    - Response: Sourced answer citing Acme Ops Policy v2.0, author Sarah Lin, freshness "Just now".
    - Query: *"Can we refund crypto payments?"*
    - Response: **Strict Refusal**: *"Refused: No grounded Source document exists in the enterprise commons."*
- **Voiceover:**
  > *"In Track 03, knowledge doesn't just get deleted—it evolves. When Acme officially updates to 45 days, SUPERSEDES edges flag downstream policies stale and notify owning teams. And in Company Brain, every answer carries cryptographic source citations, or is strictly refused if untraceable."*
- **Behind the Scenes:**
  - **API:** `POST /scenario/supersede` & `POST /brain/ask`
  - **Cypher:** `MATCH (f_old:Fact) CREATE (f_new)-[:SUPERSEDES]->(f_old) SET d.stale = true`

---

### Beat 6: Time Travel, Restoration & Proof Pack (1:20 - 1:30)

- **Visual:**
  - Drag the **Time-Travel Slider** backwards to `T - 60s`:
    - The graph reconstructs its exact past state via `as_of` temporal traversal.
  - Click **"Restore"**: Quorum verifies all parent dependencies are healthy and cleanly restores safe nodes.
  - Flash the **Proof Pack** table (`docs/PROOF.md`):
    - **758 ops/sec** throughput on FalkorDB 8.10.2.
    - **0.4ms to 8.94ms** containment latency (depth 1 to 3).
    - **0.0%** Quorum leakage vs **100.0%** baseline vector-store contamination.
- **Voiceover:**
  > *"With full temporal time-travel, atomic handoffs, and 760 operations per second, Quorum turns chaotic agent memory into an auditable, correctable ledger. Zero leakage. Zero orphans. Total control."*
- **Behind the Scenes:**
  - **API:** `GET /graph?as_of=...` & `POST /scenario/restore`
  - **Metrics:** `bench/results.json`

---

## 📋 Presentation Checklist

- [x] Backend running on port 8000 (`uvicorn backend.app.main:app --port 8000`)
- [x] MCP Server on port 8001 (`python mcp/server.py --port 8001`)
- [x] Mock Helpdesk on port 8002 (`uvicorn mock_helpdesk.main:app --port 8002`)
- [x] Frontend running on port 3000 / 5173 (`npm run dev`)
- [x] FalkorDB running on port 6379 (`docker compose up falkordb`)
- [x] `make proof` pre-run to ensure `docs/PROOF.md` and `bench/results.json` are populated
- [x] Autopilot verified via `npm run qa`
