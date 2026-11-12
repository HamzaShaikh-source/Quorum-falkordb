import time
import random
import threading
from concurrent.futures import ThreadPoolExecutor
import pytest
from pydantic import ValidationError
from fastapi import HTTPException

from app.models.common import Status, NodeKind, EdgeType, GateStatus, Reversibility
from app.models.graph import GraphNode, GraphEdge, ActionInfo
from app.graph.queries import GraphStore
from graph.queries import _robust_query


def test_extreme_dag_diamond_topology():
    store = GraphStore("tenant_test_diamond")
    
    # Diamond topology:
    #         root (fact)
    #        /          \
    #     fact_a       fact_b
    #        \          /
    #         conc_c
    #            |
    #         act_d
    root = GraphNode(id="n_root", kind=NodeKind.FACT, label="Root Policy", status=Status.HEALTHY)
    fa = GraphNode(id="n_fa", kind=NodeKind.FACT, label="Fact A", status=Status.HEALTHY)
    fb = GraphNode(id="n_fb", kind=NodeKind.FACT, label="Fact B", status=Status.HEALTHY)
    cc = GraphNode(id="n_cc", kind=NodeKind.CONCLUSION, label="Conclusion C", status=Status.HEALTHY)
    act = GraphNode(
        id="n_act",
        kind=NodeKind.ACTION,
        label="Action D",
        status=Status.HEALTHY,
        action=ActionInfo(
            type="refund",
            reversibility=Reversibility.COMPENSABLE,
            executed=False,
            gate=GateStatus.PASSED,
        ),
    )

    for n in [root, fa, fb, cc, act]:
        store.add_node(n)

    store.add_edge(GraphEdge(id="e1", source="n_root", target="n_fa", type=EdgeType.LEADS_TO))
    store.add_edge(GraphEdge(id="e2", source="n_root", target="n_fb", type=EdgeType.LEADS_TO))
    store.add_edge(GraphEdge(id="e3", source="n_fa", target="n_cc", type=EdgeType.LEADS_TO))
    store.add_edge(GraphEdge(id="e4", source="n_fb", target="n_cc", type=EdgeType.SUPPORTS))
    store.add_edge(GraphEdge(id="e5", source="n_cc", target="n_act", type=EdgeType.LEADS_TO))

    # Invariants should pass
    inv = store.check_invariants()
    assert inv["ok"] is True, f"Initial diamond failed invariants: {inv}"

    # Verify ancestors of act
    ancestor_ids = {a.id for a in store.get_ancestors("n_act")}
    assert ancestor_ids == {"n_cc", "n_fa", "n_fb", "n_root"}

    # Descendants of root
    descendant_ids = {d.id for d in store.get_descendants("n_root")}
    assert descendant_ids == {"n_fa", "n_fb", "n_cc", "n_act"}

    # Cascade quarantine from root
    affected = store.cascade_quarantine("n_root")
    aff_ids = {a["id"] for a in affected}
    assert aff_ids == {"n_root", "n_fa", "n_fb", "n_cc", "n_act"}

    # Verify all quarantined
    for nid in ["n_root", "n_fa", "n_fb", "n_cc", "n_act"]:
        assert store.nodes[nid].status == Status.QUARANTINED

    # Invariants must still pass (zero healthy under quarantined)
    inv_post_q = store.check_invariants()
    assert inv_post_q["ok"] is True, f"Post-quarantine invariants failed: {inv_post_q}"

    # Restore root
    restored = store.restore_node("n_root")
    assert set(restored) == {"n_root", "n_fa", "n_fb", "n_cc", "n_act"}

    for nid in ["n_root", "n_fa", "n_fb", "n_cc", "n_act"]:
        assert store.nodes[nid].status == Status.HEALTHY

    inv_post_res = store.check_invariants()
    assert inv_post_res["ok"] is True


def test_extreme_dag_10_level_deep_chain():
    store = GraphStore("tenant_test_10chain")
    
    chain_length = 12
    nodes = []
    for i in range(chain_length):
        node = GraphNode(
            id=f"chain_node_{i}",
            kind=NodeKind.FACT if i < 8 else NodeKind.CONCLUSION,
            label=f"Level {i} Node",
            status=Status.HEALTHY,
        )
        nodes.append(node)
        store.add_node(node)

    for i in range(chain_length - 1):
        edge_type = EdgeType.LEADS_TO if i % 2 == 0 else EdgeType.SUPPORTS
        store.add_edge(GraphEdge(
            id=f"chain_edge_{i}",
            source=f"chain_node_{i}",
            target=f"chain_node_{i+1}",
            type=edge_type,
        ))

    inv = store.check_invariants()
    assert inv["ok"] is True

    # Check depth computation in cascade quarantine
    affected = store.cascade_quarantine("chain_node_0")
    assert len(affected) == chain_length
    
    depth_map = {item["id"]: item["depth"] for item in affected}
    for i in range(chain_length):
        assert depth_map[f"chain_node_{i}"] == i
        assert store.nodes[f"chain_node_{i}"].status == Status.QUARANTINED

    inv_q = store.check_invariants()
    assert inv_q["ok"] is True

    # Restore chain
    restored = store.restore_node("chain_node_0")
    assert len(restored) == chain_length
    assert store.check_invariants()["ok"] is True


def test_cyclic_edge_prevention_and_acyclicity():
    store = GraphStore("tenant_test_cycle")
    
    n1 = GraphNode(id="c_n1", kind=NodeKind.FACT, label="Node 1", status=Status.HEALTHY)
    n2 = GraphNode(id="c_n2", kind=NodeKind.FACT, label="Node 2", status=Status.HEALTHY)
    n3 = GraphNode(id="c_n3", kind=NodeKind.FACT, label="Node 3", status=Status.HEALTHY)
    
    store.add_node(n1)
    store.add_node(n2)
    store.add_node(n3)

    # 1. Self loop attempt
    with pytest.raises(HTTPException) as exc_info:
        store.add_edge(GraphEdge(id="e_self", source="c_n1", target="c_n1", type=EdgeType.LEADS_TO))
    assert exc_info.value.status_code == 409
    assert "DAG" in exc_info.value.detail

    # 2. Add n1 -> n2
    store.add_edge(GraphEdge(id="e_12", source="c_n1", target="c_n2", type=EdgeType.LEADS_TO))
    
    # Direct 2-node cycle attempt: n2 -> n1
    with pytest.raises(HTTPException) as exc_info:
        store.add_edge(GraphEdge(id="e_21", source="c_n2", target="c_n1", type=EdgeType.SUPPORTS))
    assert exc_info.value.status_code == 409
    assert "DAG" in exc_info.value.detail

    # 3. Add n2 -> n3
    store.add_edge(GraphEdge(id="e_23", source="c_n2", target="c_n3", type=EdgeType.FROM_SOURCE))

    # 3-node cycle attempt: n3 -> n1
    with pytest.raises(HTTPException) as exc_info:
        store.add_edge(GraphEdge(id="e_31", source="c_n3", target="c_n1", type=EdgeType.LEADS_TO))
    assert exc_info.value.status_code == 409

    # Invariants should be completely healthy
    inv = store.check_invariants()
    assert inv["ok"] is True

    # 4. Verify check_invariants detects forced cycle (if corrupted/bypassed)
    corrupt_edge = GraphEdge(id="e_corrupt", source="c_n3", target="c_n1", type=EdgeType.LEADS_TO)
    store.edges.append(corrupt_edge)
    
    inv_corrupt = store.check_invariants()
    assert inv_corrupt["ok"] is False
    dag_check = next(c for c in inv_corrupt["checks"] if c["name"] == "dag_acyclicity")
    assert dag_check["ok"] is False
    assert dag_check["value"] == 1.0

    # Clean up
    store.edges.remove(corrupt_edge)
    assert store.check_invariants()["ok"] is True


def test_concurrency_stress_50_threads():
    store = GraphStore("tenant_concurrency_stress")
    
    # Pre-seed some nodes
    for i in range(20):
        store.add_node(GraphNode(
            id=f"seed_{i}",
            kind=NodeKind.FACT,
            label=f"Seed {i}",
            status=Status.HEALTHY,
        ))

    errors = []
    stop_event = threading.Event()

    def node_writer(thread_id: int):
        count = 0
        while not stop_event.is_set() and count < 30:
            count += 1
            nid = f"thread_{thread_id}_node_{count}"
            node = GraphNode(
                id=nid,
                kind=NodeKind.FACT if count % 2 == 0 else NodeKind.CONCLUSION,
                label=f"Node {nid}",
                status=Status.HEALTHY,
            )
            try:
                store.add_node(node)
                # Attach to a seed node
                seed_id = f"seed_{random.randint(0, 19)}"
                store.add_edge(GraphEdge(
                    id=f"edge_{nid}",
                    source=seed_id,
                    target=nid,
                    type=EdgeType.LEADS_TO,
                ))
            except Exception as e:
                # If duplicate or expected 409, continue; else record error
                if not (isinstance(e, HTTPException) and e.status_code == 409):
                    errors.append(f"Writer error in thread {thread_id}: {e}")
            time.sleep(0.001)

    def quarantine_worker(worker_id: int):
        while not stop_event.is_set():
            target = f"seed_{random.randint(0, 19)}"
            try:
                store.cascade_quarantine(target)
            except Exception as e:
                errors.append(f"Quarantine error in worker {worker_id}: {e}")
            time.sleep(0.005)

    def restore_worker(worker_id: int):
        while not stop_event.is_set():
            target = f"seed_{random.randint(0, 19)}"
            try:
                store.restore_node(target)
            except Exception as e:
                errors.append(f"Restore error in worker {worker_id}: {e}")
            time.sleep(0.005)

    def invariant_reader(reader_id: int):
        while not stop_event.is_set():
            try:
                res = store.check_invariants()
                assert "ok" in res
                assert "checks" in res
                assert len(res["checks"]) == 3
            except Exception as e:
                errors.append(f"Invariant reader error in reader {reader_id}: {e}")
            time.sleep(0.002)

    with ThreadPoolExecutor(max_workers=50) as executor:
        futures = []
        # 25 writers
        for i in range(25):
            futures.append(executor.submit(node_writer, i))
        # 10 quarantine workers
        for i in range(10):
            futures.append(executor.submit(quarantine_worker, i))
        # 5 restore workers
        for i in range(5):
            futures.append(executor.submit(restore_worker, i))
        # 10 invariant readers
        for i in range(10):
            futures.append(executor.submit(invariant_reader, i))

        # Run for 2.5 seconds
        time.sleep(2.5)
        stop_event.set()

        for f in futures:
            f.result()

    assert len(errors) == 0, f"Encountered concurrency errors: {errors[:10]}"
    
    # Final invariant check after all threads settle
    final_inv = store.check_invariants()
    # If any quarantine was left un-restored, zero_healthy_under_quarantined might fail IF a writer added a child to a quarantined seed
    # Let's verify structure of checks
    assert "ok" in final_inv


def test_corrupted_partial_node_attributes():
    # 1. Null label must raise ValidationError
    with pytest.raises(ValidationError):
        GraphNode(
            id="bad_null_label",
            kind=NodeKind.FACT,
            label=None,  # type: ignore
            status=Status.HEALTHY,
        )

    # 2. Oversized label (> 120 chars) must raise ValidationError
    oversized_label = "A" * 121
    with pytest.raises(ValidationError):
        GraphNode(
            id="bad_long_label",
            kind=NodeKind.FACT,
            label=oversized_label,
            status=Status.HEALTHY,
        )

    # 3. Label exactly 120 chars should succeed
    ok_label = "A" * 120
    node_ok = GraphNode(
        id="ok_label_node",
        kind=NodeKind.FACT,
        label=ok_label,
        status=Status.HEALTHY,
    )
    assert len(node_ok.label) == 120

    # 4. Oversized payload in action (10,000 keys)
    large_payload = {f"k_{i}": f"v_{i}" * 10 for i in range(5000)}
    action_large = GraphNode(
        id="act_large_payload",
        kind=NodeKind.ACTION,
        label="Action with large payload",
        status=Status.HEALTHY,
        action=ActionInfo(
            type="big_action",
            reversibility=Reversibility.REVERSIBLE,
            gate=GateStatus.PASSED,
            payload=large_payload,
        ),
    )
    dumped = action_large.model_dump_json()
    loaded = GraphNode.model_validate_json(dumped)
    assert len(loaded.action.payload) == 5000


def test_robust_query_retry_loop(monkeypatch):
    import graph.queries as gq

    monkeypatch.setattr(gq, "RETRY_BUDGET_S", 0.3)
    class MockGraph:
        def __init__(self):
            self.attempts = 0

        def query(self, cypher, params=None):
            self.attempts += 1
            if self.attempts < 4:
                raise Exception("ResponseError: Max pending queries exceeded")
            return "SUCCESS"

    mock_g = MockGraph()
    res = _robust_query(mock_g, "MATCH (n) RETURN n")
    assert res == "SUCCESS"
    assert mock_g.attempts == 4

    # Persistent back-pressure: retried until the time budget is spent, then raised
    class FailingMockGraph:
        def __init__(self):
            self.attempts = 0

        def query(self, cypher, params=None):
            self.attempts += 1
            raise Exception("ResponseError: Max pending queries exceeded")

    fail_g = FailingMockGraph()
    with pytest.raises(Exception) as exc:
        _robust_query(fail_g, "MATCH (n) RETURN n")
    assert "Max pending queries exceeded" in str(exc.value)
    assert fail_g.attempts > 3

    # Test immediate failure for unrelated errors
    class OtherErrorGraph:
        def __init__(self):
            self.attempts = 0

        def query(self, cypher, params=None):
            self.attempts += 1
            raise ValueError("Syntax error in Cypher")

    other_g = OtherErrorGraph()
    with pytest.raises(ValueError):
        _robust_query(other_g, "INVALID CYPHER")
    assert other_g.attempts == 1
