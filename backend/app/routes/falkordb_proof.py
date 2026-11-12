"""
Evidence for the "graph-native" claim, from the running hub rather than from a README.

GET /api/falkordb/proof runs the quarantine traversal inside FalkorDB, runs the same traversal in this process, compares
the two, and reports which engine has answered each traversal since start-up. A judge (or a CI job) can call it any time.
"""
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Query

from app.auth import AuthContext, require_role
from app.graph.queries import GRAPH_STATS, get_graph_store

router = APIRouter()

CAUSAL = ("LEADS_TO", "SUPPORTS", "FROM_SOURCE")

# What FalkorDB executes for the decisions that matter. Each is a real statement the hub runs (see persistence.py, queries.py,
# context_pack.py); `where` names the code path so the claim can be checked in the source.
STATEMENTS = [
    {"decision": "Quarantine cascade", "where": "GraphStore.cascade_quarantine", "cypher": "MATCH (a:QNode)-[:LEADS_TO|SUPPORTS|FROM_SOURCE]->(b:QNode) WHERE a.id IN $frontier RETURN DISTINCT b.id   (one statement per hop)"},
    {"decision": "Status flip", "where": "GraphStore.cascade_quarantine", "cypher": "UNWIND $rows AS r MATCH (n:QNode {id: r.id}) SET n.status = 'QUARANTINED', n.depth = r.depth"},
    {"decision": "Execute guard: are all ancestors healthy?", "where": "GraphStore.execute_guard -> get_ancestors", "cypher": "MATCH (b:QNode)-[:LEADS_TO|SUPPORTS|FROM_SOURCE]->(a:QNode) WHERE a.id IN $frontier RETURN DISTINCT b.id"},
    {"decision": "Blast-radius preview", "where": "GraphStore.get_descendants", "cypher": "MATCH (a:QNode)-[:LEADS_TO|SUPPORTS]->(b:QNode) WHERE a.id IN $frontier RETURN DISTINCT b.id"},
    {"decision": "Provenance path", "where": "context_pack.find_path", "cypher": "MATCH p = shortestPath((a)-[:LEADS_TO|SUPPORTS|FROM_SOURCE*]-(b)) RETURN [n IN nodes(p) | n.id]"},
    {"decision": "Semantic retrieval", "where": "routes/observe.semantic_check", "cypher": "CALL db.idx.vector.queryNodes('QNode', 'embedding', $k, vecf32($q)) YIELD node, score"},
    {"decision": "Durable recovery state", "where": "Persister.sync", "cypher": "MERGE (s:QCheckpoint {id:'state'}) SET s.json = $state   (one statement) + UNWIND ... MERGE (n:QNode {id: row.id})"},
]


def _pick_root(store, wanted: Optional[str]) -> Optional[str]:
    if wanted and wanted in store.nodes:
        return wanted
    best, best_n = None, -1
    with store._lock:
        children: Dict[str, int] = {}
        for e in store.edges:
            if e.type.value in CAUSAL:
                children[e.source] = children.get(e.source, 0) + 1
        for nid, n in children.items():
            if n > best_n and nid in store.nodes:
                best, best_n = nid, n
    return best


@router.get("/falkordb/proof")
def proof(root: Optional[str] = Query(None, max_length=80), auth: AuthContext = Depends(require_role(["human", "auditor"]))) -> Dict[str, Any]:
    store = get_graph_store(auth.tenant)
    out: Dict[str, Any] = {
        "tenant": auth.tenant,
        "graph": f"quorum:{auth.tenant}",
        "statements": STATEMENTS,
        "traversals_answered_by": dict(GRAPH_STATS),
        "last_cascade_engine": store.last_cascade_engine,
    }
    if store.persister is not None:
        g = store.persister
        try:
            out["database"] = {
                "nodes": g._q("proof_count_nodes", "MATCH (n:QNode) RETURN count(n)", {}).result_set[0][0],
                "edges": g._q("proof_count_edges", "MATCH (:QNode)-[r]->(:QNode) RETURN count(r)", {}).result_set[0][0],
            }
        except Exception as exc:  # noqa: BLE001
            out["database"] = {"error": str(exc)[:200]}
    rid = _pick_root(store, root)
    if rid is None:
        out["comparison"] = None
        out["note"] = "No causal edges yet. Remember a fact and derive from it, then call this again."
        return out
    t0 = time.perf_counter()
    with store._lock:
        in_db = store.closure_in_falkordb(rid, CAUSAL, "out")
        db_ms = (time.perf_counter() - t0) * 1000
        t1 = time.perf_counter()
        in_proc = store._process_closure(rid, CAUSAL)
        proc_ms = (time.perf_counter() - t1) * 1000
    out["comparison"] = {
        "root": rid,
        "falkordb": None if in_db is None else {"descendants": len(in_db), "max_depth": max(in_db.values(), default=0), "ms": round(db_ms, 2)},
        "process": {"descendants": len(in_proc), "max_depth": max(in_proc.values(), default=0), "ms": round(proc_ms, 3)},
        "identical": in_db is not None and in_db == in_proc,
    }
    return out
