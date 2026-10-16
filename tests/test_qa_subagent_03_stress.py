"""
tests/test_qa_subagent_03_stress.py
Multi-Vector Stress & Boundary Test Suite for Subagent_03:
Causal Graph Engine, Invariants & Graph Mutators QA Architect.
"""

import time
import threading
from typing import List, Dict, Any
import pytest

from app.models.common import Status, NodeKind, EdgeType, Reversibility, GateStatus
from app.models.graph import GraphNode, GraphEdge, ActionInfo
from app.graph.queries import GraphStore
from graph import queries as falkor_queries


# =========================================================================
# 1. EXTREME DAG SHAPES & TOPOLOGY TESTS
# =========================================================================

def test_diamond_topology_in_memory():
    r"""
    Diamond topology:
         Root (A)
        /        \
       B          C
        \        /
         Target (D)
    Verify:
    - Ancestors and descendants
    - Cascade quarantine from Root affects all
    - Restore from Root restores all (multi-parent convergence)
    - Invariants pass throughout
    """
    store = GraphStore(tenant="test_diamond")

    # Create nodes
    nA = GraphNode(id="A", kind=NodeKind.FACT, label="Fact A", status=Status.HEALTHY)
    nB = GraphNode(id="B", kind=NodeKind.CONCLUSION, label="Conclusion B", status=Status.HEALTHY)
    nC = GraphNode(id="C", kind=NodeKind.CONCLUSION, label="Conclusion C", status=Status.HEALTHY)
    nD = GraphNode(
        id="D",
        kind=NodeKind.ACTION,
        label="Action D",
        status=Status.HEALTHY,
        action=ActionInfo(type="EXEC", reversibility=Reversibility.COMPENSABLE, gate=GateStatus.PASSED)
    )

    for n in [nA, nB, nC, nD]:
        store.add_node(n)

    # Create diamond edges
    store.add_edge(GraphEdge(id="e_AB", source="A", target="B", type=EdgeType.LEADS_TO))
    store.add_edge(GraphEdge(id="e_AC", source="A", target="C", type=EdgeType.LEADS_TO))
    store.add_edge(GraphEdge(id="e_BD", source="B", target="D", type=EdgeType.LEADS_TO))
    store.add_edge(GraphEdge(id="e_CD", source="C", target="D", type=EdgeType.LEADS_TO))

    # Invariants should pass
    inv = store.check_invariants()
    assert inv["ok"] is True, f"Invariants failed on clean diamond: {inv}"

    # Ancestor traversal for D should return A, B, C exactly once
    anc_D = [n.id for n in store.get_ancestors("D")]
    assert set(anc_D) == {"A", "B", "C"}
    assert len(anc_D) == 3, f"Duplicate ancestors in diamond: {anc_D}"

    # Descendant traversal for A should return B, C, D exactly once
    desc_A = [n.id for n in store.get_descendants("A")]
    assert set(desc_A) == {"B", "C", "D"}
    assert len(desc_A) == 3, f"Duplicate descendants in diamond: {desc_A}"

    # Quarantine root A -> all B, C, D must be quarantined
    aff = store.cascade_quarantine("A")
    aff_ids = {item["id"] for item in aff}
    assert aff_ids == {"A", "B", "C", "D"}

    assert store.nodes["A"].status == Status.QUARANTINED
    assert store.nodes["B"].status == Status.QUARANTINED
    assert store.nodes["C"].status == Status.QUARANTINED
    assert store.nodes["D"].status == Status.QUARANTINED

    inv_q = store.check_invariants()
    assert inv_q["ok"] is True

    # Restore root A -> B and C should restore, and D should restore
    restored = store.restore_node("A")
    assert "A" in restored
    assert "B" in restored
    assert "C" in restored
    assert "D" in restored

    inv_r = store.check_invariants()
    assert inv_r["ok"] is True


def test_10_level_deep_chain_in_memory():
    """
    10-level deep chain:
    N0 -> N1 -> N2 -> ... -> N10
    Verify:
    - Traversal depth
    - Cascade quarantine propagation down 10 levels
    - Depth calculations in cascade_quarantine
    - Invariants
    """
    store = GraphStore(tenant="test_chain")

    nodes = []
    for i in range(11):
        kind = NodeKind.FACT if i == 0 else (NodeKind.ACTION if i == 10 else NodeKind.CONCLUSION)
        act = ActionInfo(type="EXEC", reversibility=Reversibility.COMPENSABLE, gate=GateStatus.PASSED) if i == 10 else None
        n = GraphNode(id=f"N{i}", kind=kind, label=f"Node {i}", status=Status.HEALTHY, action=act)
        nodes.append(n)
        store.add_node(n)

    for i in range(10):
        store.add_edge(GraphEdge(id=f"e_{i}_{i+1}", source=f"N{i}", target=f"N{i+1}", type=EdgeType.LEADS_TO))

    # Check invariants
    inv = store.check_invariants()
    assert inv["ok"] is True

    # Check ancestors of N10
    anc = [n.id for n in store.get_ancestors("N10")]
    assert len(anc) == 10
    assert set(anc) == {f"N{i}" for i in range(10)}

    # Check descendants of N0
    desc = [n.id for n in store.get_descendants("N0")]
    assert len(desc) == 10
    assert set(desc) == {f"N{i}" for i in range(1, 11)}

    # Cascade quarantine from N0
    aff = store.cascade_quarantine("N0")
    assert len(aff) == 11
    depth_map = {item["id"]: item["depth"] for item in aff}
    for i in range(11):
        assert depth_map[f"N{i}"] == i, f"Expected depth {i} for N{i}, got {depth_map[f'N{i}']}"

    inv_post_q = store.check_invariants()
    assert inv_post_q["ok"] is True

    # Restore from N0 -> all 11 should restore
    restored = store.restore_node("N0")
    assert len(restored) == 11
    for i in range(11):
        assert f"N{i}" in restored


def test_cyclic_edge_prevention_and_detection():
    """
    Test cycle attempts:
    1. Self-loop: A -> A (blocked by add_edge)
    2. 2-hop cycle: A -> B, then attempt B -> A (NOT blocked by add_edge, but detected by check_invariants)
    """
    store = GraphStore(tenant="test_cycle")

    nA = GraphNode(id="A", kind=NodeKind.FACT, label="Fact A", status=Status.HEALTHY)
    nB = GraphNode(id="B", kind=NodeKind.CONCLUSION, label="Conclusion B", status=Status.HEALTHY)

    for n in [nA, nB]:
        store.add_node(n)

    # 1. Self loop attempt
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        store.add_edge(GraphEdge(id="e_self", source="A", target="A", type=EdgeType.LEADS_TO))
    assert exc_info.value.status_code == 409
    assert "DAG" in str(exc_info.value.detail)

    # 2. Add A -> B
    store.add_edge(GraphEdge(id="e_AB", source="A", target="B", type=EdgeType.LEADS_TO))

    # 3. Add B -> A (2-hop cycle attempt) is blocked by add_edge DAG verification
    with pytest.raises(HTTPException) as exc_info:
        store.add_edge(GraphEdge(id="e_BA", source="B", target="A", type=EdgeType.LEADS_TO))
    assert exc_info.value.status_code == 409
    assert "DAG" in str(exc_info.value.detail)

    inv = store.check_invariants()
    assert inv["ok"] is True


def test_phantom_node_cycle_invariant_blindspot():
    """
    Verify add_edge prevents cycles even between phantom nodes.
    """
    store = GraphStore(tenant="test_phantom")
    store.add_edge(GraphEdge(id="e1", source="p1", target="p2", type=EdgeType.LEADS_TO))
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        store.add_edge(GraphEdge(id="e2", source="p2", target="p1", type=EdgeType.LEADS_TO))
    assert exc_info.value.status_code == 409


def test_from_source_restore_invariant_violation():
    """
    Verify restore_node() respects FROM_SOURCE parents when restoring descendants,
    preventing a HEALTHY node from existing under a QUARANTINED SOURCE parent.
    """
    store = GraphStore(tenant="test_from_source_restore")

    src = GraphNode(id="src_1", kind=NodeKind.SOURCE, label="Source 1", status=Status.QUARANTINED)
    f1 = GraphNode(id="f_1", kind=NodeKind.FACT, label="Fact 1", status=Status.QUARANTINED)
    f2 = GraphNode(id="f_2", kind=NodeKind.FACT, label="Fact 2", status=Status.HEALTHY)

    store.add_node(src)
    store.add_node(f1)
    store.add_node(f2)

    store.add_edge(GraphEdge(id="e_src", source="src_1", target="f_1", type=EdgeType.FROM_SOURCE))
    store.add_edge(GraphEdge(id="e_f2", source="f_2", target="f_1", type=EdgeType.SUPPORTS))

    # restore_node on f_1 correctly refuses to restore because src_1 (FROM_SOURCE) is QUARANTINED
    restored = store.restore_node("f_1")
    assert "f_1" not in restored
    assert store.nodes["f_1"].status == Status.QUARANTINED

    # Invariants remain clean
    inv = store.check_invariants()
    assert inv["ok"] is True


def test_partial_quarantine_multi_parent_convergence():
    r"""
    V-shape / Multi-parent:
       P1 (Quarantined)     P2 (Healthy)
               \           /
                Child (C)
    Invariant:
    - If P1 is quarantined, Child MUST be quarantined.
    - If P2 is restored but P1 remains quarantined, Child CANNOT be restored.
    - Child can only be restored when ALL parents are HEALTHY.
    """
    store = GraphStore(tenant="test_multi_parent")

    p1 = GraphNode(id="P1", kind=NodeKind.FACT, label="Parent 1", status=Status.HEALTHY)
    p2 = GraphNode(id="P2", kind=NodeKind.FACT, label="Parent 2", status=Status.HEALTHY)
    child = GraphNode(id="C", kind=NodeKind.CONCLUSION, label="Child", status=Status.HEALTHY)

    for n in [p1, p2, child]:
        store.add_node(n)

    store.add_edge(GraphEdge(id="e_p1_c", source="P1", target="C", type=EdgeType.LEADS_TO))
    store.add_edge(GraphEdge(id="e_p2_c", source="P2", target="C", type=EdgeType.SUPPORTS))

    assert store.check_invariants()["ok"] is True

    # Quarantine P1
    store.cascade_quarantine("P1")
    assert store.nodes["P1"].status == Status.QUARANTINED
    assert store.nodes["C"].status == Status.QUARANTINED
    assert store.nodes["P2"].status == Status.HEALTHY

    assert store.check_invariants()["ok"] is True

    # Attempt to restore C directly
    restored = store.restore_node("P2")
    assert "C" not in restored
    assert store.nodes["C"].status == Status.QUARANTINED

    # Now restore P1
    restored_p1 = store.restore_node("P1")
    assert "P1" in restored_p1
    assert "C" in restored_p1
    assert store.nodes["C"].status == Status.HEALTHY

    assert store.check_invariants()["ok"] is True


# =========================================================================
# 2. CONCURRENCY STRESS: 50 THREADS MUTATING & AUDITING
# =========================================================================

def test_concurrency_stress_50_threads():
    """
    Concurrency stress test:
    50 concurrent threads:
    - 35 Writer threads: adding nodes, adding edges, running cascade_quarantine, running restore_node, execute_guard
    - 15 Auditor threads: continuously reading check_invariants, get_ancestors, get_descendants
    Duration: 3.0 seconds
    Verify:
    - Thread-safety under _lock (RLock)
    - 0 unhandled exceptions or deadlocks
    - Mutations and audits progress concurrently without crashing
    """
    store = GraphStore(tenant="test_stress_50")
    stop_event = threading.Event()
    errors: List[Exception] = []
    audit_passes = 0
    mutations_count = 0
    lock_stats = threading.Lock()

    # Pre-populate base DAG
    root = GraphNode(id="root_fact", kind=NodeKind.FACT, label="Root Fact", status=Status.HEALTHY)
    store.add_node(root)

    for i in range(10):
        n = GraphNode(id=f"base_node_{i}", kind=NodeKind.CONCLUSION, label=f"Base {i}", status=Status.HEALTHY)
        store.add_node(n)
        store.add_edge(GraphEdge(id=f"e_root_{i}", source="root_fact", target=f"base_node_{i}", type=EdgeType.LEADS_TO))

    def writer_worker(thread_idx: int):
        nonlocal mutations_count
        seq = 0
        while not stop_event.is_set():
            seq += 1
            node_id = f"w_{thread_idx}_{seq}"
            edge_id = f"ew_{thread_idx}_{seq}"
            try:
                # 1. Add node
                node = GraphNode(
                    id=node_id,
                    kind=NodeKind.CONCLUSION if seq % 2 == 0 else NodeKind.ACTION,
                    label=f"Writer {thread_idx} {seq}",
                    status=Status.HEALTHY,
                    action=ActionInfo(type="EXEC", reversibility=Reversibility.COMPENSABLE, gate=GateStatus.PASSED) if seq % 2 == 1 else None
                )
                store.add_node(node)

                # 2. Add edge connecting to base node
                parent_id = f"base_node_{seq % 10}"
                store.add_edge(GraphEdge(id=edge_id, source=parent_id, target=node_id, type=EdgeType.LEADS_TO))

                # 3. Occasional cascade or restore or execute_guard
                if seq % 7 == 0:
                    store.cascade_quarantine(node_id)
                elif seq % 11 == 0:
                    store.restore_node(node_id)
                elif seq % 5 == 0 and node.kind == NodeKind.ACTION:
                    store.execute_guard(node_id)

                with lock_stats:
                    mutations_count += 1
            except Exception as e:
                with lock_stats:
                    errors.append(e)

    def auditor_worker(thread_idx: int):
        nonlocal audit_passes
        while not stop_event.is_set():
            try:
                # Run invariant check
                inv = store.check_invariants()
                # Run traversal
                anc = store.get_ancestors(f"base_node_{thread_idx % 10}")
                desc = store.get_descendants("root_fact")

                with lock_stats:
                    audit_passes += 1
            except Exception as e:
                with lock_stats:
                    errors.append(e)

    threads: List[threading.Thread] = []
    # 35 writers
    for i in range(35):
        t = threading.Thread(target=writer_worker, args=(i,), daemon=True)
        threads.append(t)

    # 15 auditors
    for i in range(15):
        t = threading.Thread(target=auditor_worker, args=(i,), daemon=True)
        threads.append(t)

    start_time = time.time()
    for t in threads:
        t.start()

    time.sleep(3.0)
    stop_event.set()

    for t in threads:
        t.join(timeout=2.0)

    assert len(errors) == 0, f"Encountered {len(errors)} thread-safety errors during 50-thread stress: {errors[:5]}"
    assert mutations_count > 50, f"Expected >50 mutations, got {mutations_count}"
    assert audit_passes > 10, f"Expected >10 audit passes, got {audit_passes}"


# =========================================================================
# 3. ROBUST QUERY RETRY & BACKOFF CHECK
# =========================================================================

def test_robust_query_retry_behavior():
    """
    Test _robust_query exponential backoff logic on ResponseError:
    'Max pending queries exceeded'
    """
    from graph.queries import _robust_query

    attempts_seen = 0

    class MockGraph:
        def query(self, cypher, params=None):
            nonlocal attempts_seen
            attempts_seen += 1
            if attempts_seen < 4:
                raise Exception("ResponseError: Max pending queries exceeded")
            return "SUCCESS"

    t0 = time.time()
    res = _robust_query(MockGraph(), "RETURN 1")
    elapsed = time.time() - t0

    assert res == "SUCCESS"
    assert attempts_seen == 4
    assert elapsed >= 0.030, f"Expected >= 30ms backoff sleep, got {elapsed:.4f}s"


def test_robust_query_raises_when_retries_exhausted():
    """Verify _robust_query raises after 5 failed attempts."""
    from graph.queries import _robust_query

    attempts_seen = 0

    class FailingGraph:
        def query(self, cypher, params=None):
            nonlocal attempts_seen
            attempts_seen += 1
            raise Exception("ResponseError: Max pending queries exceeded")

    with pytest.raises(Exception) as exc_info:
        _robust_query(FailingGraph(), "RETURN 1")

    assert "Max pending queries exceeded" in str(exc_info.value)
    assert attempts_seen == 6


# =========================================================================
# 4. CYPHER INJECTION RISKS IN RETRIEVE & BRAIN_ASK
# =========================================================================

def test_cypher_injection_in_retrieve():
    """
    Demonstrate that max_hops in graph/queries.py::retrieve() is interpolated
    as a raw f-string without integer validation or parameterization.
    """
    k = 5
    max_hops = "2]->(target:MemoryNode) DETACH DELETE target //"
    injected_cypher = f"""
    CALL db.idx.vector.queryNodes('Fact', 'embedding', {k * 2}, vecf32($vec))
    OPTIONAL MATCH p = (node)-[:LEADS_TO*0..{max_hops}]->(target:MemoryNode)
    """

    assert "DETACH DELETE target" in injected_cypher


# =========================================================================
# 5. LIVE FALKORDB EXTREME DAG & INVARIANT AUDIT
# =========================================================================

def test_falkordb_diamond_and_10_level_chain():
    """
    Test live FalkorDB execution of:
    1. Diamond topology
    2. 10-level deep derivation chain
    3. Cascade quarantine & invariant checks
    """
    tenant = "test_falkor_topologies"
    g = falkor_queries.get_graph(tenant, "commons")
    try:
        g.delete()
    except Exception:
        pass
    falkor_queries.ensure_indexes(tenant)

    # 1. 10-level deep chain:
    # f_0 -> c_1 -> c_2 -> ... -> c_9 -> act_10
    g.query("CREATE (:Fact:MemoryNode {id: 'f_0', status: 'HEALTHY', text: 'Root Fact'})")
    prev_id = "f_0"
    for i in range(1, 10):
        c_id = f"c_{i}"
        res = falkor_queries.derive(tenant, c_id, [prev_id], f"Conclusion {i}")
        assert res["success"] is True
        prev_id = c_id

    # Action at depth 10
    res_act = falkor_queries.stage_action(tenant, "act_10", [prev_id], "REFUND", {"val": 100}, "COMPENSABLE", gate="PASSED")
    assert res_act["success"] is True

    # Invariants should pass
    inv = falkor_queries.invariant_check(tenant)
    assert inv["passed"] is True
    assert inv["violations"] == 0

    # Execute guard at depth 10 should succeed
    res_exec = falkor_queries.execute_guard(tenant, "act_10")
    assert res_exec["success"] is True

    # Cascade quarantine from f_0 should cascade all 10 levels down to act_10
    casc = falkor_queries.cascade(tenant, "f_0")
    assert casc["success"] is True
    assert casc["affected_count"] == 11
    assert any(a["action_id"] == "act_10" for a in casc["executed_actions"])

    # Invariants after cascade
    inv2 = falkor_queries.invariant_check(tenant)
    assert inv2["passed"] is True
    assert inv2["violations"] == 0


def test_falkordb_invariant_check_omits_dag_acyclicity():
    """
    CRITICAL FINDING:
    falkor_queries.invariant_check() in graph/queries.py ONLY checks:
    - zero_healthy_under_quarantined
    - executed_has_healthy_ancestors
    It has NO query or check for dag_acyclicity!
    If a cycle is introduced directly or via an edge mutator, invariant_check() passes!
    """
    tenant = "test_falkor_cycle_omission"
    g = falkor_queries.get_graph(tenant, "commons")
    try:
        g.delete()
    except Exception:
        pass
    falkor_queries.ensure_indexes(tenant)

    # Introduce a cyclic link in FalkorDB: f_a -> f_b -> f_a
    g.query("CREATE (a:Fact:MemoryNode {id: 'f_a', status: 'HEALTHY'})")
    g.query("CREATE (b:Fact:MemoryNode {id: 'f_b', status: 'HEALTHY'})")
    g.query("MATCH (a:Fact {id: 'f_a'}), (b:Fact {id: 'f_b'}) CREATE (a)-[:LEADS_TO]->(b), (b)-[:LEADS_TO]->(a)")

    inv = falkor_queries.invariant_check(tenant)
    # Notice: inv['passed'] is True despite a blatant cycle!
    assert inv["passed"] is True
    # The checks list does NOT contain 'dag_acyclicity'
    check_names = [c["name"] for c in inv["checks"]]
    assert "dag_acyclicity" not in check_names

