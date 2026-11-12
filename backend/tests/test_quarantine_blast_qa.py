import concurrent.futures
import time
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.graph.queries import get_graph_store, GraphStore
from app.services.auditor import auditor_service
from app.models.graph import GraphNode, GraphEdge, ActionInfo
from app.models.common import Status, NodeKind, EdgeType, Reversibility, GateStatus, CompensationStatus


@pytest.fixture
def test_client():
    return TestClient(app)


def test_deep_subgraph_cascade_quarantine_and_convergence():
    """
    Multi-Vector Stress Test:
    Quarantine cascade on deep subgraphs (depth > 5) with multi-parent convergence.
    Builds a DAG with depth = 8, where each intermediate layer contains multiple
    convergent paths (diamonds at every level) leading to a shared sink action.
    Asserts:
    1. Every descendant node across all 8 depths is marked QUARANTINED.
    2. Computed depth reflects exact hop distance (sink at depth 8).
    3. Traversal visited set prevents duplicate processing or infinite loops.
    4. Meta.epoch bumps monotonically.
    5. Invariants hold: zero healthy nodes under quarantined nodes.
    """
    store = GraphStore("acme_deep_test")
    now = int(time.time() * 1000)

    # Root node at depth 0
    root = GraphNode(id="root_deep", kind=NodeKind.FACT, label="Root Deep Policy", status=Status.HEALTHY, valid_from=now)
    store.add_node(root)

    # 7 intermediate levels with 2 nodes per level (diamond convergence at each hop)
    depth_levels = 7
    for d in range(1, depth_levels + 1):
        for variant in ["A", "B"]:
            nid = f"node_d{d}_{variant}"
            store.add_node(GraphNode(id=nid, kind=NodeKind.CONCLUSION, label=f"Level {d} {variant}", status=Status.HEALTHY, valid_from=now))

    # Sink node at depth 8 (executed compensable action)
    sink = GraphNode(
        id="act_sink_depth8",
        kind=NodeKind.ACTION,
        label="Deep Sink Action",
        status=Status.HEALTHY,
        valid_from=now,
        action=ActionInfo(
            type="refund",
            reversibility=Reversibility.COMPENSABLE,
            executed=True,
            gate=GateStatus.PASSED,
        ),
    )
    store.add_node(sink)

    # Edges from root to level 1
    store.add_edge(GraphEdge(id="e_root_1A", source="root_deep", target="node_d1_A", type=EdgeType.LEADS_TO))
    store.add_edge(GraphEdge(id="e_root_1B", source="root_deep", target="node_d1_B", type=EdgeType.LEADS_TO))

    # All-to-all converging edges between adjacent layers
    for d in range(1, depth_levels):
        for v1 in ["A", "B"]:
            for v2 in ["A", "B"]:
                eid = f"e_d{d}{v1}_d{d+1}{v2}"
                store.add_edge(GraphEdge(id=eid, source=f"node_d{d}_{v1}", target=f"node_d{d+1}_{v2}", type=EdgeType.LEADS_TO))

    # Level 7 converges onto sink action (depth 8)
    store.add_edge(GraphEdge(id="e_7A_sink", source=f"node_d{depth_levels}_A", target="act_sink_depth8", type=EdgeType.LEADS_TO))
    store.add_edge(GraphEdge(id="e_7B_sink", source=f"node_d{depth_levels}_B", target="act_sink_depth8", type=EdgeType.LEADS_TO))

    initial_epoch = store.epoch

    # Execute quarantine cascade
    affected = auditor_service.quarantine(store, "root_deep", reason="Deep test poison", tenant="acme_deep_test")

    # Assertions
    # Total nodes = 1 (root) + 7*2 (intermediate) + 1 (sink) = 16 nodes
    assert len(affected) == 16, f"Expected 16 affected nodes, got {len(affected)}"
    assert store.epoch == initial_epoch + 1

    # Verify sink node reached depth 8
    sink_node = store.nodes["act_sink_depth8"]
    assert sink_node.status == Status.QUARANTINED
    assert sink_node.depth == 8
    assert sink_node.quarantine_root == "root_deep"
    assert sink_node.action.compensation.status == CompensationStatus.PENDING_COMPENSATION

    # Verify every intermediate node is quarantined with correct depth
    for d in range(1, depth_levels + 1):
        for variant in ["A", "B"]:
            n = store.nodes[f"node_d{d}_{variant}"]
            assert n.status == Status.QUARANTINED
            assert n.depth == d
            assert n.quarantine_root == "root_deep"

    # Invariants check
    inv = store.check_invariants()
    assert inv["ok"] is True, f"Invariant check failed: {inv}"


def test_concurrent_sibling_quarantine_race_condition():
    """
    Stress Test: Simultaneous quarantine of two sibling nodes that share descendants.
    Graph structure:
      Sibling1 (Fact) -----\\
                            +---> Shared_Conclusion ---> Shared_Action (executed, reversible)
      Sibling2 (Fact) -----/
    Simultaneously trigger quarantine on Sibling1 and Sibling2 in parallel threads.
    Asserts:
    1. Both sibling cascades complete cleanly without deadlocks.
    2. Shared_Conclusion and Shared_Action are marked QUARANTINED.
    3. Compensation is enqueued exactly ONCE (no duplicate compensation race).
    4. Graph invariants remain 100% sound.
    5. Epoch increments monotonically for each quarantine pass.
    """
    store = GraphStore("acme_sibling_race")
    now = int(time.time() * 1000)

    s1 = GraphNode(id="sib_1", kind=NodeKind.FACT, label="Sibling 1", status=Status.HEALTHY, valid_from=now)
    s2 = GraphNode(id="sib_2", kind=NodeKind.FACT, label="Sibling 2", status=Status.HEALTHY, valid_from=now)
    sc = GraphNode(id="shared_conc", kind=NodeKind.CONCLUSION, label="Shared Conclusion", status=Status.HEALTHY, valid_from=now)
    sa = GraphNode(
        id="shared_act",
        kind=NodeKind.ACTION,
        label="Shared Compensable Action",
        status=Status.HEALTHY,
        valid_from=now,
        action=ActionInfo(
            type="refund",
            reversibility=Reversibility.COMPENSABLE,
            executed=True,
            gate=GateStatus.PASSED,
        ),
    )

    store.add_node(s1)
    store.add_node(s2)
    store.add_node(sc)
    store.add_node(sa)

    store.add_edge(GraphEdge(id="e_s1_sc", source="sib_1", target="shared_conc", type=EdgeType.LEADS_TO))
    store.add_edge(GraphEdge(id="e_s2_sc", source="sib_2", target="shared_conc", type=EdgeType.LEADS_TO))
    store.add_edge(GraphEdge(id="e_sc_sa", source="shared_conc", target="shared_act", type=EdgeType.LEADS_TO))

    initial_epoch = store.epoch

    # Hammer both quarantines concurrently
    def run_q(node_id: str):
        return auditor_service.quarantine(store, node_id, tenant="acme_sibling_race")

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(run_q, "sib_1")
        f2 = executor.submit(run_q, "sib_2")
        res1 = f1.result()
        res2 = f2.result()
        assert res1 is not None and res2 is not None

    # Both completed
    assert s1.status == Status.QUARANTINED
    assert s2.status == Status.QUARANTINED
    assert sc.status == Status.QUARANTINED
    assert sa.status == Status.QUARANTINED

    # Epoch bumped twice
    assert store.epoch == initial_epoch + 2

    # Compensation must be pending and attempts initialized to 0
    assert sa.action.compensation is not None
    assert sa.action.compensation.status == CompensationStatus.PENDING_COMPENSATION
    assert sa.action.compensation.attempts == 0

    # Invariants must hold
    inv = store.check_invariants()
    assert inv["ok"] is True, f"Invariants failed after sibling race: {inv}"


def test_blast_preview_rest_simulation_without_mutation(test_client):
    """
    Tests POST /api/blast/preview and GET /api/blast/{node_id}/preview:
    1. Verifies preview simulates the cascade without mutating state:
       - Node statuses remain HEALTHY.
       - Store epoch remains identical.
       - No quarantined_at timestamps are written.
    2. Verifies preview accurately detects and categorizes executed actions:
       - staged vs executed_reversible vs executed_compensable vs executed_irreversible.
    """
    store = get_graph_store("acme")
    now = int(time.time() * 1000)

    # Set up test chain in store
    root_id = "fact_blast_sim_root"
    store.add_node(GraphNode(id=root_id, kind=NodeKind.FACT, label="Blast Sim Root", status=Status.HEALTHY, valid_from=now))
    store.add_node(GraphNode(id="conc_blast_sim", kind=NodeKind.CONCLUSION, label="Blast Sim Conc", status=Status.HEALTHY, valid_from=now))
    store.add_node(GraphNode(
        id="act_staged",
        kind=NodeKind.ACTION,
        label="Staged Act",
        status=Status.HEALTHY,
        valid_from=now,
        action=ActionInfo(type="refund", reversibility=Reversibility.COMPENSABLE, executed=False, gate=GateStatus.PASSED),
    ))
    store.add_node(GraphNode(
        id="act_comp",
        kind=NodeKind.ACTION,
        label="Executed Compensable",
        status=Status.HEALTHY,
        valid_from=now,
        action=ActionInfo(type="refund", reversibility=Reversibility.COMPENSABLE, executed=True, gate=GateStatus.PASSED),
    ))
    store.add_node(GraphNode(
        id="act_irrev",
        kind=NodeKind.ACTION,
        label="Executed Irreversible",
        status=Status.HEALTHY,
        valid_from=now,
        action=ActionInfo(type="email", reversibility=Reversibility.IRREVERSIBLE, executed=True, gate=GateStatus.PASSED),
    ))

    store.add_edge(GraphEdge(id="e_bs_1", source=root_id, target="conc_blast_sim", type=EdgeType.LEADS_TO))
    store.add_edge(GraphEdge(id="e_bs_2", source="conc_blast_sim", target="act_staged", type=EdgeType.LEADS_TO))
    store.add_edge(GraphEdge(id="e_bs_3", source="conc_blast_sim", target="act_comp", type=EdgeType.LEADS_TO))
    store.add_edge(GraphEdge(id="e_bs_4", source="conc_blast_sim", target="act_irrev", type=EdgeType.LEADS_TO))

    epoch_before = store.epoch

    # Call POST /api/blast/preview
    headers = {"Authorization": "Bearer token_acme_auditor"}
    resp = test_client.post("/api/blast/preview", json={"node_id": root_id}, headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["root_id"] == root_id
    assert set(data["node_ids"]) == {root_id, "conc_blast_sim", "act_staged", "act_comp", "act_irrev"}
    assert data["facts"] == 1
    assert data["conclusions"] == 1
    assert data["actions"]["staged"] == 1
    assert data["actions"]["executed_compensable"] == 1
    assert data["actions"]["executed_irreversible"] == 1
    assert data["actions"]["executed_reversible"] == 0

    # Verify NO STATE WAS MUTATED
    assert store.epoch == epoch_before
    assert store.nodes[root_id].status == Status.HEALTHY
    assert store.nodes["conc_blast_sim"].status == Status.HEALTHY
    assert store.nodes["act_staged"].status == Status.HEALTHY
    assert store.nodes["act_comp"].status == Status.HEALTHY
    assert store.nodes["act_irrev"].status == Status.HEALTHY
    assert store.nodes[root_id].quarantined_at is None


def test_snapshot_restoration_safety_and_invariant_preservation(test_client):
    """
    Tests restoration invariants and POST /api/graph/restore:
    1. Refuses to restore a child node whose parent is QUARANTINED (preserves zero_healthy_under_quarantined).
    2. Restoring the root safely unquarantines valid descendants where all parents are healthy.
    3. Executed actions are NEVER restored (remain quarantined).
    4. Restoring updates released_at and bumps epoch monotonically.
    5. Re-quarantining clears released_at, preserving bi-temporal as-of consistency.
    """
    store = get_graph_store("acme")
    now = int(time.time() * 1000)

    p_id = "fact_parent_restore"
    c_id = "conc_child_restore"
    a_id = "act_exec_restore"

    store.add_node(GraphNode(id=p_id, kind=NodeKind.FACT, label="Parent Fact", status=Status.HEALTHY, valid_from=now))
    store.add_node(GraphNode(id=c_id, kind=NodeKind.CONCLUSION, label="Child Conc", status=Status.HEALTHY, valid_from=now))
    store.add_node(GraphNode(
        id=a_id,
        kind=NodeKind.ACTION,
        label="Executed Action",
        status=Status.HEALTHY,
        valid_from=now,
        action=ActionInfo(type="refund", reversibility=Reversibility.COMPENSABLE, executed=True, gate=GateStatus.PASSED),
    ))

    store.add_edge(GraphEdge(id="e_p_c", source=p_id, target=c_id, type=EdgeType.LEADS_TO))
    store.add_edge(GraphEdge(id="e_c_a", source=c_id, target=a_id, type=EdgeType.LEADS_TO))

    # Quarantine parent cascade
    auditor_service.quarantine(store, p_id, tenant="acme")
    assert store.nodes[p_id].status == Status.QUARANTINED
    assert store.nodes[c_id].status == Status.QUARANTINED
    assert store.nodes[a_id].status == Status.QUARANTINED

    headers = {"Authorization": "Bearer token_acme_auditor"}

    # Attempt 1: Call POST /api/graph/restore on the CHILD while parent is still quarantined!
    resp_child = test_client.post("/api/graph/restore", json={"root_id": c_id}, headers=headers)
    assert resp_child.status_code == 200
    res_child_data = resp_child.json()
    assert res_child_data["restored_count"] == 0
    assert c_id not in res_child_data["restored_nodes"]
    assert store.nodes[c_id].status == Status.QUARANTINED, "Child MUST NOT be restored while parent is quarantined!"

    # Invariants must still be strictly valid
    assert store.check_invariants()["ok"] is True

    # Attempt 2: Restore the parent!
    resp_parent = test_client.post("/api/graph/restore", json={"root_id": p_id}, headers=headers)
    assert resp_parent.status_code == 200
    res_parent_data = resp_parent.json()

    # Parent and child should be restored, but EXECUTED ACTION MUST NOT BE RESTORED!
    assert p_id in res_parent_data["restored_nodes"]
    assert c_id in res_parent_data["restored_nodes"]
    assert a_id not in res_parent_data["restored_nodes"]
    assert store.nodes[p_id].status == Status.HEALTHY
    assert store.nodes[c_id].status == Status.HEALTHY
    assert store.nodes[a_id].status == Status.QUARANTINED

    # Both restored nodes have released_at set
    assert store.nodes[p_id].released_at is not None
    assert store.nodes[c_id].released_at is not None

    # Invariants check
    assert store.check_invariants()["ok"] is True

    # Attempt 3: Re-quarantine parent and verify released_at is wiped clean
    auditor_service.quarantine(store, p_id, tenant="acme")
    assert store.nodes[p_id].status == Status.QUARANTINED
    assert store.nodes[p_id].released_at is None, "released_at must be reset to None upon re-quarantine!"
    assert store.nodes[c_id].status == Status.QUARANTINED
    assert store.nodes[c_id].released_at is None, "released_at must be reset to None upon re-quarantine!"

    # Bi-temporal check: as_of after re-quarantine must NOT return p_id or c_id
    as_of_nodes = store.get_as_of(int(time.time() * 1000) + 10)
    as_of_ids = {n.id for n in as_of_nodes}
    assert p_id not in as_of_ids
    assert c_id not in as_of_ids
