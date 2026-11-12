"""
Regression test: restore_node must be strictly scoped to the target node
and its downstream causal descendants. Unrelated quarantined nodes elsewhere
in the graph must strictly remain quarantined.
"""
from app.graph.queries import GraphStore
from app.models.common import EdgeType, NodeKind, Status
from app.models.graph import GraphEdge, GraphNode
from app.services.auditor import auditor_service


def test_restore_node_scopes_to_causal_descendants_only():
    """
    Scenario:
    - Quarantine Fact A (with downstream Decision A).
    - Quarantine unrelated Fact B (with downstream Decision B).
    - Restore Fact A.
    - Assert: Fact A and Decision A are restored (Status.HEALTHY).
    - Assert: Fact B and Decision B strictly REMAIN quarantined (Status.QUARANTINED).
    """
    store = GraphStore("tenant_restore_scope")

    # Branch A: Fact A -> Decision A
    store.add_node(GraphNode(id="fact_a", kind=NodeKind.FACT, label="Fact A", status=Status.HEALTHY, stale=False, valid_from=0, depth=0))
    store.add_node(GraphNode(id="dec_a", kind=NodeKind.CONCLUSION, label="Decision A", status=Status.HEALTHY, stale=False, valid_from=0, depth=1))
    store.add_edge(GraphEdge(id="edge_a", source="fact_a", target="dec_a", type=EdgeType.LEADS_TO))

    # Branch B: Unrelated Fact B -> Decision B
    store.add_node(GraphNode(id="fact_b", kind=NodeKind.FACT, label="Fact B", status=Status.HEALTHY, stale=False, valid_from=0, depth=0))
    store.add_node(GraphNode(id="dec_b", kind=NodeKind.CONCLUSION, label="Decision B", status=Status.HEALTHY, stale=False, valid_from=0, depth=1))
    store.add_edge(GraphEdge(id="edge_b", source="fact_b", target="dec_b", type=EdgeType.LEADS_TO))

    # Isolated Fact C (quarantined independently)
    store.add_node(GraphNode(id="fact_c", kind=NodeKind.FACT, label="Fact C", status=Status.HEALTHY, stale=False, valid_from=0, depth=0))

    # 1. Quarantine Fact A (cascades to Decision A)
    affected_a = store.cascade_quarantine("fact_a")
    assert any(x["id"] == "fact_a" for x in affected_a)
    assert any(x["id"] == "dec_a" for x in affected_a)

    # 2. Quarantine Fact B (cascades to Decision B)
    affected_b = store.cascade_quarantine("fact_b")
    assert any(x["id"] == "fact_b" for x in affected_b)
    assert any(x["id"] == "dec_b" for x in affected_b)

    # 3. Quarantine Fact C
    store.cascade_quarantine("fact_c")

    # Verify all nodes are quarantined before restore
    assert store.nodes["fact_a"].status == Status.QUARANTINED
    assert store.nodes["dec_a"].status == Status.QUARANTINED
    assert store.nodes["fact_b"].status == Status.QUARANTINED
    assert store.nodes["dec_b"].status == Status.QUARANTINED
    assert store.nodes["fact_c"].status == Status.QUARANTINED

    # 4. Restore Fact A
    restored = store.restore_node("fact_a")

    # Assert: Fact A and Decision A are restored
    assert "fact_a" in restored
    assert "dec_a" in restored
    assert store.nodes["fact_a"].status == Status.HEALTHY
    assert store.nodes["dec_a"].status == Status.HEALTHY

    # Assert: Fact B, Decision B, and Fact C strictly REMAIN quarantined
    assert "fact_b" not in restored
    assert "dec_b" not in restored
    assert "fact_c" not in restored
    assert store.nodes["fact_b"].status == Status.QUARANTINED
    assert store.nodes["dec_b"].status == Status.QUARANTINED
    assert store.nodes["fact_c"].status == Status.QUARANTINED

    # Invariants must remain valid
    inv = store.check_invariants()
    assert inv["ok"] is True, f"Invariants failed: {inv}"


def test_auditor_service_restore_respects_subtree_scope():
    """Verify that auditor_service.restore also maintains strict scoping."""
    store = GraphStore("tenant_auditor_restore_scope")

    store.add_node(GraphNode(id="f1", kind=NodeKind.FACT, label="F1", status=Status.HEALTHY, stale=False, valid_from=0, depth=0))
    store.add_node(GraphNode(id="d1", kind=NodeKind.CONCLUSION, label="D1", status=Status.HEALTHY, stale=False, valid_from=0, depth=1))
    store.add_edge(GraphEdge(id="e1", source="f1", target="d1", type=EdgeType.LEADS_TO))

    store.add_node(GraphNode(id="f2", kind=NodeKind.FACT, label="F2", status=Status.HEALTHY, stale=False, valid_from=0, depth=0))
    store.add_node(GraphNode(id="d2", kind=NodeKind.CONCLUSION, label="D2", status=Status.HEALTHY, stale=False, valid_from=0, depth=1))
    store.add_edge(GraphEdge(id="e2", source="f2", target="d2", type=EdgeType.LEADS_TO))

    store.cascade_quarantine("f1")
    store.cascade_quarantine("f2")

    restored = auditor_service.restore(store, "f1", tenant="tenant_auditor_restore_scope")
    assert set(restored) == {"f1", "d1"}
    assert store.nodes["f1"].status == Status.HEALTHY
    assert store.nodes["d1"].status == Status.HEALTHY
    assert store.nodes["f2"].status == Status.QUARANTINED
    assert store.nodes["d2"].status == Status.QUARANTINED
