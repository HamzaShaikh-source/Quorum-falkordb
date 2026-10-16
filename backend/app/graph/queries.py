import time
import threading
from collections import deque
from typing import Dict, List, Optional, Any, Sequence
from pydantic import BaseModel
from fastapi import HTTPException
from app.models.common import Status, NodeKind, EdgeType, Tier, Reversibility, GateStatus, CompensationStatus
from app.models.graph import GraphNode, GraphEdge, GraphResponse
from app.models.handoffs import Handoff
from app.config import settings
from app.graph.candidates import NodeRegistry


class CypherLogEntry(BaseModel):
    ts: int
    request_id: Optional[str] = None
    name: str
    cypher: str
    ms: float
    rows: int
    origin: str = "illustrative"
    outcome: str = "success"
    timing_boundary: str = "client_round_trip"


class GraphStore:
    """
    Thread-safe in-memory graph store implementing the interface contract expected from Person 1 (graph/queries.py).
    Can be swapped or wired to live FalkorDB queries.
    """
    def __init__(self, tenant: str = "acme"):
        self.tenant = tenant
        self._lock = threading.RLock()
        self.nodes = NodeRegistry()
        self.baseline_cache: Dict[Any, int] = {}
        self.edges: List[GraphEdge] = []
        self.epoch: int = 1
        self.query_log: List[CypherLogEntry] = []
        # Handoffs belong to the tenant's graph, like everything else.
        self.handoffs: List[Handoff] = []
        # One lock order for graph, workspace authorization, persistence and handoff claims.
        self.handoff_lock = self._lock
        self.persister: Optional[Any] = None  # set by get_graph_store when persistence is FalkorDB
        from app.services.workspace import default_workspace

        self.workspace: Dict[str, Any] = default_workspace(tenant)
        # Every MCP tool call, newest last. Presence, the feed, chats and token savings are all derived from this.
        self.calls: List[Dict[str, Any]] = []
        self.call_seq = 0
        # What each agent has already been handed, so delta() can say what changed since its last read.
        self.agent_state: Dict[str, Dict[str, Any]] = {}

    MAX_CALLS = 5000

    def record_call(self, rec: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            self.call_seq += 1
            rec = {**rec, "id": self.call_seq, "ts": int(time.time() * 1000)}
            self.calls.append(rec)
            if len(self.calls) > self.MAX_CALLS:
                del self.calls[: len(self.calls) - self.MAX_CALLS]
            return rec

    def add_handoff(self, handoff: Handoff) -> Handoff:
        with self.handoff_lock:
            self.handoffs.append(handoff)
        return handoff

    def commit(self, request_id: Optional[str] = None) -> int:
        """Persist what changed. Called at the end of every API request and tool call, and by the background flusher."""
        if self.persister is None:
            return 0
        return self.persister.sync(request_id)

    def log_query(self, name: str, cypher: str, ms: float, rows: int, request_id: Optional[str] = None, real: bool = False, timing_boundary: str = "client_round_trip", outcome: str = "success"):
        # With FalkorDB behind the store the log shows only Cypher that actually ran (persistence passes real=True).
        if self.persister is not None and not real:
            return
        entry = CypherLogEntry(
            ts=int(time.time() * 1000),
            request_id=request_id,
            name=name,
            cypher=cypher,
            ms=ms,
            rows=rows,
            origin="falkordb" if real else "memory",
            timing_boundary=timing_boundary if real else "in_process",
            outcome=outcome,
        )
        with self._lock:
            self.query_log.append(entry)
            if len(self.query_log) > 200:
                self.query_log.pop(0)

    def bump_epoch(self, request_id: Optional[str] = None) -> int:
        with self._lock:
            start = time.time()
            self.epoch = (self.epoch or 0) + 1
            self.log_query("bump_epoch", "MATCH (m:Meta) SET m.epoch = m.epoch + 1 RETURN m.epoch", (time.time() - start) * 1000, 1, request_id)
            return self.epoch

    def add_node(self, node: GraphNode, request_id: Optional[str] = None):
        with self._lock:
            start = time.time()
            existing = self.nodes.get(node.id)
            if existing is not None:
                if existing.model_dump() == node.model_dump():
                    return  # exact retry, including status, provenance and scope
                raise HTTPException(409, {"error": {"code": "CONFLICT", "message": "Node ID already exists; use an authorized restore or a new version ID."}})
            self.nodes[node.id] = node
            self.log_query("add_node", f"CREATE (n:{node.kind.value.capitalize()} {{id:'{node.id}'}})", (time.time() - start) * 1000, 1, request_id)

    def add_edge(self, edge: GraphEdge, request_id: Optional[str] = None):
        with self._lock:
            start = time.time()
            if any(e.id == edge.id for e in self.edges):
                if any(e == edge for e in self.edges):
                    return
                raise HTTPException(409, "Edge ID already exists")
            if edge.type in (EdgeType.LEADS_TO, EdgeType.SUPPORTS, EdgeType.FROM_SOURCE):
                if edge.source == edge.target:
                    raise HTTPException(409, "Causal links must form a DAG")
                causal_types = {EdgeType.LEADS_TO.value, EdgeType.SUPPORTS.value, EdgeType.FROM_SOURCE.value}
                q = [edge.target]
                visited = {edge.target}
                while q:
                    curr = q.pop(0)
                    if curr == edge.source:
                        raise HTTPException(409, "Causal links must form a DAG")
                    for e in self.edges:
                        if e.source == curr and e.type.value in causal_types and e.target not in visited:
                            visited.add(e.target)
                            q.append(e.target)
            self.edges.append(edge)
            self.log_query("add_edge", f"MATCH (a), (b) CREATE (a)-[:{edge.type.value}]->(b)", (time.time() - start) * 1000, 1, request_id)

    def get_parents(self, node_id: str, edge_types: Optional[Sequence[str]] = None) -> List[GraphNode]:
        """Returns direct parents of node_id along incoming edges."""
        types = list(edge_types) if edge_types is not None else ["LEADS_TO", "SUPPORTS"]
        if not types:
            return []
        with self._lock:
            incoming = [e for e in self.edges if e.target == node_id and e.type.value in types]
            parents = []
            for e in incoming:
                p = self.nodes.get(e.source)
                if p:
                    parents.append(p)
            return parents

    def get_ancestors(self, node_id: str, edge_types: Optional[Sequence[str]] = None) -> List[GraphNode]:
        """Traverses backwards from node_id along incoming edges of given types."""
        types = list(edge_types) if edge_types is not None else ["LEADS_TO", "SUPPORTS"]
        if not types:
            return []
        with self._lock:
            if self.persister is not None:
                try:
                    self.persister.sync()
                    from app.persistence import _label
                    rel_pattern = "|".join(_label(t) for t in types)
                    cypher = f"MATCH (d:QNode {{id: $node_id}})<-[:{rel_pattern}*1..]-(root:QNode) RETURN DISTINCT root.id AS id"
                    res = self.persister._q("get_ancestors", cypher, {"node_id": node_id})
                    ids = [row[0] for row in (res.result_set or [])]
                    return [self.nodes[i] for i in ids if i in self.nodes]
                except Exception:
                    pass

            visited = set()
            queue = [node_id]
            ancestors = []

            while queue:
                curr = queue.pop(0)
                incoming = [e for e in self.edges if e.target == curr and e.type.value in types]
                for edge in incoming:
                    parent_id = edge.source
                    if parent_id not in visited and parent_id != node_id:
                        visited.add(parent_id)
                        parent = self.nodes.get(parent_id)
                        if parent:
                            ancestors.append(parent)
                            queue.append(parent_id)

            return ancestors

    def get_descendants(self, node_id: str, edge_types: Optional[Sequence[str]] = None) -> List[GraphNode]:
        """Traverses forwards from node_id along outgoing edges of given types."""
        types = list(edge_types) if edge_types is not None else ["LEADS_TO", "SUPPORTS"]
        if not types:
            return []
        with self._lock:
            if self.persister is not None:
                try:
                    self.persister.sync()
                    from app.persistence import _label
                    rel_pattern = "|".join(_label(t) for t in types)
                    cypher = f"MATCH (root:QNode {{id: $node_id}})-[:{rel_pattern}*1..]->(d:QNode) RETURN DISTINCT d.id AS id"
                    res = self.persister._q("get_descendants", cypher, {"node_id": node_id})
                    ids = [row[0] for row in (res.result_set or [])]
                    return [self.nodes[i] for i in ids if i in self.nodes]
                except Exception:
                    pass

            visited = set()
            queue = [node_id]
            descendants = []

            while queue:
                curr = queue.pop(0)
                outgoing = [e for e in self.edges if e.source == curr and e.type.value in types]
                for edge in outgoing:
                    child_id = edge.target
                    if child_id not in visited and child_id != node_id:
                        visited.add(child_id)
                        child = self.nodes.get(child_id)
                        if child:
                            descendants.append(child)
                            queue.append(child_id)

            return descendants

    def get_leaf_facts(self, node_id: str) -> List[GraphNode]:
        """Finds all leaf ancestor Facts (ancestor Facts that have no incoming LEADS_TO edges)."""
        with self._lock:
            start = time.time()
            ancestors = self.get_ancestors(node_id, edge_types=["LEADS_TO"])
            facts = [n for n in ancestors if n.kind == NodeKind.FACT]

            leaf_facts = []
            for fact in facts:
                has_incoming_leads_to = any(e for e in self.edges if e.target == fact.id and e.type == EdgeType.LEADS_TO)
                if not has_incoming_leads_to:
                    leaf_facts.append(fact)

            self.log_query(
                "get_leaf_facts",
                "MATCH (leaf:Fact)-[:LEADS_TO*]->(a:Action {id:$id}) WHERE NOT (()-[:LEADS_TO]->(leaf)) RETURN DISTINCT leaf",
                (time.time() - start) * 1000,
                len(leaf_facts),
            )
            return leaf_facts

    def execute_guard(self, action_id: str, request_id: Optional[str] = None) -> bool:
        """
        Atomic execute guard statement:
        Ensures action is HEALTHY, executed=false, gate=PASSED, and 0 ancestors/supporters are non-healthy.
        Atomic check-and-set wrapped in lock.
        """
        with self._lock:
            start = time.time()
            cypher = (
                "MATCH (a:Action {id:$id}) WHERE a.status='HEALTHY' AND a.executed=false AND a.gate='PASSED' "
                "OPTIONAL MATCH (x:MemoryNode)-[:LEADS_TO|SUPPORTS*]->(a) WHERE coalesce(x.status,'MISSING') <> 'HEALTHY' "
                "WITH a, count(DISTINCT x) AS bad WHERE bad = 0 "
                "SET a.executed = true, a.executed_at = timestamp(), a.confirmed = false RETURN a.id"
            )
            action = self.nodes.get(action_id)
            if not action or action.kind != NodeKind.ACTION:
                self.log_query("execute_guard", cypher, (time.time() - start) * 1000, 0, request_id)
                return False

            if action.status != Status.HEALTHY or not action.action:
                self.log_query("execute_guard", cypher, (time.time() - start) * 1000, 0, request_id)
                return False

            if action.action.executed or action.action.gate != GateStatus.PASSED:
                self.log_query("execute_guard", cypher, (time.time() - start) * 1000, 0, request_id)
                return False

            ancestors = self.get_ancestors(action_id, edge_types=["LEADS_TO", "SUPPORTS", "FROM_SOURCE"])
            bad_ancestors = [x for x in ancestors if x.status != Status.HEALTHY or getattr(x, "stale", False)]

            if bad_ancestors:
                self.log_query("execute_guard", cypher, (time.time() - start) * 1000, 0, request_id)
                return False

            # Atomic check-and-set succeeds
            action.action.executed = True
            action.action.confirmed = False
            action.action.executed_at = int(time.time() * 1000)
            self.log_query("execute_guard", cypher, (time.time() - start) * 1000, 1, request_id)
            return True

    def cascade_quarantine(self, root_id: str, request_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Quarantine cascade over LEADS_TO|SUPPORTS*.
        Returns list of {id, status, depth, root_id}.
        """
        with self._lock:
            start = time.time()
            root = self.nodes.get(root_id)
            if not root:
                return []

            now_ms = int(time.time() * 1000)

            # One process owns traversal; the auditor commits status and compensation intent together.
            cypher = (
                "MATCH (root:Fact {id:$root_id}) "
                "OPTIONAL MATCH (root)-[:LEADS_TO|SUPPORTS*]->(descendant) "
                "SET descendant.status='QUARANTINED', descendant.quarantined_at=timestamp(), descendant.quarantine_root=$root_id "
                "RETURN descendant, depth"
            )

            root.status = Status.QUARANTINED
            root.quarantined_at = now_ms
            root.released_at = None
            root.quarantine_root = root_id
            root.depth = 0

            affected = [{"id": root.id, "status": Status.QUARANTINED, "depth": 0, "root_id": root_id}]

            # BFS for descendants to compute exact hop depth
            queue = deque([(root_id, 0)])
            visited = {root_id}
            outgoing_by_id: Dict[str, List[GraphEdge]] = {}
            for edge in self.edges:
                if edge.type in (EdgeType.LEADS_TO, EdgeType.SUPPORTS, EdgeType.FROM_SOURCE):
                    outgoing_by_id.setdefault(edge.source, []).append(edge)

            while queue:
                curr_id, curr_depth = queue.popleft()
                outgoing = outgoing_by_id.get(curr_id, [])
                for edge in outgoing:
                    child_id = edge.target
                    if child_id not in visited:
                        visited.add(child_id)
                        child = self.nodes.get(child_id)
                        if child:
                            child.status = Status.QUARANTINED
                            child.quarantined_at = now_ms
                            child.released_at = None
                            child.quarantine_root = root_id
                            child.depth = curr_depth + 1
                            affected.append({
                                "id": child.id,
                                "status": Status.QUARANTINED,
                                "depth": curr_depth + 1,
                                "root_id": root_id,
                            })
                            queue.append((child_id, curr_depth + 1))

            self.bump_epoch(request_id)
            self.log_query("cascade_quarantine", cypher, (time.time() - start) * 1000, len(affected), request_id)
            return affected

    def restore_node(self, root_id: str, request_id: Optional[str] = None) -> List[str]:
        """
        Restores root fact, then recursively restores descendants ONLY if ALL their parents
        (LEADS_TO, SUPPORTS, and FROM_SOURCE) are HEALTHY. NEVER restores executed actions.
        Increments Meta.epoch.
        """
        with self._lock:
            start = time.time()
            now_ms = int(time.time() * 1000)
            root = self.nodes.get(root_id)
            if not root or root.status != Status.QUARANTINED:
                return []
            if root.action and root.action.executed:
                return []

            causal_edge_types = ["LEADS_TO", "SUPPORTS", "FROM_SOURCE"]

            # Root invariant check: root cannot be restored if any of its incoming parents are not HEALTHY
            incoming_to_root = [e for e in self.edges if e.target == root_id and e.type.value in causal_edge_types]
            if incoming_to_root:
                for e in incoming_to_root:
                    p = self.nodes.get(e.source)
                    if not p or p.status != Status.HEALTHY:
                        return []

            restored = []

            # 1. Restore root
            root.status = Status.HEALTHY
            root.released_at = now_ms
            restored.append(root.id)

            # 2. Iterative propagation until fixed point
            changed = True
            while changed:
                changed = False
                for node in self.nodes.values():
                    if node.status != Status.QUARANTINED or node.id in restored:
                        continue

                    # NEVER restore executed actions
                    if node.kind == NodeKind.ACTION and node.action and node.action.executed:
                        continue

                    # Check parents across all causal derivation edges
                    in_edges = [e for e in self.edges if e.target == node.id and e.type.value in causal_edge_types]
                    if in_edges and all(e.source in self.nodes and self.nodes[e.source].status == Status.HEALTHY for e in in_edges):
                        node.status = Status.HEALTHY
                        node.released_at = now_ms
                        restored.append(node.id)
                        changed = True

            self.bump_epoch(request_id)
            self.log_query("restore_node", "MATCH (r) ... RESTORE IF ALL PARENTS HEALTHY", (time.time() - start) * 1000, len(restored), request_id)
            return restored

    def get_as_of(self, t: int) -> List[GraphNode]:
        """
        As-of historical belief recall (Section 5.11):
        Nodes valid at t, excluding those where latest quarantine window covers t.
        """
        with self._lock:
            results = []
            for n in self.nodes.values():
                # Validity window
                valid_from = n.valid_from or 0
                valid_to = n.valid_to
                if valid_from > t or (valid_to is not None and valid_to <= t):
                    continue

                # Quarantine window check
                if n.quarantined_at is not None and n.quarantined_at <= t:
                    if n.released_at is None or n.released_at > t:
                        continue  # Was quarantined at time t

                results.append(n)
            return results

    def check_invariants(self) -> Dict[str, Any]:
        """
        Runs live invariant integrity checks on the graph:
        1. zero_healthy_under_quarantined: No healthy node may have a quarantined parent.
        2. dag_acyclicity: Graph must remain a valid DAG with 0 cycles.
        3. executed_has_healthy_ancestors: No healthy executed action may have quarantined ancestors.
        """
        with self._lock:
            # 1. Zero healthy under quarantined (causal derivation edges only)
            healthy_under_quarantined = 0
            for edge in self.edges:
                if edge.type.value in ["LEADS_TO", "SUPPORTS", "FROM_SOURCE"]:
                    src = self.nodes.get(edge.source)
                    tgt = self.nodes.get(edge.target)
                    if src and tgt and src.status == Status.QUARANTINED and tgt.status == Status.HEALTHY:
                        healthy_under_quarantined += 1

            # 2. DAG acyclicity (cycle detection over LEADS_TO / SUPPORTS / FROM_SOURCE)
            adj: Dict[str, List[str]] = {}
            for edge in self.edges:
                if edge.type.value in ["LEADS_TO", "SUPPORTS", "FROM_SOURCE"]:
                    adj.setdefault(edge.source, []).append(edge.target)

            visited: Dict[str, int] = {}  # 0: unvisited, 1: visiting, 2: visited
            has_cycle = False

            for nid in list(set(self.nodes.keys()) | set(adj.keys()) | {e.target for e in self.edges}):
                if visited.get(nid, 0) == 0:
                    stack = [(nid, 0)]
                    while stack:
                        curr, idx = stack[-1]
                        if visited.get(curr, 0) == 0:
                            visited[curr] = 1
                        neighbors = adj.get(curr, [])
                        if idx < len(neighbors):
                            nxt = neighbors[idx]
                            stack[-1] = (curr, idx + 1)
                            if visited.get(nxt) == 1:
                                has_cycle = True
                                break
                            elif visited.get(nxt, 0) == 0:
                                stack.append((nxt, 0))
                        else:
                            visited[curr] = 2
                            stack.pop()
                    if has_cycle:
                        break

            # 3. Executed actions have healthy ancestors
            bad_ancestors_count = 0
            for node in self.nodes.values():
                if node.kind == NodeKind.ACTION and node.action and node.action.executed and node.status == Status.HEALTHY:
                    ancestors = self.get_ancestors(node.id, edge_types=["LEADS_TO", "SUPPORTS", "FROM_SOURCE"])
                    if any(a.status == Status.QUARANTINED for a in ancestors):
                        bad_ancestors_count += 1

            checks = [
                {"name": "zero_healthy_under_quarantined", "ok": healthy_under_quarantined == 0, "value": float(healthy_under_quarantined)},
                {"name": "dag_acyclicity", "ok": not has_cycle, "value": 1.0 if has_cycle else 0.0},
                {"name": "executed_has_healthy_ancestors", "ok": bad_ancestors_count == 0, "value": float(bad_ancestors_count)},
            ]
            all_ok = all(c["ok"] for c in checks)
            return {"ok": all_ok, "checks": checks}


# Global store instance per tenant
_stores: Dict[str, GraphStore] = {}
_stores_lock = threading.Lock()
_missing_tenants: Dict[str, float] = {}
_missing_lock = threading.Lock()


def get_graph_store(tenant: str = "acme") -> GraphStore:
    with _stores_lock:
        store = _stores.get(tenant)
        if store is not None:
            return store
        store = GraphStore(tenant)
        if settings.PERSISTENCE == "falkordb":
            from app import db
            from app.persistence import Persister

            db.require_db()  # raises FalkorUnavailable: no store is created, nothing is cached
            store.persister = Persister(store)
            if not store.persister.load():
                store.persister.sync()
        _stores[tenant] = store
        with _missing_lock:
            _missing_tenants.pop(tenant, None)
        return store


def tenant_exists(tenant: str) -> bool:
    """Whether a tenant already has a graph, without creating one. Token checks use this so a forged token cannot mint tenants."""
    with _stores_lock:
        if tenant in _stores:
            return True
    if settings.PERSISTENCE != "falkordb":
        return False
    import time
    now = time.monotonic()
    with _missing_lock:
        last = _missing_tenants.get(tenant)
        if last is not None and now - last < 30.0:
            return False
    from app import db

    db.require_db()
    exists = f"quorum:{tenant}" in db.get_client().list_graphs()
    with _missing_lock:
        if not exists:
            _missing_tenants[tenant] = now
            if len(_missing_tenants) > 5000:
                cutoff = now - 60.0
                for k in [k for k, t in _missing_tenants.items() if t < cutoff]:
                    del _missing_tenants[k]
                if len(_missing_tenants) > 5000:
                    sorted_keys = sorted(_missing_tenants.keys(), key=lambda k: _missing_tenants[k])
                    for k in sorted_keys[:2500]:
                        _missing_tenants.pop(k, None)
        else:
            _missing_tenants.pop(tenant, None)
    return exists


def sync_all(request_id: Optional[str] = None) -> int:
    """Persist every tenant that has been touched. Raises if FalkorDB is down."""
    with _stores_lock:
        stores = list(_stores.values())
    return sum(s.commit(request_id) for s in stores)


def forget_stores() -> None:
    """Drop the in-memory view (not the data). The next access reloads from FalkorDB, like a restart."""
    with _stores_lock:
        _stores.clear()
    with _missing_lock:
        _missing_tenants.clear()
