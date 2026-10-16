import time
import pytest
import concurrent.futures
from fastapi.testclient import TestClient
from app.main import app
from app.graph.queries import get_graph_store
from app.models.common import Status, NodeKind, EdgeType, CompensationStatus

@pytest.fixture
def client():
    return TestClient(app)

def test_full_4beat_scenario_replay(client):
    """
    Audit 4-beat scenario replay:
    Beat 1: poison webhook
    Beat 2: run agents
    Beat 3: audit memory
    Beat 4: replace policy (supersede)
    """
    headers = {"Authorization": "Bearer token_auditor"}
    store = get_graph_store("acme")

    # Reset
    res_reset = client.post("/api/scenario/reset", headers=headers)
    assert res_reset.status_code == 200
    assert "fact_30d" in store.nodes
    assert store.nodes["fact_30d"].status == Status.HEALTHY
    assert len(store.nodes) == 2
    assert len(store.edges) == 1

    # Beat 1: inject_poison
    res_b1 = client.post("/api/scenario/inject_poison", headers=headers)
    assert res_b1.status_code == 200
    assert "fact_90d" in store.nodes
    assert store.nodes["fact_90d"].status == Status.HEALTHY
    assert "src_webhook_partner" in store.nodes

    # Beat 2: run_agents
    res_b2 = client.post("/api/scenario/run_agents", headers=headers)
    assert res_b2.status_code == 200
    assert "conc_eligible_1042" in store.nodes
    assert store.nodes["conc_eligible_1042"].status == Status.HEALTHY
    assert store.nodes["act_refund_1"].action.executed is True
    assert store.nodes["act_refund_2"].action.executed is True
    assert store.nodes["act_refund_3"].action.executed is False
    assert store.nodes["act_email_cust"].action.executed is True

    # Beat 3: audit
    res_b3 = client.post("/api/scenario/audit", headers=headers)
    assert res_b3.status_code == 200
    assert store.nodes["fact_90d"].status == Status.QUARANTINED
    assert store.nodes["conc_eligible_1042"].status == Status.QUARANTINED
    assert store.nodes["act_refund_1"].status == Status.QUARANTINED
    assert store.nodes["act_refund_2"].status == Status.QUARANTINED
    assert store.nodes["act_refund_3"].status == Status.QUARANTINED
    assert store.nodes["act_email_cust"].status == Status.QUARANTINED
    # Compensation check
    assert store.nodes["act_refund_1"].action.compensation.status == CompensationStatus.COMPENSATED
    assert store.nodes["act_refund_2"].action.compensation.status == CompensationStatus.COMPENSATED

    # Beat 4: supersede (replace policy)
    res_b4 = client.post("/api/scenario/supersede", headers=headers)
    assert res_b4.status_code == 200
    assert "fact_45d_v4" in store.nodes
    assert store.nodes["fact_45d_v4"].status == Status.HEALTHY
    assert store.nodes["fact_30d"].status == Status.SUPERSEDED


def test_reset_hammer_30_parallel(client):
    """
    Multi-vector stress: 30 parallel POST requests to /api/scenario/reset.
    """
    headers = {"Authorization": "Bearer token_auditor"}
    
    def call_reset(i):
        return client.post("/api/scenario/reset", headers=headers)
        
    with concurrent.futures.ThreadPoolExecutor(max_workers=30) as executor:
        futures = [executor.submit(call_reset, i) for i in range(30)]
        results = [f.result() for f in futures]
        
    for r in results:
        assert r.status_code == 200, f"Reset failed with {r.status_code}: {r.text}"
        assert r.json()["accepted"] is True
        
    store = get_graph_store("acme")
    assert "fact_30d" in store.nodes
    assert store.nodes["fact_30d"].status == Status.HEALTHY
    assert "src_policy_v3" in store.nodes
    assert len(store.nodes) == 2
    assert len(store.edges) == 1
    inv = store.check_invariants()
    assert inv["ok"] is True


def test_rapid_hammer_clicking_idempotency(client):
    """
    Audit idempotency under rapid clicking: verify pop existing node IDs before re-insertion under rapid hammer stress.
    """
    headers = {"Authorization": "Bearer token_auditor"}
    store = get_graph_store("acme")
    
    # 1. Reset hammer (10 concurrent)
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(lambda: client.post("/api/scenario/reset", headers=headers)) for _ in range(10)]
        assert all(f.result().status_code == 200 for f in futures)
    assert len(store.nodes) == 2

    # 2. Inject poison hammer (10 concurrent)
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(lambda: client.post("/api/scenario/inject_poison", headers=headers)) for _ in range(10)]
        assert all(f.result().status_code == 200 for f in futures)
    assert "fact_90d" in store.nodes
    assert len([n for n in store.nodes.values() if n.id == "fact_90d"]) == 1
    assert len([e for e in store.edges if e.id == "e_src_webhook"]) == 1

    # 3. Run agents hammer (10 concurrent)
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(lambda: client.post("/api/scenario/run_agents", headers=headers)) for _ in range(10)]
        assert all(f.result().status_code == 200 for f in futures)
    assert len([n for n in store.nodes.values() if n.id == "conc_eligible_1042"]) == 1
    assert len([n for n in store.nodes.values() if n.id == "act_refund_1"]) == 1
    assert len([e for e in store.edges if e.id == "e_conc_1042"]) == 1

    # 4. Audit hammer (10 concurrent)
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(lambda: client.post("/api/scenario/audit", headers=headers)) for _ in range(10)]
        assert all(f.result().status_code == 200 for f in futures)
    assert store.nodes["fact_90d"].status == Status.QUARANTINED

    # 5. Supersede hammer (10 concurrent)
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(lambda: client.post("/api/scenario/supersede", headers=headers)) for _ in range(10)]
        assert all(f.result().status_code == 200 for f in futures)
    assert len([n for n in store.nodes.values() if n.id == "fact_45d_v4"]) == 1
    assert len([e for e in store.edges if e.id == "e_src_v4"]) == 1
    assert len([e for e in store.edges if e.id == "e_supersede_30d"]) == 1


def test_out_of_order_beat_boundary_conditions(client):
    """
    Multi-vector stress & boundary tests:
    - Beat 4 before Beat 1
    - Beat 3 before Beat 1
    - Beat 2 before Beat 1 (triggers dangling edge e_conc_1042!)
    """
    headers = {"Authorization": "Bearer token_auditor"}
    store = get_graph_store("acme")

    # 1. Beat 4 (supersede) before Beat 1 (inject_poison)
    client.post("/api/scenario/reset", headers=headers)
    res_b4 = client.post("/api/scenario/supersede", headers=headers)
    assert res_b4.status_code == 200
    assert store.nodes["fact_30d"].status == Status.SUPERSEDED
    assert "fact_45d_v4" in store.nodes
    inv = store.check_invariants()
    assert inv["ok"] is True

    # 2. Beat 3 (audit) before Beat 1 (inject_poison)
    client.post("/api/scenario/reset", headers=headers)
    res_b3 = client.post("/api/scenario/audit", headers=headers)
    assert res_b3.status_code == 200
    # Audit safely self-heals by injecting poison, then quarantining it
    assert "fact_90d" in store.nodes
    assert store.nodes["fact_90d"].status == Status.QUARANTINED
    assert store.nodes["fact_30d"].status == Status.HEALTHY

    # 3. Beat 2 (run_agents) before Beat 1 (inject_poison) -> No dangling edges allowed
    client.post("/api/scenario/reset", headers=headers)
    res_b2 = client.post("/api/scenario/run_agents", headers=headers)
    assert res_b2.status_code == 200
    assert "fact_90d" in store.nodes
    assert "conc_eligible_1042" in store.nodes
    # Verify zero dangling edges:
    dangling_edges = [e for e in store.edges if e.source not in store.nodes or e.target not in store.nodes]
    assert len(dangling_edges) == 0, f"Found unexpected dangling edges: {dangling_edges}"


def test_system_reset_route_audit(client):
    """
    Audit timetravel snapshots: verify if POST /api/system/reset exists.
    """
    headers = {"Authorization": "Bearer token_auditor"}
    res = client.post("/api/system/reset", headers=headers)
    # The endpoint does NOT exist (404 Not Found)
    print("POST /api/system/reset response:", res.status_code)
    assert res.status_code in (404, 405)


def test_timetravel_as_of_queries(client):
    """
    Audit timetravel as-of queries:
    - Before poison (t0) -> only 30d policy
    - After agents (t1) -> 90d policy and conclusion/actions visible
    - After audit (t2) -> 90d policy and conclusion/actions excluded (quarantined)
    - After supersede (t3) -> checks if 30d policy valid_to was closed
    """
    headers = {"Authorization": "Bearer token_auditor"}
    store = get_graph_store("acme")

    # t0: Reset
    client.post("/api/scenario/reset", headers=headers)
    t0 = int(time.time() * 1000)
    time.sleep(0.01)

    # t1: Inject and run agents
    client.post("/api/scenario/inject_poison", headers=headers)
    client.post("/api/scenario/run_agents", headers=headers)
    t1 = int(time.time() * 1000)
    time.sleep(0.01)

    # t2: Audit (quarantine)
    client.post("/api/scenario/audit", headers=headers)
    t2 = int(time.time() * 1000)
    time.sleep(0.01)

    # t3: Supersede
    client.post("/api/scenario/supersede", headers=headers)
    t3 = int(time.time() * 1000)

    # As-of query at t0: should only see fact_30d, not fact_90d
    res_t0 = client.get(f"/api/graph?as_of={t0}", headers=headers)
    assert res_t0.status_code == 200
    t0_nodes = {n["id"] for n in res_t0.json()["nodes"]}
    assert "fact_30d" in t0_nodes
    assert "fact_90d" not in t0_nodes

    # As-of query at t1: should see fact_90d and conc_eligible_1042
    res_t1 = client.get(f"/api/graph?as_of={t1}", headers=headers)
    assert res_t1.status_code == 200
    t1_nodes = {n["id"] for n in res_t1.json()["nodes"]}
    assert "fact_90d" in t1_nodes
    assert "conc_eligible_1042" in t1_nodes

    # As-of query at t2: should exclude fact_90d and conc_eligible_1042 because they are quarantined
    res_t2 = client.get(f"/api/graph?as_of={t2}", headers=headers)
    assert res_t2.status_code == 200
    t2_nodes = {n["id"] for n in res_t2.json()["nodes"]}
    assert "fact_90d" not in t2_nodes
    assert "conc_eligible_1042" not in t2_nodes

    # As-of query at t3:
    res_t3 = client.get(f"/api/graph?as_of={t3}", headers=headers)
    assert res_t3.status_code == 200
    t3_nodes = {n["id"] for n in res_t3.json()["nodes"]}
    assert "fact_45d_v4" in t3_nodes
    # Check if fact_30d is present or excluded:
    # Since fact_30d.valid_to was NOT set, fact_30d is still returned!
    print("t3_nodes:", t3_nodes)
    print("fact_30d in t3_nodes:", "fact_30d" in t3_nodes)
