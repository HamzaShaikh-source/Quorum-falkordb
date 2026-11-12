# Quorum — Cross-Team Integration Guide

> **Author:** Janani (Person 1 – Graph & FalkorDB)  
> **Target branch:** `janani` → integrates with `p2-backend-agents` (Aaditya) and `hamza/frontend`

---

## Overview

Quorum has three contributor layers:

| Person | Branch | Responsibility |
|--------|--------|----------------|
| P1 — Janani | `janani` | FalkorDB graph layer (`graph/queries.py`) |
| P2 — Aaditya | `p2-backend-agents` | FastAPI backend, agents, scenario orchestrator |
| P3 — Hamza | `hamza/frontend` | Cytoscape.js frontend, console UI |

---

## How P1's Graph Layer Connects to P2's Backend

P2's backend (`backend/app/graph/queries.py`) contains a **thread-safe in-memory `GraphStore`** that mirrors the P1 interface exactly and is used as a fallback when FalkorDB is unavailable.

When FalkorDB **is** running, P2's `bench/run.py` automatically detects and delegates to P1's real Cypher layer:

```python
# bench/run.py (P2, already in place)
try:
    from graph import queries as falkor_queries   # ← P1's real FalkorDB layer
    db = falkor_queries.get_db()
    db.select_graph("probe").query("RETURN 1")
    USE_LIVE_FALKORDB = True
except Exception:
    USE_LIVE_FALKORDB = False   # Falls back to in-memory GraphStore
```

### What makes this work

For `from graph import queries` to resolve inside the backend container:

1. The `backend/Dockerfile` build context is the **repo root** (see `docker-compose.yml`).
2. The `COPY graph/ graph/` step copies P1's graph package into the container.
3. `PYTHONPATH` is set to `/app` — which contains both `backend/app/` and `graph/`.

---

## Service Port Map

| Service | Container Port | Host Port | Purpose |
|---------|---------------|-----------|---------|
| `falkordb` | 6379 | 6379 | FalkorDB / Redis |
| `falkordb-browser` | 8080 | 8080 | FalkorDB web UI |
| `mock-helpdesk` | 8002 | 8002 | Tombstone helpdesk (P1) |
| `backend` | 8000 | 8000 | FastAPI (P2) |
| `mcp-server` | 8001 | 8001 | MCP tool server (P2) |
| `frontend` | 5173 | 3000 | Cytoscape.js UI (P3) |

---

## Running the Full Stack

```bash
# From repo root (requires Docker + Docker Compose)
docker compose up -d

# Seed the graph with synthetic corpus
python scripts/seed.py

# Run proof harness (auto-detects live FalkorDB)
python bench/run.py

# Run all tests
make test
```

---

## Key P1 Functions (called by P2 services)

| Function | Called by | Purpose |
|----------|-----------|---------|
| `graph.queries.stage_action` | `bench/run.py` | Stages an action node with gate status |
| `graph.queries.derive` | `bench/run.py` | Creates a derived conclusion from parent facts |
| `graph.queries.execute_guard` | `bench/run.py` | Atomic CAS guard before external execution |
| `graph.queries.cascade` | `backend/app/services/executor.py` | Quarantine cascade over `LEADS_TO\|SUPPORTS*` |
| `graph.queries.claim_handoff` | `bench/run.py` | Atomic single-winner handoff claim |
| `graph.queries.retrieve` | `bench/run.py` | Vector + status-filtered retrieval (no leakage) |
| `graph.queries.as_of` | `backend/app/routes/graph.py` | Historical belief recall at timestamp `t` |
| `graph.queries.invariant_check` | `bench/run.py` | Live integrity checks (DAG acyclicity, orphan audit) |
| `graph.queries.restore` | `backend/app/services/executor.py` | Restore root + propagate if all parents healthy |

---

## Environment Variables

```dotenv
# backend/.env (copy from .env.example)
FALKORDB_HOST=falkordb          # Docker service name
FALKORDB_PORT=6379
HELPDESK_URL=http://mock-helpdesk:8002
MCP_SERVER_URL=http://mcp-server:8001
QUORUM_MODE=live                # 'live' or 'replay'
```

---

## Verified Benchmark Numbers (Live FalkorDB, WSL Ubuntu)

| Metric | Value |
|--------|-------|
| 30-agent throughput | **2,030 ops/sec** |
| p50 latency | 12.6 ms |
| p95 latency | 23.1 ms |
| p99 latency | 28.9 ms |
| Handoff race winner | **100%** single-winner (100 × 30 claimers) |
| Poison containment D1 | 1.40 ms |
| Poison containment D2 | 1.62 ms |
| Poison containment D3 | 2.88 ms |
| Retrieval leakage | **0.0%** (quarantined nodes never returned) |
