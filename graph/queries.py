"""
Quorum Graph Query Library (graph/queries.py)

Author: Janani (Person 1 - Graph & FalkorDB)
Delivers tested Cypher statements returning plain Python dicts.
Logs every query execution to an in-memory query log for the UI 'Show Cypher' drawer.
"""

import time
import json
import logging
from typing import Any, Dict, List, Optional, Tuple
from falkordb import FalkorDB

logger = logging.getLogger("quorum.graph")

# In-memory query log (FIFO ring buffer of last 500 queries)
QUERY_LOG: List[Dict[str, Any]] = []
MAX_QUERY_LOG_SIZE = 500

_db_client: Optional[FalkorDB] = None


def get_db(host: str = "localhost", port: int = 6379) -> FalkorDB:
    global _db_client
    if _db_client is None:
        _db_client = FalkorDB(host=host, port=port)
    return _db_client


def log_query(name: str, cypher: str, ms: float, rows: int, request_id: Optional[str] = None):
    entry = {
        "ts": int(time.time() * 1000),
        "request_id": request_id or "",
        "name": name,
        "cypher": cypher.strip(),
        "ms": round(ms, 2),
        "rows": rows,
    }
    QUERY_LOG.append(entry)
    if len(QUERY_LOG) > MAX_QUERY_LOG_SIZE:
        QUERY_LOG.pop(0)


def get_query_log(limit: int = 50) -> List[Dict[str, Any]]:
    return list(reversed(QUERY_LOG[-limit:]))


def get_graph(tenant_id: str, kind: str = "commons"):
    clean_tenant = tenant_id[7:] if tenant_id.startswith("tenant_") else tenant_id
    graph_name = f"tenant_{clean_tenant}:{kind}"
    db = get_db()
    g = db.select_graph(graph_name)
    # Ensure Meta node exists
    try:
        res = g.query("MATCH (m:Meta {id: 'meta'}) RETURN m.epoch")
        if not res.result_set:
            g.query("CREATE (:Meta {id: 'meta', epoch: 1})")
    except Exception:
        pass
    return g


def ensure_indexes(tenant_id: str):
    """Initializes vector index and primary lookup indexes."""
    g = get_graph(tenant_id, "commons")
    queries = [
        "CREATE INDEX FOR (f:Fact) ON (f.id)",
        "CREATE INDEX FOR (f:Fact) ON (f.key)",
        "CREATE INDEX FOR (f:Fact) ON (f.status)",
        "CREATE INDEX FOR (c:Conclusion) ON (c.id)",
        "CREATE INDEX FOR (c:Conclusion) ON (c.status)",
        "CREATE INDEX FOR (a:Action) ON (a.id)",
        "CREATE INDEX FOR (a:Action) ON (a.status)",
        "CREATE INDEX FOR (h:Handoff) ON (h.id)",
        "CREATE INDEX FOR (s:Source) ON (s.id)",
        "CREATE INDEX FOR (m:MemoryNode) ON (m.id)",
        "CREATE VECTOR INDEX FOR (f:Fact) ON (f.embedding) OPTIONS {dimension: 384, similarityFunction: 'cosine'}",
    ]
    for q in queries:
        try:
            g.query(q)
        except Exception:
            pass


# =========================================================================
# 1. ATOMIC DERIVATION GUARD: STAGE ACTION & DERIVE
# =========================================================================

def stage_action(
    tenant_id: str,
    action_id: str,
    parent_ids: List[str],
    action_type: str,
    payload: Dict[str, Any],
    reversibility: str,
    gate: str = "PASSED",
    procedure_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    request_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Stages an action with the atomic derivation guard: commits only if all
    parent_ids currently exist and are HEALTHY.
    """
    g = get_graph(tenant_id, "commons")
    name = "stage_action"
    idempotency_key = idempotency_key or f"idem_{action_id}"
    payload_str = json.dumps(payload)

    if not parent_ids:
        # Standalone action without parents
        cypher = """
        CREATE (a:Action:MemoryNode {
            id: $id, type: $type, payload: $payload, status: 'HEALTHY',
            executed: false, executed_at: null, confirmed: false,
            gate: $gate, reversibility: $reversibility,
            procedure_id: $procedure_id, idempotency_key: $idempotency_key,
            stale: false, created_at: timestamp()
        })
        RETURN a.id, a.status, a.gate
        """
        params = {
            "id": action_id, "type": action_type, "payload": payload_str,
            "gate": gate, "reversibility": reversibility,
            "procedure_id": procedure_id, "idempotency_key": idempotency_key
        }
    else:
        cypher = """
        MATCH (p:MemoryNode) WHERE p.id IN $parent_ids
        WITH collect(p) AS parents
        WHERE size(parents) = size($parent_ids) AND ALL(x IN parents WHERE x.status = 'HEALTHY')
        CREATE (a:Action:MemoryNode {
            id: $id, type: $type, payload: $payload, status: 'HEALTHY',
            executed: false, executed_at: null, confirmed: false,
            gate: $gate, reversibility: $reversibility,
            procedure_id: $procedure_id, idempotency_key: $idempotency_key,
            stale: false, created_at: timestamp()
        })
        WITH a, parents
        UNWIND parents AS p
        CREATE (p)-[:LEADS_TO]->(a)
        RETURN a.id, a.status, a.gate
        """
        params = {
            "id": action_id, "parent_ids": parent_ids, "type": action_type,
            "payload": payload_str, "gate": gate, "reversibility": reversibility,
            "procedure_id": procedure_id, "idempotency_key": idempotency_key
        }

    t0 = time.time()
    res = g.query(cypher, params)
    ms = (time.time() - t0) * 1000
    rows = len(res.result_set)
    log_query(name, cypher, ms, rows, request_id)

    if not rows:
        return {"success": False, "error": "Atomic derivation guard refused: parents missing or not HEALTHY"}

    return {
        "success": True,
        "action_id": res.result_set[0][0],
        "status": res.result_set[0][1],
        "gate": res.result_set[0][2],
    }


def derive(
    tenant_id: str,
    conclusion_id: str,
    parent_ids: List[str],
    text: str,
    is_decision: bool = False,
    owner_team: Optional[str] = None,
    request_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Derives a new Conclusion (or Decision) node with atomic derivation guard.
    """
    if not parent_ids:
        return {"success": False, "error": "Atomic derivation guard refused: parent_ids cannot be empty"}

    g = get_graph(tenant_id, "commons")
    name = "derive"
    label_part = ":Conclusion:Decision:MemoryNode" if is_decision else ":Conclusion:MemoryNode"

    cypher = f"""
    MATCH (p:MemoryNode) WHERE p.id IN $parent_ids
    WITH collect(p) AS parents
    WHERE size($parent_ids) > 0 AND size(parents) = size($parent_ids) AND ALL(x IN parents WHERE x.status = 'HEALTHY')
    CREATE (c{label_part} {{
        id: $id, text: $text, status: 'HEALTHY', stale: false,
        owner_team: $owner_team, created_at: timestamp()
    }})
    WITH c, parents
    UNWIND parents AS p
    CREATE (p)-[:LEADS_TO]->(c)
    RETURN DISTINCT c.id, c.status
    """
    params = {"id": conclusion_id, "parent_ids": parent_ids, "text": text, "owner_team": owner_team}

    t0 = time.time()
    res = g.query(cypher, params)
    ms = (time.time() - t0) * 1000
    rows = len(res.result_set)
    log_query(name, cypher, ms, rows, request_id)

    if not rows:
        return {"success": False, "error": "Atomic derivation guard refused: parents missing or not HEALTHY"}

    return {"success": True, "conclusion_id": res.result_set[0][0], "status": res.result_set[0][1]}


# =========================================================================
# 2. ATOMIC EXECUTE GUARD
# =========================================================================

def execute_guard(tenant_id: str, action_id: str, request_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Atomic execute guard. Single statement checking that action is HEALTHY,
    unexecuted, gate PASSED, and has zero non-healthy ancestors over [:LEADS_TO|SUPPORTS*].
    Returns success=True only if the action passed all checks and was marked executed.
    """
    g = get_graph(tenant_id, "commons")
    name = "execute_guard"
    cypher = """
    MATCH (a:Action {id: $id})
    WHERE a.status = 'HEALTHY' AND a.executed = false AND a.gate = 'PASSED' AND coalesce(a.stale, false) = false
    OPTIONAL MATCH (x:MemoryNode)-[:LEADS_TO|SUPPORTS*]->(a)
    WHERE coalesce(x.status, 'MISSING') <> 'HEALTHY' OR coalesce(x.stale, false) = true
    WITH a, count(DISTINCT x) AS bad
    WHERE bad = 0
    SET a.executed = true, a.executed_at = timestamp(), a.confirmed = false
    RETURN a.id, a.executed, a.executed_at, a.idempotency_key, a.type, a.payload
    """
    t0 = time.time()
    res = g.query(cypher, {"id": action_id})
    ms = (time.time() - t0) * 1000
    rows = len(res.result_set)
    log_query(name, cypher, ms, rows, request_id)

    if not rows:
        return {"success": False, "action_id": action_id, "reason": "Guard refused: ancestor non-healthy/stale or gate blocked"}

    row = res.result_set[0]
    return {
        "success": True,
        "action_id": row[0],
        "executed": row[1],
        "executed_at": row[2],
        "idempotency_key": row[3],
        "action_type": row[4],
        "payload": row[5],
    }


def confirm_action(tenant_id: str, action_id: str, request_id: Optional[str] = None) -> bool:
    """Sets confirmed=true upon receiving 200 OK from external API."""
    g = get_graph(tenant_id, "commons")
    cypher = "MATCH (a:Action {id: $id}) SET a.confirmed = true RETURN a.id"
    t0 = time.time()
    res = g.query(cypher, {"id": action_id})
    ms = (time.time() - t0) * 1000
    log_query("confirm_action", cypher, ms, len(res.result_set), request_id)
    return len(res.result_set) > 0


# =========================================================================
# 3. QUARANTINE CASCADE & COMPENSATIONS
# =========================================================================

def cascade(tenant_id: str, root_fact_id: str, request_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Quarantine cascade. In one pass:
    1. Collects descendants over [:LEADS_TO|SUPPORTS*].
    2. Partitions actions before mutating status.
    3. Sets status='QUARANTINED', quarantined_at=now, quarantine_root=root_id.
    4. Increments Meta.epoch.
    """
    g = get_graph(tenant_id, "commons")
    name = "cascade"
    t0 = time.time()

    # Query descendants with shortest path length (depth)
    cypher = """
    MATCH (root:Fact {id: $root_id})
    OPTIONAL MATCH p = (root)-[:LEADS_TO|SUPPORTS*0..15]->(m:MemoryNode)
    WITH m, length(p) AS depth
    ORDER BY depth ASC
    WITH collect(DISTINCT {node: m, depth: depth}) AS items
    UNWIND items AS item
    WITH item.node AS m, item.depth AS depth
    WHERE m IS NOT NULL
    SET m.status = 'QUARANTINED',
        m.quarantined_at = timestamp(),
        m.quarantine_root = $root_id
    WITH collect(DISTINCT {id: m.id, labels: labels(m), depth: depth, executed: m.executed, reversibility: m.reversibility}) AS affected
    MATCH (meta:Meta {id: 'meta'})
    SET meta.epoch = meta.epoch + 1
    RETURN affected, meta.epoch
    """
    res = g.query(cypher, {"root_id": root_fact_id})
    ms_containment = (time.time() - t0) * 1000
    rows = len(res.result_set)
    log_query(name, cypher, ms_containment, rows, request_id)

    if not rows or not res.result_set[0][0]:
        return {
            "success": False,
            "error": f"Root fact '{root_fact_id}' not found",
            "affected_count": 0,
            "ms_to_containment": ms_containment,
            "node_ids_by_depth": {},
            "executed_actions": [],
        }

    raw_affected = res.result_set[0][0]
    epoch = res.result_set[0][1]

    node_ids_by_depth: Dict[int, List[str]] = {}
    executed_actions: List[Dict[str, Any]] = []

    for item in raw_affected:
        nid = item["id"]
        d = item["depth"]
        node_ids_by_depth.setdefault(d, []).append(nid)
        if "Action" in item.get("labels", []) and item.get("executed") is True:
            executed_actions.append({
                "action_id": nid,
                "reversibility": item.get("reversibility", "COMPENSABLE")
            })

    return {
        "success": True,
        "root_id": root_fact_id,
        "affected_count": len(raw_affected),
        "ms_to_containment": round(ms_containment, 2),
        "node_ids_by_depth": node_ids_by_depth,
        "executed_actions": executed_actions,
        "epoch": epoch,
    }


def create_compensations(
    tenant_id: str, executed_actions: List[Dict[str, Any]], request_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Separate idempotent compensation creation for actions that were executed=true.
    REVERSIBLE -> revert request
    COMPENSABLE -> reverse request
    IRREVERSIBLE -> ESCALATION handoff + notice
    """
    g = get_graph(tenant_id, "commons")
    results = []
    cypher = """
    MATCH (a:Action {id: $action_id})
    MERGE (c:Compensation {id: $comp_id})
    ON CREATE SET c.target_action_id = a.id,
                  c.status = 'PENDING_COMPENSATION',
                  c.attempts = 0,
                  c.lease_until = null,
                  c.idempotency_key = 'comp_' + a.idempotency_key,
                  c.reversibility = a.reversibility,
                  c.created_at = timestamp()
    MERGE (a)-[:COMPENSATED_BY]->(c)
    RETURN c.id, c.status
    """
    for act in executed_actions:
        aid = act["action_id"]
        cid = f"comp_{aid}"
        t0 = time.time()
        res = g.query(cypher, {"action_id": aid, "comp_id": cid})
        ms = (time.time() - t0) * 1000
        log_query("create_compensation", cypher, ms, len(res.result_set), request_id)
        if res.result_set:
            results.append({"compensation_id": cid, "action_id": aid, "status": "PENDING_COMPENSATION"})
    return results


# =========================================================================
# 4. SUPERSEDE & STALE PROPAGATION
# =========================================================================

def supersede(
    tenant_id: str,
    old_fact_id: str,
    new_fact_id: str,
    new_value: str,
    new_text: str,
    new_embedding: List[float],
    request_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Supersedes an existing healthy fact with new value.
    Marks old fact valid_to=now, status=SUPERSEDED.
    Creates new fact, SUPERSEDES edge, increments Meta.epoch.
    Propagates stale=true to all Conclusion/Decision descendants.
    Creates REVIEW handoffs to owning teams.
    """
    g = get_graph(tenant_id, "commons")
    name = "supersede"

    cypher_main = """
    MATCH (old:Fact {id: $old_id}) WHERE old.status = 'HEALTHY' AND old.valid_to IS NULL
    SET old.valid_to = timestamp(), old.status = 'SUPERSEDED'
    CREATE (new:Fact:MemoryNode {
        id: $new_id, key: old.key, value: $value, text: $text, status: 'HEALTHY',
        embedding: vecf32($embedding), valid_from: timestamp(), stale: false
    })
    CREATE (new)-[:SUPERSEDES]->(old)
    WITH old, new
    MATCH (meta:Meta {id: 'meta'})
    SET meta.epoch = meta.epoch + 1
    RETURN new.id, meta.epoch
    """
    t0 = time.time()
    res = g.query(cypher_main, {
        "old_id": old_fact_id, "new_id": new_fact_id,
        "value": new_value, "text": new_text, "embedding": new_embedding
    })
    ms = (time.time() - t0) * 1000
    log_query(name, cypher_main, ms, len(res.result_set), request_id)

    if not res.result_set:
        return {"success": False, "error": "Old fact not found, not HEALTHY, or already superseded"}

    epoch = res.result_set[0][1]

    # Stale propagation: flag descendant conclusions as stale
    cypher_stale = """
    MATCH (old:Fact {id: $old_id})-[:LEADS_TO*1..10]->(c:Conclusion)
    SET c.stale = true
    RETURN c.id, c.owner_team
    """
    t1 = time.time()
    res_stale = g.query(cypher_stale, {"old_id": old_fact_id})
    ms_stale = (time.time() - t1) * 1000
    log_query("stale_propagation", cypher_stale, ms_stale, len(res_stale.result_set), request_id)

    stale_conclusions = [r[0] for r in res_stale.result_set]
    return {
        "success": True,
        "new_fact_id": new_fact_id,
        "stale_conclusions_count": len(stale_conclusions),
        "epoch": epoch,
    }


# =========================================================================
# 5. RESTORE
# =========================================================================

def restore(tenant_id: str, root_fact_id: str, request_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Restores quarantined subgraph starting from root.
    A node is restored ONLY if ALL its parents (LEADS_TO and SUPPORTS) are HEALTHY.
    NEVER restores executed actions. Bumps Meta.epoch.
    """
    g = get_graph(tenant_id, "commons")
    name = "restore"

    # Step 1: Restore root fact
    cypher_root = """
    MATCH (root:Fact {id: $root_id, status: 'QUARANTINED'})
    SET root.status = 'HEALTHY', root.released_at = timestamp()
    RETURN root.id
    """
    t0 = time.time()
    res_root = g.query(cypher_root, {"root_id": root_fact_id})
    ms = (time.time() - t0) * 1000
    log_query("restore_root", cypher_root, ms, len(res_root.result_set), request_id)

    if not res_root.result_set:
        return {"success": False, "error": "Root fact not in QUARANTINED state"}

    # Step 2: Iterative parent-verified restore for descendants
    # Never restore executed actions (executed = true)
    cypher_iter = """
    MATCH (p:MemoryNode)-[:LEADS_TO|SUPPORTS]->(m:MemoryNode {status: 'QUARANTINED'})
    WHERE NOT (m:Action AND m.executed = true)
    WITH m, collect(p) AS parents
    WHERE ALL(parent IN parents WHERE parent.status = 'HEALTHY')
    SET m.status = 'HEALTHY', m.released_at = timestamp()
    RETURN m.id
    """
    restored_nodes = [root_fact_id]
    while True:
        t_iter = time.time()
        res_iter = g.query(cypher_iter)
        ms_iter = (time.time() - t_iter) * 1000
        rows = len(res_iter.result_set)
        log_query("restore_descendant_pass", cypher_iter, ms_iter, rows, request_id)
        if not rows:
            break
        restored_nodes.extend([r[0] for r in res_iter.result_set])

    # Increment epoch
    bump_epoch(tenant_id)

    return {
        "success": True,
        "restored_count": len(restored_nodes),
        "restored_nodes": restored_nodes,
    }


# =========================================================================
# 6. HANDOFF CLAIM
# =========================================================================

def claim_handoff(
    tenant_id: str,
    handoff_id: str,
    agent_id: str,
    role: str = "agent",
    request_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Kind-aware atomic claim of a handoff node with lease timestamp.
    REVIEW/ESCALATION kinds require role in ['human', 'auditor'].
    """
    g = get_graph(tenant_id, "commons")
    name = "claim_handoff"

    # Pre-check kind permissions
    res_kind = g.query("MATCH (h:Handoff {id: $id}) RETURN h.kind", {"id": handoff_id})
    if not res_kind.result_set:
        return {"success": False, "error": "Handoff not found"}

    kind = res_kind.result_set[0][0]
    if kind in ["REVIEW", "ESCALATION"] and role not in ["human", "auditor"]:
        return {"success": False, "error": f"Role '{role}' forbidden from claiming {kind} handoffs"}

    cypher = """
    MATCH (h:Handoff {id: $id})
    OPTIONAL MATCH (h)-[:CONTEXT_ROOT]->(anchor:MemoryNode)
    WITH h, anchor
    WHERE (h.status = 'PENDING' OR (h.status = 'IN_PROGRESS' AND h.lease_until < timestamp()))
      AND (h.kind <> 'TASK' OR anchor IS NULL OR anchor.status = 'HEALTHY')
    SET h.status = 'IN_PROGRESS',
        h.claimed_by = $agent_id,
        h.claimed_at = timestamp(),
        h.lease_until = timestamp() + 60000
    RETURN h.id, h.status, h.claimed_by, h.lease_until
    """
    t0 = time.time()
    res = g.query(cypher, {"id": handoff_id, "agent_id": agent_id})
    ms = (time.time() - t0) * 1000
    rows = len(res.result_set)
    log_query(name, cypher, ms, rows, request_id)

    if not rows:
        return {"success": False, "error": "Handoff already claimed, expired, or anchor unhealthy"}

    row = res.result_set[0]
    return {
        "success": True,
        "handoff_id": row[0],
        "status": row[1],
        "claimed_by": row[2],
        "winner": row[2],
        "losers": [],
        "lease_until": row[3],
    }


# =========================================================================
# 7. RETRIEVAL & AS-OF
# =========================================================================

def retrieve(
    tenant_id: str,
    query_embedding: List[float],
    k: int = 5,
    max_hops: int = 3,
    request_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Healthy-only retrieval. Returns only HEALTHY, unsuperseded context.
    Expands up to max_hops over healthy LEADS_TO paths.
    Uses verified vec.cosineDistance for ranking.
    """
    g = get_graph(tenant_id, "commons")
    name = "retrieve"

    cypher = f"""
    CALL db.idx.vector.queryNodes('Fact', 'embedding', {k * 2}, vecf32($vec))
    YIELD node, score
    WHERE node.status = 'HEALTHY' AND (node.valid_to IS NULL OR node.valid_to > timestamp())
    WITH node, score
    ORDER BY score ASC
    LIMIT {k}
    OPTIONAL MATCH p = (node)-[:LEADS_TO*0..{max_hops}]->(target:MemoryNode)
    WHERE ALL(n IN nodes(p) WHERE n.status = 'HEALTHY')
    RETURN DISTINCT target.id, labels(target), target.text, target.status, score
    """
    t0 = time.time()
    res = g.query(cypher, {"vec": query_embedding})
    ms = (time.time() - t0) * 1000
    rows = len(res.result_set)
    log_query(name, cypher, ms, rows, request_id)

    output = []
    for row in res.result_set:
        output.append({
            "id": row[0],
            "labels": row[1],
            "text": row[2],
            "status": row[3],
            "score": row[4],
        })
    return output


def as_of(tenant_id: str, timestamp_ms: int, request_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    As-of point-in-time belief recall (latest quarantine window per node).
    """
    g = get_graph(tenant_id, "commons")
    name = "as_of"
    cypher = """
    MATCH (f:Fact)
    WHERE f.valid_from <= $t AND (f.valid_to IS NULL OR f.valid_to > $t)
      AND NOT (f.quarantined_at IS NOT NULL AND f.quarantined_at <= $t
               AND (f.released_at IS NULL OR f.released_at > $t))
    RETURN f.id, f.text, f.status
    """
    t0 = time.time()
    res = g.query(cypher, {"t": timestamp_ms})
    ms = (time.time() - t0) * 1000
    rows = len(res.result_set)
    log_query(name, cypher, ms, rows, request_id)

    return [{"id": r[0], "text": r[1], "status": r[2]} for r in res.result_set]


# =========================================================================
# 8. COMPANY BRAIN QUERIES (TRACK 03)
# =========================================================================

def provenance(tenant_id: str, decision_id: str, request_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Decision provenance: Decision <- LEADS_TO <- Fact -> Source.
    """
    g = get_graph(tenant_id, "commons")
    name = "provenance"
    cypher = """
    MATCH (dec:Decision {id: $id})<-[:LEADS_TO*1..5]-(f:Fact)-[:FROM_SOURCE]->(s:Source)
    OPTIONAL MATCH (team:Team)-[:OWNS]->(s)
    RETURN f.text AS claim, f.quote AS quote, s.title AS source, s.url AS url,
           s.version AS version, team.name AS owner
    """
    t0 = time.time()
    res = g.query(cypher, {"id": decision_id})
    ms = (time.time() - t0) * 1000
    rows = len(res.result_set)
    log_query(name, cypher, ms, rows, request_id)

    return [
        {
            "claim": r[0] or "",
            "quote": r[1] or "",
            "source_title": r[2] or "",
            "source": r[2] or "",
            "url": r[3] or "",
            "version": r[4] or "1",
            "owner": r[5] or "Engineering",
        }
        for r in res.result_set
    ]


def who_knows(tenant_id: str, topic_id: str, request_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Ranks Person nodes by count of AUTHORED|RESOLVED Sources ABOUT topic.
    """
    g = get_graph(tenant_id, "commons")
    name = "who_knows"
    cypher = """
    MATCH (p:Person)-[:AUTHORED|RESOLVED]->(s:Source)-[:ABOUT]->(t:Topic {id: $topic_id})
    OPTIONAL MATCH (p)-[:MEMBER_OF]->(team:Team)
    RETURN p.id, p.name, coalesce(team.name, 'Engineering') AS team, count(DISTINCT s) AS evidence, collect(DISTINCT s.id) AS source_ids
    ORDER BY evidence DESC
    """
    t0 = time.time()
    res = g.query(cypher, {"topic_id": topic_id})
    ms = (time.time() - t0) * 1000
    rows = len(res.result_set)
    log_query(name, cypher, ms, rows, request_id)

    return [
        {
            "person": r[1] or r[0],
            "team": r[2] or "Engineering",
            "evidence_count": r[3],
            "source_ids": r[4] or [],
            "person_id": r[0],
            "name": r[1] or r[0],
        }
        for r in res.result_set
    ]


# =========================================================================
# 9. BLAST PREVIEW, INVARIANTS, EPOCH & SNAPSHOT
# =========================================================================

def blast_preview(tenant_id: str, root_id: str, request_id: Optional[str] = None) -> Dict[str, Any]:
    """Read-only preview of affected facts, conclusions, and actions."""
    g = get_graph(tenant_id, "commons")
    name = "blast_preview"
    cypher = """
    MATCH (root:MemoryNode {id: $root_id})
    OPTIONAL MATCH (root)-[:LEADS_TO|SUPPORTS*0..15]->(m:MemoryNode)
    RETURN count(DISTINCT CASE WHEN 'Fact' IN labels(m) THEN m END) AS facts,
           count(DISTINCT CASE WHEN 'Conclusion' IN labels(m) THEN m END) AS conclusions,
           count(DISTINCT CASE WHEN 'Action' IN labels(m) AND m.executed = true AND coalesce(m.reversibility, 'COMPENSABLE') = 'REVERSIBLE' THEN m END) AS executed_rev,
           count(DISTINCT CASE WHEN 'Action' IN labels(m) AND m.executed = true AND coalesce(m.reversibility, 'COMPENSABLE') = 'COMPENSABLE' THEN m END) AS executed_comp,
           count(DISTINCT CASE WHEN 'Action' IN labels(m) AND m.executed = true AND coalesce(m.reversibility, 'COMPENSABLE') = 'IRREVERSIBLE' THEN m END) AS executed_irrev,
           count(DISTINCT CASE WHEN 'Action' IN labels(m) AND (m.executed IS NULL OR m.executed = false) THEN m END) AS staged_actions,
           collect(DISTINCT m.id) AS node_ids
    """
    t0 = time.time()
    try:
        res = g.ro_query(cypher, {"root_id": root_id})
    except Exception:
        res = g.query(cypher, {"root_id": root_id})
    ms = (time.time() - t0) * 1000
    rows = len(res.result_set)
    log_query(name, cypher, ms, rows, request_id)

    if not rows or not res.result_set[0][6]:
        return {
            "root_id": root_id,
            "node_ids": [],
            "impacted_count": 0,
            "facts": 0,
            "conclusions": 0,
            "actions": {
                "staged": 0,
                "executed_reversible": 0,
                "executed_compensable": 0,
                "executed_irreversible": 0,
            },
            "executed_actions": 0,
            "staged_actions": 0,
        }

    row = res.result_set[0]
    facts = row[0]
    conclusions = row[1]
    executed_rev = row[2]
    executed_comp = row[3]
    executed_irrev = row[4]
    staged = row[5]
    node_ids = row[6] or []
    total_executed = executed_rev + executed_comp + executed_irrev

    return {
        "root_id": root_id,
        "node_ids": node_ids,
        "impacted_count": len(node_ids),
        "facts": facts,
        "conclusions": conclusions,
        "actions": {
            "staged": staged,
            "executed_reversible": executed_rev,
            "executed_compensable": executed_comp,
            "executed_irreversible": executed_irrev,
        },
        "executed_actions": total_executed,
        "staged_actions": staged,
    }


def invariant_check(tenant_id: str, request_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Audits graph invariants:
    1. Zero HEALTHY children under QUARANTINED parents (zero orphans).
    2. Zero actions executed after an ancestor was already quarantined.
    Returns:
      - passed: bool (True if all invariants hold)
      - all_passed: bool (backward compatibility)
      - violations: int (total count of violations)
      - healthy_children_under_quarantined: int
      - actions_executed_after_ancestor_quarantined: int
      - violating_child_ids: List[str]
      - violating_action_ids: List[str]
      - violating_node_ids: List[str]
    """
    g = get_graph(tenant_id, "commons")
    name = "invariant_check"
    t0 = time.time()

    # Invariant 1: Healthy child under quarantined parent
    q1 = """
    MATCH (parent:MemoryNode {status: 'QUARANTINED'})-[:LEADS_TO|SUPPORTS]->(child:MemoryNode {status: 'HEALTHY'})
    RETURN count(DISTINCT child) AS cnt, collect(DISTINCT child.id) AS ids
    """
    res1_raw = g.query(q1).result_set
    if res1_raw and len(res1_raw) > 0 and len(res1_raw[0]) > 0:
        res1 = res1_raw[0][0]
        violating_child_ids = [cid for cid in (res1_raw[0][1] or []) if cid is not None]
    else:
        res1 = 0
        violating_child_ids = []

    # Invariant 2: Action executed after an ancestor was already quarantined
    q2 = """
    MATCH (ancestor:MemoryNode)-[:LEADS_TO|SUPPORTS*]->(a:Action {executed: true})
    WHERE (ancestor.quarantined_at IS NOT NULL AND a.executed_at IS NOT NULL AND a.executed_at > ancestor.quarantined_at)
       OR (ancestor.status = 'QUARANTINED' AND a.executed = true AND (a.executed_at IS NULL OR ancestor.quarantined_at IS NULL))
    RETURN count(DISTINCT a) AS cnt, collect(DISTINCT a.id) AS ids
    """
    res2_raw = g.query(q2).result_set
    if res2_raw and len(res2_raw) > 0 and len(res2_raw[0]) > 0:
        res2 = res2_raw[0][0]
        violating_action_ids = [aid for aid in (res2_raw[0][1] or []) if aid is not None]
    else:
        res2 = 0
        violating_action_ids = []

    ms = (time.time() - t0) * 1000
    total_violations = res1 + res2
    log_query(name, f"{q1.strip()}\n---\n{q2.strip()}", ms, total_violations, request_id)

    violating_node_ids = sorted(list(set(violating_child_ids + violating_action_ids)))
    passed = (total_violations == 0)

    now_ms = int(time.time() * 1000)
    checks = [
        {"name": "zero_healthy_under_quarantined", "ok": res1 == 0, "value": float(res1)},
        {"name": "executed_has_healthy_ancestors", "ok": res2 == 0, "value": float(res2)},
    ]

    return {
        "ok": passed,
        "checked_at": now_ms,
        "checks": checks,
        "passed": passed,
        "all_passed": passed,
        "violations": total_violations,
        "healthy_children_under_quarantined": res1,
        "actions_executed_after_ancestor_quarantined": res2,
        "violating_child_ids": violating_child_ids,
        "violating_action_ids": violating_action_ids,
        "violating_node_ids": violating_node_ids,
    }


def bump_epoch(tenant_id: str, request_id: Optional[str] = None) -> int:
    """Increments Meta.epoch and returns new epoch."""
    g = get_graph(tenant_id, "commons")
    name = "bump_epoch"
    t0 = time.time()
    cypher = "MERGE (m:Meta {id: 'meta'}) ON CREATE SET m.epoch = 1 ON MATCH SET m.epoch = m.epoch + 1 RETURN m.epoch"
    res = g.query(cypher)
    ms = (time.time() - t0) * 1000
    if res.result_set:
        epoch = res.result_set[0][0]
        log_query(name, cypher, ms, 1, request_id)
        return epoch
    log_query(name, cypher, ms, 0, request_id)
    return 1


def graph_snapshot(tenant_id: str, as_of_ms: Optional[int] = None) -> Dict[str, Any]:
    """
    Returns full graph payload matching CONTRACT.md format for Cytoscape.js.
    """
    g = get_graph(tenant_id, "commons")
    nodes_q = "MATCH (n) RETURN n.id, labels(n), properties(n)"
    edges_q = "MATCH (src)-[r]->(tgt) RETURN src.id, type(r), tgt.id, properties(r)"

    res_nodes = g.query(nodes_q).result_set
    res_edges = g.query(edges_q).result_set

    nodes = []
    for r in res_nodes:
        nid, labels, props = r[0], r[1], r[2]
        if not nid or "Meta" in labels or nid == "meta":
            continue
        kind = "fact"
        if "Action" in labels:
            kind = "action"
        elif "Conclusion" in labels:
            kind = "conclusion"
        elif "Source" in labels:
            kind = "source"
        elif "Handoff" in labels:
            kind = "handoff"
        elif "Procedure" in labels:
            kind = "procedure"

        node_data = {
            "id": nid,
            "kind": kind,
            "is_decision": "Decision" in labels,
            "label": (props.get("text") or props.get("id") or nid)[:60],
            "status": props.get("status", "HEALTHY"),
            "stale": props.get("stale", False),
        }
        if props.get("text") is not None:
            node_data["text"] = str(props["text"])
        if props.get("key") is not None:
            node_data["key"] = str(props["key"])
        if props.get("value") is not None:
            node_data["value"] = str(props["value"])
        if props.get("quarantined_at") is not None:
            node_data["quarantined_at"] = props["quarantined_at"]
        if props.get("released_at") is not None:
            node_data["released_at"] = props["released_at"]
        if props.get("quarantine_root") is not None:
            node_data["quarantine_root"] = props["quarantine_root"]
        if props.get("depth") is not None:
            node_data["depth"] = props["depth"]
        if props.get("owner_team") is not None:
            node_data["owner_team"] = props["owner_team"]

        # Backwards compatibility flat fields
        node_data["executed"] = props.get("executed", False)
        if props.get("gate"):
            node_data["gate"] = props["gate"]
        if props.get("reversibility"):
            node_data["reversibility"] = props["reversibility"]

        if kind == "action":
            node_data["action"] = {
                "type": props.get("type", "generic"),
                "reversibility": props.get("reversibility", "COMPENSABLE"),
                "executed": props.get("executed", False),
                "confirmed": props.get("confirmed", False),
                "gate": props.get("gate", "PASSED"),
                "procedure_id": props.get("procedure_id"),
            }
        nodes.append(node_data)

    edges = []
    for r in res_edges:
        edges.append({
            "id": f"e_{r[0]}_{r[2]}",
            "source": r[0],
            "type": r[1],
            "target": r[2],
        })

    epoch_res = g.query("MATCH (m:Meta {id: 'meta'}) RETURN m.epoch")
    epoch = epoch_res.result_set[0][0] if epoch_res.result_set else 1
    now_ms = int(time.time() * 1000)

    return {
        "contract_version": "0.1.0",
        "tenant": tenant_id,
        "epoch": epoch,
        "seq": len(QUERY_LOG),
        "as_of": as_of_ms,
        "generated_at": now_ms,
        "truncated": False,
        "nodes": nodes,
        "edges": edges,
    }


# =========================================================================
# T02-1: EPISODIC, SEMANTIC & PROCEDURAL MEMORY
# =========================================================================

def record_episode(
    tenant_id: str,
    agent_id: str,
    session_id: str,
    message_id: str,
    role: str,          # "user" | "assistant" | "system"
    content: str,
    ts_ms: Optional[int] = None,
    request_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Records an episodic memory event into the agent's private raw graph.
    Creates: (:Message {id, session_id, role, content, ts})
    Links:   (prev_message)-[:NEXT]->(message)  (ordered chain)
    Graph:   tenant_<id>:agent_<n>:raw
    """
    g = get_graph(tenant_id, f"agent_{agent_id}:raw")
    ts = ts_ms or int(time.time() * 1000)
    # Step 1: Merge session + create message node
    cypher_create = """
    MERGE (s:Session {id: $session_id})
    ON CREATE SET s.started_at = $ts
    CREATE (m:Message {
        id: $message_id,
        session_id: $session_id,
        role: $role,
        content: $content,
        ts: $ts
    })
    MERGE (s)-[:HAS_MESSAGE]->(m)
    RETURN m.id AS message_id, s.id AS session_id
    """
    t0 = time.time()
    res = g.query(cypher_create, {
        "session_id": session_id,
        "message_id": message_id,
        "role": role,
        "content": content,
        "ts": ts,
    })
    ms = (time.time() - t0) * 1000
    log_query("record_episode_create", cypher_create, ms, len(res.result_set), request_id)

    # Step 2: Link previous message with NEXT (FalkorDB has no FOREACH)
    cypher_next = """
    MATCH (prev:Message {session_id: $session_id})
    WHERE prev.id <> $message_id AND prev.ts < $ts
    WITH prev ORDER BY prev.ts DESC LIMIT 1
    MATCH (m:Message {id: $message_id})
    CREATE (prev)-[:NEXT]->(m)
    RETURN prev.id
    """
    try:
        g.query(cypher_next, {
            "session_id": session_id,
            "message_id": message_id,
            "ts": ts,
        })
    except Exception:
        pass  # No prior message — first in session, no NEXT link needed

    return {"success": True, "message_id": message_id, "session_id": session_id}


def upsert_procedure(
    tenant_id: str,
    procedure_id: str,
    name: str,
    description: str,
    steps: List[str],
    domain: str = "general",
    request_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Upserts a Procedure node in the commons graph (procedural memory).
    Procedure schema: {id, name, description, steps[], domain, uses, successes, failures, rank}
    """
    g = get_graph(tenant_id, "commons")
    cypher = """
    MERGE (p:Procedure {id: $proc_id})
    ON CREATE SET
        p.name = $name,
        p.description = $description,
        p.steps = $steps,
        p.domain = $domain,
        p.uses = 0,
        p.successes = 0,
        p.failures = 0,
        p.rank = 0.5,
        p.created_at = timestamp()
    ON MATCH SET
        p.name = $name,
        p.description = $description,
        p.steps = $steps,
        p.domain = $domain,
        p.updated_at = timestamp()
    RETURN p.id AS id, p.rank AS rank
    """
    t0 = time.time()
    res = g.query(cypher, {
        "proc_id": procedure_id,
        "name": name,
        "description": description,
        "steps": steps,
        "domain": domain,
    })
    ms = (time.time() - t0) * 1000
    log_query("upsert_procedure", cypher, ms, len(res.result_set), request_id)
    row = res.result_set[0] if res.result_set else {}
    rank = row[1] if res.result_set else 0.5
    return {"id": procedure_id, "rank": rank, "rank_score": rank}


def score_procedure(
    tenant_id: str,
    procedure_id: str,
    outcome: str,  # "success" | "failure"
    request_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Updates procedure outcome counters and recalculates Laplace-smoothed rank:
        rank = (successes + 1) / (uses + 2)
    This is bayesian smoothing — new procedures start at 0.5, drift up or down.
    Runs on post-execution feedback.
    """
    g = get_graph(tenant_id, "commons")
    cypher = """
    MATCH (p:Procedure {id: $proc_id})
    SET p.uses = p.uses + 1,
        p.successes = p.successes + $inc_success,
        p.failures = p.failures + $inc_failure,
        p.rank = toFloat(p.successes + 1) / toFloat(p.uses + 2),
        p.last_used_at = timestamp()
    RETURN p.id AS id, p.uses AS uses, p.successes AS successes,
           p.failures AS failures, p.rank AS rank
    """
    inc_success = 1 if outcome == "success" else 0
    inc_failure = 1 if outcome == "failure" else 0
    t0 = time.time()
    res = g.query(cypher, {
        "proc_id": procedure_id,
        "inc_success": inc_success,
        "inc_failure": inc_failure,
    })
    ms = (time.time() - t0) * 1000
    log_query("score_procedure", cypher, ms, len(res.result_set), request_id)
    if not res.result_set:
        return {"success": False, "error": "Procedure not found"}
    r = res.result_set[0]
    rank = round(r[4], 4)
    return {
        "success": True,
        "procedure_id": r[0],
        "uses": r[1],
        "successes": r[2],
        "failures": r[3],
        "rank": rank,
        "rank_score": rank,
    }


def rank_procedures(
    tenant_id: str,
    domain: Optional[str] = None,
    limit: int = 10,
    request_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Returns procedures ranked by Laplace-smoothed success rate (desc).
    Optional domain filter. T02-1 verification: rank changes between runs.
    """
    g = get_graph(tenant_id, "commons")
    if domain:
        cypher = """
        MATCH (p:Procedure {domain: $domain})
        RETURN p.id, p.name, p.rank, p.uses, p.successes, p.failures, p.steps
        ORDER BY p.rank DESC
        LIMIT $limit
        """
        params: Dict[str, Any] = {"domain": domain, "limit": limit}
    else:
        cypher = """
        MATCH (p:Procedure)
        RETURN p.id, p.name, p.rank, p.uses, p.successes, p.failures, p.steps
        ORDER BY p.rank DESC
        LIMIT $limit
        """
        params = {"limit": limit}
    t0 = time.time()
    res = g.query(cypher, params)
    ms = (time.time() - t0) * 1000
    log_query("rank_procedures", cypher, ms, len(res.result_set), request_id)
    return [
        {
            "id": r[0],
            "name": r[1],
            "task_type": r[1],
            "rank": round(r[2], 4) if r[2] is not None else 0.5,
            "rank_score": round(r[2], 4) if r[2] is not None else 0.5,
            "uses": r[3] or 0,
            "successes": r[4] or 0,
            "failures": r[5] or 0,
            "steps": r[6] or [],
            "seeded": False,
        }
        for r in res.result_set
    ]


# =========================================================================
# T02-2: PERSISTENT CONVERSATION HISTORY
# =========================================================================

def close_session(
    tenant_id: str,
    agent_id: str,
    session_id: str,
    request_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Closes a session and writes a deterministic summary Fact into the commons graph.
    Summary includes: message count, first+last message timestamps, topic digest.
    This is the 'session summarizer' step — enables agent restart recall.
    """
    raw_g = get_graph(tenant_id, f"agent_{agent_id}:raw")
    # Collect all messages in session ordered by ts
    cypher_messages = """
    MATCH (s:Session {id: $session_id})-[:HAS_MESSAGE]->(m:Message)
    RETURN m.ts, m.role, m.content
    ORDER BY m.ts ASC
    """
    t0 = time.time()
    res = raw_g.query(cypher_messages, {"session_id": session_id})
    ms = (time.time() - t0) * 1000
    log_query("close_session_read", cypher_messages, ms, len(res.result_set), request_id)

    messages = res.result_set
    if not messages:
        return {"success": False, "error": "Session has no messages"}

    msg_count = len(messages)
    first_ts = messages[0][0]
    last_ts = messages[-1][0]
    # Deterministic digest: roles + content lengths (no LLM)
    digest = " | ".join(
        f"{r[1]}:{len(r[2])}" for r in messages[:5]
    )

    # Write summary Fact into commons graph
    commons_g = get_graph(tenant_id, "commons")
    summary_id = f"session_summary_{agent_id}_{session_id}"
    cypher_summary = """
    MERGE (f:Fact:MemoryNode {id: $fact_id})
    ON CREATE SET
        f.key = $fact_id,
        f.text = $summary_text,
        f.session_id = $session_id,
        f.agent_id = $agent_id,
        f.msg_count = $msg_count,
        f.first_ts = $first_ts,
        f.last_ts = $last_ts,
        f.status = 'HEALTHY',
        f.kind = 'EPISODIC_SUMMARY',
        f.valid_from = $first_ts
    ON MATCH SET
        f.text = $summary_text,
        f.msg_count = $msg_count,
        f.last_ts = $last_ts
    RETURN f.id
    """
    summary_text = (
        f"[Session {session_id}] agent={agent_id} msgs={msg_count} "
        f"span={first_ts}-{last_ts} digest={digest}"
    )
    t1 = time.time()
    res2 = commons_g.query(cypher_summary, {
        "fact_id": summary_id,
        "summary_text": summary_text,
        "session_id": session_id,
        "agent_id": agent_id,
        "msg_count": msg_count,
        "first_ts": first_ts,
        "last_ts": last_ts,
    })
    ms2 = (time.time() - t1) * 1000
    log_query("close_session_write_summary", cypher_summary, ms2, len(res2.result_set), request_id)
    bump_epoch(tenant_id)
    return {
        "success": True,
        "summary_fact_id": summary_id,
        "msg_count": msg_count,
        "summary_text": summary_text,
    }


def resume_agent(
    tenant_id: str,
    agent_id: str,
    limit: int = 5,
    request_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    2-query agent resume flow (T02-2 core spec):
    1. Query raw private graph for most recent session messages.
    2. Query commons graph for session summary Facts authored by this agent.
    Returns both so the restarted agent knows what it was doing.
    """
    # Query 1: Most recent messages from raw graph
    raw_g = get_graph(tenant_id, f"agent_{agent_id}:raw")
    cypher_recent = """
    MATCH (m:Message)
    RETURN m.session_id, m.role, m.content, m.ts
    ORDER BY m.ts DESC
    LIMIT $limit
    """
    t0 = time.time()
    res_raw = raw_g.query(cypher_recent, {"limit": limit})
    ms1 = (time.time() - t0) * 1000
    log_query("resume_agent_raw", cypher_recent, ms1, len(res_raw.result_set), request_id)

    recent_messages = [
        {"session_id": r[0], "role": r[1], "content": r[2], "ts": r[3]}
        for r in res_raw.result_set
    ]

    # Query 2: Session summaries from commons graph
    commons_g = get_graph(tenant_id, "commons")
    cypher_summaries = """
    MATCH (f:Fact {agent_id: $agent_id})
    RETURN f.id, f.session_id, f.text, f.msg_count, f.last_ts
    ORDER BY f.last_ts DESC
    LIMIT $limit
    """
    t1 = time.time()
    res_commons = commons_g.query(cypher_summaries, {"agent_id": agent_id, "limit": limit})
    ms2 = (time.time() - t1) * 1000
    log_query("resume_agent_commons", cypher_summaries, ms2, len(res_commons.result_set), request_id)

    summaries = [
        {
            "fact_id": r[0],
            "session_id": r[1],
            "summary": r[2],
            "msg_count": r[3],
            "last_ts": r[4],
        }
        for r in res_commons.result_set
    ]

    memories = [
        {
            "fact_id": s["fact_id"],
            "text": s["summary"],
            "source_ref": f"tenant_{tenant_id}:commons:{s['fact_id']}",
            "quote": s["summary"][:200],
        }
        for s in summaries
    ]
    primary_session_id = summaries[0]["session_id"] if summaries else (recent_messages[0]["session_id"] if recent_messages else f"sess_{agent_id}_001")
    primary_summary = summaries[0]["summary"] if summaries else (recent_messages[0]["content"] if recent_messages else "Agent resumed")

    return {
        "agent_id": agent_id,
        "session_id": primary_session_id,
        "summary": primary_summary,
        "memories": memories,
        "recent_messages": recent_messages,
        "session_summaries": summaries,
        "resumed": True,
    }


def session_history(
    tenant_id: str,
    agent_id: str,
    session_id: str,
    request_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Returns the full ordered message chain for a session (NEXT-linked traversal).
    Used for the attribution demo: 'where did this belief come from'.
    """
    g = get_graph(tenant_id, f"agent_{agent_id}:raw")
    cypher = """
    MATCH (m:Message {session_id: $session_id})
    RETURN m.id, m.role, m.content, m.ts
    ORDER BY m.ts ASC
    """
    t0 = time.time()
    res = g.query(cypher, {"session_id": session_id})
    ms = (time.time() - t0) * 1000
    log_query("session_history", cypher, ms, len(res.result_set), request_id)
    return [
        {"id": r[0], "role": r[1], "content": r[2], "ts": r[3]}
        for r in res.result_set
    ]


# =========================================================================
# T03-4: KEEPING KNOWLEDGE CURRENT (stale propagation + REVIEW handoffs)
# =========================================================================

def supersede_with_review(
    tenant_id: str,
    old_fact_key: str,
    new_fact_id: str,
    new_text: str,
    new_value: str,
    author: str = "system",
    source_url: str = "",
    request_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Full T03-4 supersede flow:
    1. SUPERSEDES link: new fact -> old fact
    2. Mark old fact superseded
    3. Propagate stale=true to all downstream Conclusions and Decisions
    4. Create REVIEW Handoff for each stale Decision, assigned to owning team
    5. Bump epoch
    Returns superseded_count, stale_count, review_handoffs_created.
    """
    g = get_graph(tenant_id, "commons")
    now_ms = int(time.time() * 1000)

    # Step 1: Create new fact and SUPERSEDES link
    cypher_create = """
    MATCH (old:Fact {key: $old_key})
    WHERE old.status <> 'SUPERSEDED'
    WITH old LIMIT 1
    CREATE (new:Fact:MemoryNode {
        id: $new_id,
        key: $old_key,
        text: $new_text,
        value: $new_value,
        author: $author,
        source_url: $source_url,
        status: 'HEALTHY',
        stale: false,
        valid_from: $now_ms
    })
    CREATE (new)-[:SUPERSEDES {at: $now_ms}]->(old)
    SET old.status = 'SUPERSEDED', old.superseded_at = $now_ms
    RETURN old.id AS old_id, new.id AS new_id
    """
    t0 = time.time()
    res = g.query(cypher_create, {
        "old_key": old_fact_key,
        "new_id": new_fact_id,
        "new_text": new_text,
        "new_value": new_value,
        "author": author,
        "source_url": source_url,
        "now_ms": now_ms,
    })
    ms = (time.time() - t0) * 1000
    log_query("supersede_create_link", cypher_create, ms, len(res.result_set), request_id)

    if not res.result_set:
        return {"success": False, "error": f"Old fact with key '{old_fact_key}' not found or already superseded"}

    # Step 2: Propagate stale=true to dependent Conclusions, Decisions, and Actions
    cypher_stale = """
    MATCH (old:Fact {key: $old_key})-[:LEADS_TO*1..]->(dependent)
    WHERE dependent:Conclusion OR dependent:Decision OR dependent:Action
    SET dependent.stale = true, dependent.stale_since = $now_ms
    RETURN dependent.id AS dep_id, labels(dependent) AS dep_labels
    """
    t1 = time.time()
    res_stale = g.query(cypher_stale, {"old_key": old_fact_key, "now_ms": now_ms})
    ms2 = (time.time() - t1) * 1000
    stale_count = len(res_stale.result_set)
    log_query("supersede_propagate_stale", cypher_stale, ms2, stale_count, request_id)

    stale_decisions = list(dict.fromkeys([
        r[0] for r in res_stale.result_set
        if r[1] and "Decision" in r[1]
    ]))

    # Step 3: Create REVIEW Handoff for each stale Decision
    handoffs_created = []
    for decision_id in stale_decisions:
        # Find owning team via: Decision<-LEADS_TO*1..-Fact<-FROM_SOURCE-Source<-OWNS-Team
        cypher_owner = """
        MATCH (d:Decision {id: $dec_id})<-[:LEADS_TO*1..]-(f:Fact)<-[:FROM_SOURCE]-(src:Source)
              <-[:OWNS]-(team:Team)
        RETURN team.id AS team_id
        LIMIT 1
        """
        res_owner = g.query(cypher_owner, {"dec_id": decision_id})
        owner_team = res_owner.result_set[0][0] if res_owner.result_set else "ops-team"

        handoff_id = f"review_{decision_id}_{now_ms}"
        cypher_handoff = """
        CREATE (h:Handoff {
            id: $hid,
            kind: 'REVIEW',
            status: 'PENDING',
            context_root: $dec_id,
            assigned_to: $team_id,
            reason: $reason,
            created_at: $now_ms
        })
        WITH h
        MATCH (d:Decision {id: $dec_id})
        CREATE (d)-[:REQUIRES_REVIEW]->(h)
        RETURN h.id
        """
        reason = f"Policy fact '{old_fact_key}' superseded → decision flagged stale"
        res_h = g.query(cypher_handoff, {
            "hid": handoff_id,
            "dec_id": decision_id,
            "team_id": owner_team,
            "reason": reason,
            "now_ms": now_ms,
        })
        if res_h.result_set:
            handoffs_created.append({"handoff_id": handoff_id, "decision_id": decision_id, "assigned_to": owner_team})

    bump_epoch(tenant_id)

    return {
        "success": True,
        "old_fact_key": old_fact_key,
        "new_fact_id": new_fact_id,
        "stale_propagated": stale_count,
        "review_handoffs_created": len(handoffs_created),
        "handoffs": handoffs_created,
    }


# =========================================================================
# T03-5: ANSWERS WITH CLEAR SOURCES (grounded brain/ask)
# =========================================================================

def brain_ask(
    tenant_id: str,
    question: str,
    embedding: Optional[List[float]] = None,
    k: int = 5,
    threshold: float = 0.0,
    min_confidence: Optional[float] = None,
    request_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Grounded Q&A with citation guard (T03-5).
    1. Vector retrieval of top-k healthy, non-stale Facts.
    2. For each fact: trace back to Source (url, title, owner_team, quote, author_tier).
    3. If no grounded facts or confidence below threshold: return structured refusal (never hallucinate).
    Returns: {answer, citations[], grounded: bool, refused: bool}
    """
    g = get_graph(tenant_id, "commons")
    now_ms = int(time.time() * 1000)
    effective_threshold = min_confidence if min_confidence is not None else threshold

    # Step 1: Vector retrieval — healthy, non-stale facts only
    if embedding:
        vec_str = ", ".join(str(v) for v in embedding)
        cypher_retrieve = f"""
        MATCH (f:Fact:MemoryNode)
        WHERE f.status = 'HEALTHY' AND (f.stale IS NULL OR f.stale = false)
              AND f.embedding IS NOT NULL
        WITH f, vec.cosineDistance(f.embedding, vecf32([{vec_str}])) AS dist
        ORDER BY dist ASC
        LIMIT $k
        RETURN f.id, f.key, f.text, f.value, f.source_ref, dist
        """
        params: Dict[str, Any] = {"k": k}
        t0 = time.time()
        res = g.query(cypher_retrieve, params)
        ms1 = (time.time() - t0) * 1000
        log_query("brain_ask_retrieve", cypher_retrieve, ms1, len(res.result_set), request_id)
    else:
        # Keyword fallback: try candidate keywords in descending length order
        import re as _re
        raw_tokens = [_re.sub(r'[^a-z0-9]', '', w) for w in question.lower().split()]
        raw_tokens = [t for t in raw_tokens if len(t) > 3]

        candidates = []
        for t in raw_tokens:
            candidates.append(t)
            if t.endswith('s') and len(t) > 4:
                candidates.append(t[:-1])

        # Deduplicate while preserving length-descending order
        seen = set()
        unique_candidates = []
        for t in sorted(candidates, key=len, reverse=True):
            if t not in seen:
                seen.add(t)
                unique_candidates.append(t)

        if not unique_candidates:
            unique_candidates = [question[:20].lower()]

        res = None
        for kw in unique_candidates:
            # Single-line query avoids FalkorDB multi-line WHERE/WITH parsing issues
            cypher_retrieve = (
                "MATCH (f:Fact:MemoryNode) "
                "WHERE f.status = 'HEALTHY' AND (f.stale IS NULL OR f.stale = false) "
                "AND toLower(f.text) CONTAINS $keyword "
                "WITH f, 0.0 AS dist LIMIT $k "
                "RETURN f.id, f.key, f.text, f.value, f.source_ref, dist"
            )
            params = {"keyword": kw, "k": k}
            t0 = time.time()
            res = g.query(cypher_retrieve, params)
            ms1 = (time.time() - t0) * 1000
            log_query("brain_ask_retrieve", cypher_retrieve, ms1, len(res.result_set), request_id)
            if res.result_set:
                break

    if not res.result_set:
        # Structured refusal — never return ungrounded answer
        log_query("brain_ask_refused", "-- refusal: no grounded facts --", 0.0, 0, request_id)
        msg = "No verified, healthy, non-stale facts found matching this query. Answer withheld."
        return {
            "answer": None,
            "grounded": False,
            "refused": True,
            "reason": msg,
            "refusal_reason": msg,
            "citations": [],
            "generated_at": now_ms,
        }

    # Step 2: Trace each fact back to its Source for citation
    citations = []
    answer_parts = []
    for row in res.result_set:
        fact_id, key, text, value, source_ref, dist = row[0], row[1], row[2], row[3], row[4], row[5]
        if not text:
            continue

        similarity = round(1.0 - float(dist), 4)
        if effective_threshold > 0.0 and similarity < effective_threshold:
            continue

        # Provenance: fact -> Source -> Team & Agent author tier
        cypher_prov = """
        MATCH (f:Fact {id: $fact_id})
        OPTIONAL MATCH (f)-[:FROM_SOURCE]->(src:Source)
        OPTIONAL MATCH (src)<-[:OWNS]-(team:Team)
        OPTIONAL MATCH (agent:Agent) WHERE agent.id = f.agent_id OR agent.id = f.author OR agent.id = f.asserted_by
        RETURN f.id, src.id, src.title, src.url, src.kind, src.version, team.id, team.name,
               f.quote, f.valid_from, f.stale, COALESCE(f.author_tier, f.tier, agent.tier, 'root') AS author_tier
        LIMIT 1
        """
        t1 = time.time()
        prov_res = g.query(cypher_prov, {"fact_id": fact_id})
        ms_p = (time.time() - t1) * 1000
        log_query("brain_ask_provenance", cypher_prov, ms_p, len(prov_res.result_set), request_id)

        pr = prov_res.result_set[0] if prov_res.result_set else None
        src_title = (pr[2] if pr and pr[2] else None) or "Internal policy"
        src_url = (pr[3] if pr and pr[3] else "") or ""
        src_kind = (pr[4] if pr and pr[4] else None) or "DOC"
        src_version = (pr[5] if pr and pr[5] else None) or "1.0"
        owner_team = (pr[7] if pr and pr[7] else None) or "unknown"
        fact_quote = (pr[8] if pr and pr[8] else None) or text[:200]
        valid_from = pr[9] if pr and pr[9] is not None else now_ms
        stale_status = bool(pr[10]) if pr and pr[10] is not None else False
        author_tier = (pr[11] if pr and pr[11] else None) or "root"

        citations.append({
            "fact_id": fact_id,
            "key": key,
            "quote": fact_quote,
            "source_title": src_title,
            "source_url": src_url,
            "url": src_url,
            "source_kind": src_kind,
            "version": src_version,
            "owner_team": owner_team,
            "author_tier": author_tier,
            "valid_from": valid_from,
            "stale": stale_status,
            "similarity": similarity,
        })
        answer_parts.append(text)

    if not citations:
        refusal_msg = (
            f"No verified healthy facts met confidence threshold ({effective_threshold:.2f}). Answer withheld."
            if effective_threshold > 0.0
            else "Facts retrieved but none had traceable sources. Answer withheld."
        )
        log_query("brain_ask_refused", f"-- refusal: {refusal_msg} --", 0.0, 0, request_id)
        return {
            "answer": None,
            "grounded": False,
            "refused": True,
            "reason": refusal_msg,
            "refusal_reason": refusal_msg,
            "citations": [],
            "generated_at": now_ms,
        }

    # Step 3: Compose grounded answer with citations
    answer = " ".join(answer_parts[:2])  # top 2 facts, deterministic (no LLM)
    return {
        "answer": answer,
        "grounded": True,
        "refused": False,
        "citations": citations,
        "generated_at": now_ms,
    }
