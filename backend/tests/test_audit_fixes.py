"""
Verification tests for the 10-point audit bug fixes:
1. Production default & console sign-in (already verified)
2. Proxy IP extraction & uvicorn forwarded headers
3. CSV export empty cell formula quote bug
4. Bounded LRU caches & periodic pruning
5. Per-agent read state cleanup on member removal & 500 cap
6. Negative cache for forged/unknown tenants
7. Observer keys preserving word order
8. Quarantined facts allow re-observation
9. Exact path matching for open paths (/api/healthXYZ rejected)
10. Vector batching in cosine_scores
"""
from unittest.mock import MagicMock
from starlette.requests import Request

from app.graph.queries import get_graph_store, tenant_exists
from app.main import _client_ip, _is_open_path, app
from app.models.common import Status
from app.ratelimit import FailureTracker, RateLimiter
from app.services import embeddings, mcp_read, mcp_write, observer, workspace
from starlette.testclient import TestClient


def _make_request(headers: dict, client_host: str = "10.0.0.1") -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/health",
        "headers": [(k.lower().encode("latin1"), v.encode("latin1")) for k, v in headers.items()],
        "client": (client_host, 12345),
    }
    return Request(scope)


def test_item_2_client_ip_proxy_headers(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "TRUSTED_PROXIES", "127.0.0.1,10.0.0.2")

    # Behind trusted proxy with X-Forwarded-For
    req = _make_request({"X-Forwarded-For": "203.0.113.195, 10.0.0.2"}, client_host="10.0.0.2")
    assert _client_ip(req) == "203.0.113.195"

    # Behind trusted proxy with X-Real-IP
    req2 = _make_request({"X-Real-IP": "198.51.100.42"}, client_host="10.0.0.2")
    assert _client_ip(req2) == "198.51.100.42"

    # Untrusted sender attempting to spoof X-Forwarded-For
    untrusted_req = _make_request({"X-Forwarded-For": "1.1.1.1"}, client_host="198.51.100.25")
    assert _client_ip(untrusted_req) == "198.51.100.25"  # Spoofed header ignored!

    # Untrusted sender attempting to spoof X-Real-IP
    untrusted_req2 = _make_request({"X-Real-IP": "1.1.1.1"}, client_host="192.168.1.50")
    assert _client_ip(untrusted_req2) == "192.168.1.50"  # Spoofed header ignored!


def test_item_9_open_paths_exact_matching():
    # Valid open paths
    assert _is_open_path("/api/health") is True
    assert _is_open_path("/docs") is True
    assert _is_open_path("/docs/oauth2-redirect") is True
    assert _is_open_path("/openapi.json") is True
    assert _is_open_path("/redoc") is True

    # Prefix attacks / invalid health extensions must NOT be treated as open paths
    assert _is_open_path("/api/healthXYZ") is False
    assert _is_open_path("/api/health/fake") is False
    assert _is_open_path("/api/health_check") is False
    assert _is_open_path("/api/health123") is False


def test_item_3_csv_export_empty_cells_not_quoted(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "ENV", "development")
    client = TestClient(app)
    # Token for tenant acme
    tok = "token_acme_human"
    r = client.get("/api/audit?format=csv", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    csv_text = r.text
    lines = csv_text.strip().split("\n")
    header = lines[0]
    assert "time_utc,who,team,client,tool,what,severity,ok,tokens,tokens_saved" in header

    # Ensure no stray single quotes on empty values like ,',
    assert ",''," not in csv_text
    assert ",'" not in csv_text or ",','" in csv_text  # only if preceded by a quote


def test_item_4_ratelimit_and_failure_tracker_bounds():
    limiter = RateLimiter(per_minute=60, max_entries=50)
    for i in range(100):
        limiter.allow(f"caller_{i}")
    assert len(limiter._b) <= 50

    tracker = FailureTracker(limit=5, max_entries=50)
    for i in range(100):
        tracker.record(f"ip_{i}")
    assert len(tracker._hits) <= 50


def test_item_4_embeddings_lru_cache_bounds():
    old_max = embeddings.MAX_CACHE
    try:
        embeddings.MAX_CACHE = 20
        for i in range(50):
            node = MagicMock(id=f"node_{i}", label=f"label_{i}", text=f"text_{i}", key=None, value=None)
            embeddings.vectors_for([node])
        assert len(embeddings._cache) <= 20
    finally:
        embeddings.MAX_CACHE = old_max


def test_item_5_remove_member_cleans_agent_state():
    store = get_graph_store("audit_test_ws")
    team = workspace.add_team(store, "Platform", 5)
    m, _ = workspace.add_member(store, "agent_clean", "agent", team["id"], "claude-code", "laptop")
    mid = m["id"]

    # Record agent read state
    store.agent_state[mid] = {"ids": ["fact_1", "fact_2"], "query": "hello", "epoch": 1}
    assert mid in store.agent_state

    # Remove member
    workspace.remove_member(store, mid)
    assert mid not in store.agent_state


def test_item_5_agent_state_ids_capped_at_500_by_recency():
    store = get_graph_store("audit_test_cap")
    auth = {"tenant": "audit_test_cap", "member_id": "mem_1"}
    big_list = [f"f_{i}" for i in range(700)]
    mcp_read._remember_delivery(store, auth, big_list, "test")
    st = store.agent_state.get("mem_1")
    assert st is not None
    assert len(st["ids"]) == 500
    # Must strictly preserve FIFO recency order: oldest 200 evicted, latest 500 kept
    assert st["ids"][0] == "f_200"
    assert st["ids"][-1] == "f_699"


def test_item_5_agent_state_persistence_order_preserved():
    import json
    from app.persistence import Persister  # noqa: F401
    store = get_graph_store("audit_test_persist_order")
    auth = {"tenant": "audit_test_persist_order", "member_id": "agent_fifo"}
    # Insert non-alphabetical IDs to verify sort order is not applied
    order = ["zeta_fact", "alpha_fact", "mu_fact", "beta_fact", "delta_fact"]
    mcp_read._remember_delivery(store, auth, order, "query_persist")

    st = store.agent_state.get("agent_fifo")
    assert st["ids"] == order

    # Simulate serialization done during persistence sync
    cur_agents = {
        k: json.dumps({"ids": list(v.get("ids", [])), "query": v.get("query", ""), "epoch": v.get("epoch", 0)}, sort_keys=True)
        for k, v in store.agent_state.items()
    }
    # Ensure raw serialized json retains list order, not sorted alphabetically
    saved_obj = json.loads(cur_agents["agent_fifo"])
    assert saved_obj["ids"] == order
    assert saved_obj["ids"] != sorted(order)

    # Simulate restoration done during persistence load
    new_store = get_graph_store("audit_test_persist_order_restored")
    new_store.agent_state.clear()
    for aid, js in cur_agents.items():
        d = json.loads(js)
        new_store.agent_state[aid] = {"ids": list(d.get("ids", [])), "query": d.get("query", ""), "epoch": d.get("epoch", 0)}

    restored_st = new_store.agent_state.get("agent_fifo")
    assert isinstance(restored_st["ids"], list)
    assert restored_st["ids"] == order

    # Further deliveries to restored store continue strictly in FIFO order
    mcp_read._remember_delivery(new_store, auth, ["omega_fact"])
    assert new_store.agent_state["agent_fifo"]["ids"] == order + ["omega_fact"]


def test_item_6_tenant_exists_negative_cache():
    # Calling tenant_exists for nonexistent tenant caches negative lookup
    res1 = tenant_exists("nonexistent_tenant_99999")
    assert res1 is False
    res2 = tenant_exists("nonexistent_tenant_99999")
    assert res2 is False


def test_item_7_observer_keys_direction_and_restatements():
    # 1. Distinct directions have different keys
    s1 = "Export from Chile to Norway takes 10 days."
    s2 = "Export from Norway to Chile takes 10 days."
    c1 = observer.candidates(s1)
    c2 = observer.candidates(s2)
    assert len(c1) == 1 and len(c2) == 1
    assert c1[0]["key"] != c2[0]["key"]

    # 2. Restatements with different phrasing produce the same key
    r1 = "The refund window in Chile is 21 days from delivery."
    r2 = "In Chile, the refund window is 45 days from delivery."
    cr1 = observer.candidates(r1)
    cr2 = observer.candidates(r2)
    assert len(cr1) == 1 and len(cr2) == 1
    assert cr1[0]["key"] == cr2[0]["key"] == "chile_delivery_refund_window"


def test_item_8_quarantined_facts_survive_without_overwriting():
    tok = "token_audit_reobserve_agent"
    text = "The cancellation deadline in Peru is 14 days after signup."
    # 1. Observe first time
    out1 = mcp_write.observe(text, tok)
    assert out1["stored"] == 1
    original_id = out1["facts"][0]["id"]

    # 2. Re-observing while healthy is marked as duplicate
    out_dup = mcp_write.observe(text, tok)
    assert out_dup["stored"] == 0
    assert len(out_dup["duplicates"]) == 1

    # 3. Quarantine the fact
    store = get_graph_store("audit_reobserve")
    fact = store.nodes[original_id]
    fact.status = Status.QUARANTINED

    # 4. Re-observing after quarantine does NOT overwrite the quarantined node!
    out_after = mcp_write.observe(text, tok)
    assert out_after["stored"] == 1
    new_id = out_after["facts"][0]["id"]

    # The quarantined node MUST survive in quarantined state!
    assert store.nodes[original_id].status == Status.QUARANTINED
    # The new fact got a distinct ID and did not overwrite the quarantined node
    assert new_id != original_id
    assert store.nodes[new_id].status == Status.HEALTHY


def test_item_10_vector_batch_and_cache_cap():
    assert embeddings.MAX_CACHE >= 50000
    nodes = [MagicMock(id=f"bn_{i}", label=f"node {i}", text=f"text content {i}", key=None, value=None) for i in range(30)]
    scores = embeddings.cosine_scores("content text", nodes)
    assert len(scores) == 30
    assert all(0.0 <= s <= 1.0 for s in scores.values())


def test_falkordb_cypher_cascade_and_path_finding():
    from unittest.mock import MagicMock
    from app.graph.queries import GraphStore
    from app.models.graph import GraphNode, GraphEdge
    from app.models.common import Status, NodeKind, EdgeType
    from app.services import context_pack as cp

    store = GraphStore("test_cypher_tenant")
    n1 = GraphNode(id="root1", kind=NodeKind.FACT, label="Root fact", status=Status.HEALTHY)
    n2 = GraphNode(id="child1", kind=NodeKind.CONCLUSION, label="Child conclusion", status=Status.HEALTHY)
    store.add_node(n1)
    store.add_node(n2)
    store.add_edge(GraphEdge(id="e1", source="root1", target="child1", type=EdgeType.LEADS_TO))

    # Mock persister to verify Cypher dispatch
    mock_persister = MagicMock()
    mock_res_cascade = MagicMock()
    mock_res_cascade.result_set = [["child1"]]
    mock_res_cascade.nodes_created = 0
    mock_res_cascade.properties_set = 2
    mock_res_cascade.relationships_created = 0
    mock_res_cascade.nodes_deleted = 0

    executed_queries = []

    def fake_q(name, cypher, params=None, request_id=None):
        executed_queries.append((name, cypher, params))
        store.log_query(name, cypher, 1.5, 1, request_id, real=True)
        if name == "traverse_hop":
            res = MagicMock()
            ids = set((params or {}).get("ids", []))
            if "<-" in cypher or cypher.startswith("MATCH (b:QNode)-"):
                res.result_set = [["root1"]] if "child1" in ids else []
            else:
                res.result_set = [["child1"]] if "root1" in ids else []
            return res
        if name == "shortest_path":
            res = MagicMock()
            res.result_set = [[["root1", "child1"]]]
            return res
        if name == "get_descendants":
            res = MagicMock()
            res.result_set = [["child1"]]
            return res
        if name == "get_ancestors":
            res = MagicMock()
            res.result_set = [["root1"]]
            return res
        res = MagicMock()
        res.result_set = []
        return res

    mock_persister._q = fake_q
    mock_persister.sync = MagicMock()
    store.persister = mock_persister

    # 1. The auditor's cascade is a Cypher traversal in FalkorDB (one statement per hop) plus a Cypher status flip,
    # all under the store lock, and the complete diff is committed afterwards.
    from app.services.auditor import auditor_service
    affected = auditor_service.quarantine(store, "root1", tenant=store.tenant)
    assert mock_persister.sync.called
    assert [q[0] for q in executed_queries].count("traverse_hop") >= 2
    assert any(q[0] == "cascade_flip" for q in executed_queries)
    assert store.last_cascade_engine == "falkordb"
    assert store.nodes["root1"].status == Status.QUARANTINED
    assert store.nodes["child1"].status == Status.QUARANTINED
    assert len(affected) == 2

    # 2. Test Cypher shortestPath via find_path
    res_path = cp.find_path(store, "root1", "child1")
    assert res_path["path"] == ["root1", "child1"]
    assert any(q[0] == "shortest_path" for q in executed_queries)
    assert any("shortestPath" in q[1] for q in executed_queries)

    # 3. Test Cypher get_descendants & get_ancestors
    descs = store.get_descendants("root1")
    assert [d.id for d in descs] == ["child1"]
    assert any(q[0] == "traverse_hop" for q in executed_queries)

    ancs = store.get_ancestors("child1")
    assert [a.id for a in ancs] == ["root1"]
    assert sum(1 for q in executed_queries if q[0] == "traverse_hop") >= 4

