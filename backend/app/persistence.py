"""
Write-through persistence of a tenant's graph into FalkorDB.

FalkorDB is the system of record. Each tenant has a graph named quorum:<tenant> holding:
  (:QNode:<Kind> {id, kind, label, status, stale, depth, valid_from, json})   the beliefs
  (a)-[:LEADS_TO|SUPPORTS|CONTRADICTS|SUPERSEDES|FROM_SOURCE {id}]->(b)       the causal links
  (:QHandoff {id, kind, status, json})                                        handoffs
  (:QMeta {id:'meta', epoch})                                                 the context epoch

Hot-path logic (cascade, guards, gate) still runs in the API process under locks, then `sync()` writes every change
that happened since the last sync. `load()` rebuilds the in-memory view when a tenant is first used.
A single QCheckpoint stores authoritative recovery state; QNode/edge projections are replayed after an interrupted sync.
The console distinguishes real statements from in-process operations.
"""
import hashlib
import json
import re
import time
from typing import Any, Dict, List, Optional

from app import db
from app.models.graph import GraphEdge, GraphNode
from app.models.handoffs import Handoff

_SAFE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _label(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_]", "", s or "")
    return s if _SAFE.match(s) else "Other"


def _h(js: str) -> str:
    return hashlib.blake2b(js.encode("utf8"), digest_size=12).hexdigest()


class Persister:
    def __init__(self, store):
        self.store = store
        self.checkpoint: Optional[str] = None
        self.nodes: Dict[str, str] = {}
        self.edges: Dict[str, str] = {}
        self.handoffs: Dict[str, str] = {}
        self.epoch: Optional[int] = None
        self.workspace: Optional[str] = None
        self.calls: Dict[int, str] = {}
        self.agents: Dict[str, str] = {}
        self.emb: Dict[str, str] = {}  # node id -> digest of the text its stored vector was computed from

    @property
    def graph(self):
        return db.graph_for(self.store.tenant)

    def _q(self, name: str, cypher: str, params: Dict[str, Any], request_id: Optional[str] = None):
        t0 = time.perf_counter()
        try:
            res = self.graph.query(cypher, params)
        except Exception:
            self.store.log_query(name, cypher, (time.perf_counter() - t0) * 1000, 0, request_id, real=True, outcome="failure")
            raise
        rows = len(res.result_set) if res.result_set else 0
        written = (res.nodes_created or 0) + (res.properties_set or 0) + (res.relationships_created or 0) + (res.nodes_deleted or 0)
        server_ms = getattr(res, "run_time_ms", None)
        measured = server_ms if isinstance(server_ms, (int, float)) else (time.perf_counter() - t0) * 1000
        self.store.log_query(name, cypher.strip().replace("\n", " "), measured, rows or written, request_id,
                             real=True, timing_boundary="server_execution" if isinstance(server_ms, (int, float)) else "client_round_trip")
        return res

    def ensure_schema(self) -> None:
        g = self.graph
        for stmt in (
            "CREATE INDEX FOR (n:QNode) ON (n.id)",
            "CREATE INDEX FOR (n:QNode) ON (n.status)",
            "CREATE INDEX FOR (h:QHandoff) ON (h.id)",
            "CREATE INDEX FOR (m:QMeta) ON (m.id)",
            "CREATE INDEX FOR (c:QCall) ON (c.id)",
            "CREATE INDEX FOR (a:QAgent) ON (a.id)",
        ):
            try:
                g.query(stmt)
            except Exception:
                pass  # already exists
        from app.services import embeddings

        if embeddings.provider_name() == "local":
            try:
                g.query(f"CREATE VECTOR INDEX FOR (n:QNode) ON (n.embedding) OPTIONS {{dimension:{embeddings.DIM}, similarityFunction:'cosine'}}")
            except Exception:
                pass  # already exists

    # ---------- load ----------
    def load(self) -> bool:
        """Rebuild the store from FalkorDB. Returns True if the tenant already had data."""
        self.ensure_schema()
        store = self.store
        checkpoint = self.graph.query("MATCH (s:QCheckpoint {id:'state'}) RETURN s.json").result_set
        if checkpoint:
            state = json.loads(checkpoint[0][0])
            if state.get("version") != 1:
                raise ValueError("Unsupported recovery checkpoint version")
            with store._lock:
                store.nodes.clear()
                for js in state["nodes"]:
                    node = GraphNode.model_validate_json(js)
                    store.nodes[node.id] = node
                store.edges = [GraphEdge.model_validate_json(js) for js in state["edges"]]
                store.handoffs = [Handoff.model_validate_json(js) for js in state["handoffs"]]
                store.epoch = state["epoch"]
                store.workspace = state["workspace"]
                store.calls = state["calls"]
                store.call_seq = max((c["id"] for c in store.calls), default=0)
                store.agent_state = state["agent_state"]
                self.checkpoint = _h(checkpoint[0][0])
                # Compare projections too: a crash may have interrupted their sync.
                self.nodes = {row[0]: "repair" for row in self.graph.query("MATCH (n:QNode) RETURN n.id").result_set}
                self.edges = {row[0]: "repair" for row in self.graph.query("MATCH ()-[r]->() WHERE r.id IS NOT NULL RETURN r.id").result_set}
                self.handoffs = {row[0]: "repair" for row in self.graph.query("MATCH (h:QHandoff) RETURN h.id").result_set}
                self.calls = {row[0]: "repair" for row in self.graph.query("MATCH (c:QCall) RETURN c.id").result_set}
                self.agents = {row[0]: "repair" for row in self.graph.query("MATCH (a:QAgent) RETURN a.id").result_set}
                from app.services import embeddings
                for node_id, vector, digest in self.graph.query("MATCH (n:QNode) RETURN n.id, n.embedding, n.emb").result_set:
                    node = store.nodes.get(node_id)
                    if node is not None and vector and digest == embeddings.digest_for(node):
                        embeddings.seed_cache(node_id, digest, vector)
                        self.emb[node_id] = digest
            return True
        nodes = self.graph.query("MATCH (n:QNode) RETURN n.json, n.embedding, n.emb").result_set
        edges = self.graph.query("MATCH (:QNode)-[r]->(:QNode) WHERE r.id IS NOT NULL RETURN r.json").result_set
        hands = self.graph.query("MATCH (h:QHandoff) RETURN h.json").result_set
        meta = self.graph.query("MATCH (m:QMeta {id:'meta'}) RETURN m.epoch").result_set
        ws = self.graph.query("MATCH (w:QWorkspace {id:'ws'}) RETURN w.json").result_set
        calls = self.graph.query("MATCH (c:QCall) RETURN c.json ORDER BY c.id").result_set
        agents = self.graph.query("MATCH (a:QAgent) RETURN a.id, a.json").result_set
        from app.services import embeddings

        with store._lock:
            store.nodes.clear()
            store.edges.clear()
            store.handoffs.clear()
            for js, vec, dig in nodes:
                n = GraphNode.model_validate_json(js)
                if n.action and n.action.executed and "external_key" not in (json.loads(js).get("action") or {}):
                    # Pre-migration records were delivered with an unnamespaced key. Never change it on recovery.
                    n.action.external_key = n.action.idempotency_key or f"key_{n.id}"
                store.nodes[n.id] = n
                self.nodes[n.id] = _h(js)
                if vec and dig and dig == embeddings.digest_for(n):
                    embeddings.seed_cache(n.id, dig, vec)  # the stored vector is current: no recompute after a restart
                    self.emb[n.id] = dig
            for (js,) in edges:
                e = GraphEdge.model_validate_json(js)
                store.edges.append(e)
                self.edges[e.id] = _h(js)
            for (js,) in hands:
                h = Handoff.model_validate_json(js)
                store.handoffs.append(h)
                self.handoffs[h.id] = _h(js)
            if meta and meta[0][0] is not None:
                store.epoch = int(meta[0][0])
                self.epoch = store.epoch
            if ws and ws[0][0]:
                store.workspace = json.loads(ws[0][0])
                self.workspace = _h(ws[0][0])
            store.calls.clear()
            for (js,) in calls:
                c = json.loads(js)
                store.calls.append(c)
                self.calls[c["id"]] = _h(js)
            store.call_seq = max([c["id"] for c in store.calls], default=0)
            store.agent_state.clear()
            for aid, js in agents:
                d = json.loads(js)
                store.agent_state[aid] = {"ids": list(d.get("ids", [])), "query": d.get("query", ""), "epoch": d.get("epoch", 0)}
                self.agents[aid] = _h(js)
        return bool(nodes or edges or hands or meta or ws or calls or agents)

    # ---------- sync ----------
    def sync(self, request_id: Optional[str] = None) -> int:
        """Atomically checkpoint all recovery state, then update graph projections.

        The one QCheckpoint write contains quarantine + compensation, execution
        intent + key, leases, epoch and handoffs. Projection writes are independent,
        replayable writes, NOT a transaction. A projection error is still an error
        to the caller; restart loads the authoritative checkpoint and repairs the
        projections before successful subsequent commits.
        """
        store = self.store
        with store._lock:
            state = json.dumps({
                "version": 1,
                "nodes": [n.model_dump_json() for n in store.nodes.values()],
                "edges": [e.model_dump_json() for e in store.edges],
                "handoffs": [h.model_dump_json() for h in store.handoffs],
                "epoch": store.epoch, "workspace": store.workspace,
                "calls": store.calls, "agent_state": store.agent_state,
            }, sort_keys=True)
            changed = self.checkpoint != _h(state)
            if changed:
                self._q("save_recovery_checkpoint", "MERGE (s:QCheckpoint {id:'state'}) SET s.json = $json",
                        {"json": state}, request_id)
                self.checkpoint = _h(state)
            return self._sync_diff(request_id) + int(changed)

    def _sync_diff(self, request_id: Optional[str] = None) -> int:
        """Writes everything that changed since the last sync. Returns the number of statements run."""
        store = self.store
        with store._lock:
            cur_nodes = {n.id: n.model_dump_json() for n in store.nodes.values()}
            cur_edges = {e.id: e.model_dump_json() for e in store.edges}
            cur_hands = {h.id: h.model_dump_json() for h in store.handoffs}
            epoch = store.epoch
            cur_ws = json.dumps(store.workspace, sort_keys=True)
            cur_calls = {c["id"]: json.dumps(c, sort_keys=True) for c in store.calls}
            cur_agents = {k: json.dumps({"ids": list(v.get("ids", [])), "query": v.get("query", ""), "epoch": v.get("epoch", 0)}, sort_keys=True) for k, v in store.agent_state.items()}
            nodes_by_id = dict(store.nodes)
            edges_by_id = {e.id: e for e in store.edges}

        up_nodes = [i for i, js in cur_nodes.items() if self.nodes.get(i) != _h(js)]
        del_nodes = [i for i in self.nodes if i not in cur_nodes]
        up_edges = [i for i, js in cur_edges.items() if self.edges.get(i) != _h(js)]
        del_edges = [i for i in self.edges if i not in cur_edges]
        up_hands = [i for i, js in cur_hands.items() if self.handoffs.get(i) != _h(js)]
        del_hands = [i for i in self.handoffs if i not in cur_hands]
        epoch_changed = self.epoch != epoch
        ws_changed = self.workspace != _h(cur_ws)
        up_calls = [i for i, js in cur_calls.items() if self.calls.get(i) != _h(js)]
        del_calls = [i for i in self.calls if i not in cur_calls]
        up_agents = [k for k, js in cur_agents.items() if self.agents.get(k) != _h(js)]
        del_agents = [k for k in self.agents if k not in cur_agents]
        from app.services import embeddings

        want_vec = [i for i in up_nodes if nodes_by_id[i].kind.value in ("fact", "conclusion")]
        if embeddings.provider_name() == "local":
            vec_rows = want_vec
        else:  # a network call has no place in the write path: store only vectors a query already computed
            vec_rows = [i for i in want_vec if embeddings.peek(nodes_by_id[i]) is not None]
        up_vec = [i for i in vec_rows if self.emb.get(i) != embeddings.digest_for(nodes_by_id[i])]
        if not (up_agents or del_agents or up_vec or up_nodes or del_nodes or up_edges or del_edges or up_hands or del_hands or epoch_changed or ws_changed or up_calls or del_calls):
            return 0

        n_stmts = 0
        # Removed things first so a reset does not leave orphans behind.
        if del_edges:
            self._q("delete_edges", "UNWIND $ids AS id MATCH ()-[r {id:id}]->() DELETE r", {"ids": del_edges}, request_id)
            n_stmts += 1
        if del_nodes:
            self._q("delete_nodes", "UNWIND $ids AS id MATCH (n:QNode {id:id}) DETACH DELETE n", {"ids": del_nodes}, request_id)
            n_stmts += 1
        if del_hands:
            self._q("delete_handoffs", "UNWIND $ids AS id MATCH (h:QHandoff {id:id}) DELETE h", {"ids": del_hands}, request_id)
            n_stmts += 1

        by_kind: Dict[str, List[Dict[str, Any]]] = {}
        for i in up_nodes:
            n = nodes_by_id[i]
            by_kind.setdefault(_label(n.kind.value.capitalize()), []).append(
                {
                    "id": n.id,
                    "kind": n.kind.value,
                    "label": n.label,
                    "status": n.status.value,
                    "stale": bool(n.stale),
                    "depth": n.depth,
                    "valid_from": n.valid_from,
                    "json": cur_nodes[i],
                }
            )
        for kind, rows in by_kind.items():
            self._q(
                f"upsert_{kind.lower()}",
                f"UNWIND $rows AS row MERGE (n:QNode {{id: row.id}}) SET n:{kind}, n.kind = row.kind, n.label = row.label, "
                "n.status = row.status, n.stale = row.stale, n.depth = row.depth, n.valid_from = row.valid_from, n.json = row.json",
                {"rows": rows},
                request_id,
            )
            n_stmts += 1

        by_type: Dict[str, List[Dict[str, Any]]] = {}
        for i in up_edges:
            e = edges_by_id[i]
            by_type.setdefault(_label(e.type.value), []).append({"id": e.id, "s": e.source, "t": e.target, "json": cur_edges[i]})
        for typ, rows in by_type.items():
            self._q(
                f"upsert_{typ.lower()}_link",
                f"UNWIND $rows AS row MATCH (a:QNode {{id: row.s}}), (b:QNode {{id: row.t}}) "
                f"MERGE (a)-[r:{typ} {{id: row.id}}]->(b) SET r.json = row.json",
                {"rows": rows},
                request_id,
            )
            n_stmts += 1

        if up_hands:
            hs = {h.id: h for h in store.handoffs}
            rows = [{"id": i, "kind": hs[i].kind.value, "status": hs[i].status.value, "json": cur_hands[i]} for i in up_hands if i in hs]
            self._q(
                "upsert_handoffs",
                "UNWIND $rows AS row MERGE (h:QHandoff {id: row.id}) SET h.kind = row.kind, h.status = row.status, h.json = row.json",
                {"rows": rows},
                request_id,
            )
            n_stmts += 1

        if up_vec:
            vecs = embeddings.vectors_for([nodes_by_id[i] for i in up_vec])
            rows = [{"id": i, "vec": embeddings.as_list(vecs[i]), "d": embeddings.digest_for(nodes_by_id[i])} for i in up_vec if i in vecs]
            self._q("store_embeddings", "UNWIND $rows AS row MATCH (n:QNode {id: row.id}) SET n.embedding = vecf32(row.vec), n.emb = row.d", {"rows": rows}, request_id)
            n_stmts += 1
        if up_agents:
            rows = [{"id": k, "json": cur_agents[k]} for k in up_agents]
            self._q("save_agent_state", "UNWIND $rows AS row MERGE (a:QAgent {id: row.id}) SET a.json = row.json", {"rows": rows}, request_id)
            n_stmts += 1
        if del_agents:
            self._q("forget_agent_state", "UNWIND $ids AS id MATCH (a:QAgent {id:id}) DELETE a", {"ids": del_agents}, request_id)
            n_stmts += 1
        if ws_changed:
            self._q("save_workspace", "MERGE (w:QWorkspace {id:'ws'}) SET w.json = $js", {"js": cur_ws}, request_id)
            n_stmts += 1
        if up_calls:
            rows = [{"id": i, "ts": json.loads(cur_calls[i])["ts"], "json": cur_calls[i]} for i in up_calls]
            self._q("append_calls", "UNWIND $rows AS row MERGE (c:QCall {id: row.id}) SET c.ts = row.ts, c.json = row.json", {"rows": rows}, request_id)
            n_stmts += 1
        if del_calls:
            self._q("trim_calls", "UNWIND $ids AS id MATCH (c:QCall {id:id}) DELETE c", {"ids": del_calls}, request_id)
            n_stmts += 1
        if epoch_changed:
            self._q("save_epoch", "MERGE (m:QMeta {id:'meta'}) SET m.epoch = $e", {"e": epoch}, request_id)
            n_stmts += 1

        # Only now remember what is persisted, so a failed write is retried on the next sync.
        self.nodes = {i: _h(js) for i, js in cur_nodes.items()}
        self.edges = {i: _h(js) for i, js in cur_edges.items()}
        self.handoffs = {i: _h(js) for i, js in cur_hands.items()}
        self.epoch = epoch
        self.workspace = _h(cur_ws)
        self.calls = {i: _h(js) for i, js in cur_calls.items()}
        self.agents = {k: _h(js) for k, js in cur_agents.items()}
        for i in up_vec:
            self.emb[i] = embeddings.digest_for(nodes_by_id[i])
        for i in del_nodes:
            self.emb.pop(i, None)
        if del_nodes:
            embeddings.forget(del_nodes)
        return n_stmts

    def wipe(self) -> None:
        """Delete this tenant's API graph entirely (used by tests and tenant reset)."""
        try:
            self.graph.delete()
        except Exception:
            pass
        self.nodes, self.edges, self.handoffs, self.epoch, self.workspace, self.calls = {}, {}, {}, None, None, {}
        self.checkpoint = None
        self.agents, self.emb = {}, {}
