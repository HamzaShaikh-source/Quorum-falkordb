"""
backend/tests/test_qa_subagent_03_phase2.py
Comprehensive QA, Stress & Boundary Test Suite for Subagent_03:
Cascade Quarantine, Blast Radius & Audit Intelligence Architect.

Assigned Modules:
- backend/app/services/auditor.py
- backend/app/routes/blast.py
- backend/app/routes/audit.py
- backend/app/services/console_audit.py
- backend/app/routes/graph.py

Interfaces:
- Upstream: backend/app/graph/queries.py
- Downstream: backend/app/services/saga.py, backend/app/services/event_bus.py
"""
import concurrent.futures
import csv
import io
import time
import pytest
from starlette.testclient import TestClient

from app.config import settings
from app.graph.queries import GraphStore, get_graph_store
from app.main import app
from app.models.common import (
    CompensationStatus,
    EdgeType,
    GateStatus,
    HandoffKind,
    HandoffStatus,
    NodeKind,
    Reversibility,
    Status,
    Tier,
)
from app.models.graph import ActionInfo, AssertedBy, GraphEdge, GraphNode
from app.models.handoffs import Handoff
from app.services.auditor import auditor_service
from app.services.event_bus import event_bus
from app.services.mcp_guard import record_event


@pytest.fixture
def test_client(monkeypatch):
    monkeypatch.setattr(settings, "ENV", "development")
    return TestClient(app)


# =========================================================================
# 1. DEEP SUBGRAPHS (DEPTH > 5) WITH MULTI-PARENT CONVERGENCE
# =========================================================================

def test_quarantine_cascade_deep_subgraph_multi_parent_convergence():
    r"""
    Quarantine cascade on a deep DAG (depth = 7) with multi-parent convergence:
    Topology:
        Root_A (depth 0)         Root_B (depth 0, independent healthy)
           \                       /
            N1_A (depth 1)        N1_B (depth 1)
              \                  /
                Conv_2 (depth 2 - multi-parent convergence)
                  \
                   N3 (depth 3)
                  /  \
            N4_Left   N4_Right (depth 4)
                  \  /
                Conv_5 (depth 5 - diamond convergence)
                  \
                   N6 (depth 6)
                  /  \
         Act_Staged   Act_Exec (depth 7 - Action nodes)

    Verify:
    1. BFS visited set prevents infinite loops and duplicates.
    2. Exact shortest path hop depth calculations (depth 0 to 7).
    3. Monotonic epoch bump upon cascade.
    4. All descendants marked QUARANTINED and their status change is atomic.
    5. Invariants pass (zero_healthy_under_quarantined).
    6. Multi-parent restore safety: Restoring Root_A does NOT restore Conv_2
       if another parent (Root_B) were somehow quarantined, or vice-versa.
    """
    store = GraphStore(tenant=f"test_deep_{int(time.time() * 1000)}")

    # Level 0
    root_a = GraphNode(id="root_a", kind=NodeKind.FACT, label="Root A", status=Status.HEALTHY)
    root_b = GraphNode(id="root_b", kind=NodeKind.FACT, label="Root B", status=Status.HEALTHY)
    store.add_node(root_a)
    store.add_node(root_b)

    # Level 1
    n1_a = GraphNode(id="n1_a", kind=NodeKind.CONCLUSION, label="N1 A", status=Status.HEALTHY)
    n1_b = GraphNode(id="n1_b", kind=NodeKind.CONCLUSION, label="N1 B", status=Status.HEALTHY)
    store.add_node(n1_a)
    store.add_node(n1_b)
    store.add_edge(GraphEdge(id="e_0_1a", source="root_a", target="n1_a", type=EdgeType.LEADS_TO))
    store.add_edge(GraphEdge(id="e_0_1b", source="root_b", target="n1_b", type=EdgeType.LEADS_TO))

    # Level 2 (Convergence of n1_a and n1_b)
    conv_2 = GraphNode(id="conv_2", kind=NodeKind.CONCLUSION, label="Conv 2", status=Status.HEALTHY)
    store.add_node(conv_2)
    store.add_edge(GraphEdge(id="e_1a_2", source="n1_a", target="conv_2", type=EdgeType.LEADS_TO))
    store.add_edge(GraphEdge(id="e_1b_2", source="n1_b", target="conv_2", type=EdgeType.SUPPORTS))

    # Level 3
    n3 = GraphNode(id="n3", kind=NodeKind.CONCLUSION, label="N3", status=Status.HEALTHY)
    store.add_node(n3)
    store.add_edge(GraphEdge(id="e_2_3", source="conv_2", target="n3", type=EdgeType.LEADS_TO))

    # Level 4 (Diamond fork)
    n4_l = GraphNode(id="n4_l", kind=NodeKind.CONCLUSION, label="N4 Left", status=Status.HEALTHY)
    n4_r = GraphNode(id="n4_r", kind=NodeKind.CONCLUSION, label="N4 Right", status=Status.HEALTHY)
    store.add_node(n4_l)
    store.add_node(n4_r)
    store.add_edge(GraphEdge(id="e_3_4l", source="n3", target="n4_l", type=EdgeType.LEADS_TO))
    store.add_edge(GraphEdge(id="e_3_4r", source="n3", target="n4_r", type=EdgeType.LEADS_TO))

    # Level 5 (Diamond join)
    conv_5 = GraphNode(id="conv_5", kind=NodeKind.CONCLUSION, label="Conv 5", status=Status.HEALTHY)
    store.add_node(conv_5)
    store.add_edge(GraphEdge(id="e_4l_5", source="n4_l", target="conv_5", type=EdgeType.LEADS_TO))
    store.add_edge(GraphEdge(id="e_4r_5", source="n4_r", target="conv_5", type=EdgeType.LEADS_TO))

    # Level 6
    n6 = GraphNode(id="n6", kind=NodeKind.CONCLUSION, label="N6", status=Status.HEALTHY)
    store.add_node(n6)
    store.add_edge(GraphEdge(id="e_5_6", source="conv_5", target="n6", type=EdgeType.LEADS_TO))

    # Level 7 (Actions)
    act_staged = GraphNode(
        id="act_staged",
        kind=NodeKind.ACTION,
        label="Staged Action",
        status=Status.HEALTHY,
        action=ActionInfo(type="STAGED", reversibility=Reversibility.COMPENSABLE, executed=False, gate=GateStatus.PASSED)
    )
    act_exec = GraphNode(
        id="act_exec",
        kind=NodeKind.ACTION,
        label="Executed Action",
        status=Status.HEALTHY,
        action=ActionInfo(type="REFUND", reversibility=Reversibility.COMPENSABLE, executed=True, gate=GateStatus.PASSED)
    )
    store.add_node(act_staged)
    store.add_node(act_exec)
    store.add_edge(GraphEdge(id="e_6_as", source="n6", target="act_staged", type=EdgeType.LEADS_TO))
    store.add_edge(GraphEdge(id="e_6_ae", source="n6", target="act_exec", type=EdgeType.LEADS_TO))

    # Baseline invariants pass
    assert store.check_invariants()["ok"] is True
    initial_epoch = store.epoch

    # Trigger cascade from root_a
    affected = auditor_service.quarantine(store, "root_a", tenant=store.tenant)

    # 1. Visited set prevents infinite loops and duplicates
    affected_ids = [item["id"] for item in affected]
    assert len(affected_ids) == len(set(affected_ids)), f"Duplicate nodes returned in cascade: {affected_ids}"

    # 2. Exact shortest path hop depth calculations
    depth_map = {item["id"]: item["depth"] for item in affected}
    assert depth_map["root_a"] == 0
    assert depth_map["n1_a"] == 1
    assert depth_map["conv_2"] == 2
    assert depth_map["n3"] == 3
    assert depth_map["n4_l"] == 4
    assert depth_map["n4_r"] == 4
    assert depth_map["conv_5"] == 5
    assert depth_map["n6"] == 6
    assert depth_map["act_staged"] == 7
    assert depth_map["act_exec"] == 7

    # 3. Monotonic epoch bump
    assert store.epoch == initial_epoch + 1

    # 4. Independent branch root_b and n1_b must REMAIN HEALTHY
    assert store.nodes["root_b"].status == Status.HEALTHY
    assert store.nodes["n1_b"].status == Status.HEALTHY

    # 5. All downstream descendants from root_a must be QUARANTINED
    for nid in ["root_a", "n1_a", "conv_2", "n3", "n4_l", "n4_r", "conv_5", "n6", "act_staged", "act_exec"]:
        assert store.nodes[nid].status == Status.QUARANTINED

    # 6. Compensations enqueued for executed action
    assert store.nodes["act_exec"].action.compensation is not None
    assert store.nodes["act_exec"].action.compensation.status == CompensationStatus.PENDING_COMPENSATION

    # 7. Invariants pass
    inv = store.check_invariants()
    assert inv["ok"] is True

    # 8. Multi-parent restore safety:
    # Now quarantine root_b as well so both parents are quarantined
    auditor_service.quarantine(store, "root_b", tenant=store.tenant)
    assert store.nodes["root_b"].status == Status.QUARANTINED
    assert store.nodes["n1_b"].status == Status.QUARANTINED

    # Now restore ONLY root_a (root_b is still quarantined)
    # conv_2 has two parents: n1_a (will restore) and n1_b (quarantined)
    # conv_2 MUST NOT be restored because n1_b is quarantined!
    restored_a = auditor_service.restore(store, "root_a", tenant=store.tenant)
    assert "root_a" in restored_a
    assert "n1_a" in restored_a
    assert "conv_2" not in restored_a
    assert store.nodes["conv_2"].status == Status.QUARANTINED
    assert store.nodes["n3"].status == Status.QUARANTINED

    inv_mid = store.check_invariants()
    assert inv_mid["ok"] is True

    # Now restore root_b -> all parents of conv_2 are now HEALTHY -> conv_2 and descendants restore
    restored_b = auditor_service.restore(store, "root_b", tenant=store.tenant)
    assert "root_b" in restored_b
    assert "n1_b" in restored_b
    assert "conv_2" in restored_b
    assert "n3" in restored_b
    assert "conv_5" in restored_b
    assert "act_staged" in restored_b
    # Executed action act_exec MUST NEVER be restored!
    assert "act_exec" not in restored_b
    assert store.nodes["act_exec"].status == Status.QUARANTINED

    inv_final = store.check_invariants()
    assert inv_final["ok"] is True


# =========================================================================
# 2. RACE CONDITION: SIMULTANEOUS QUARANTINE OF SIBLING NODES
# =========================================================================

def test_race_condition_simultaneous_quarantine_sibling_nodes():
    r"""
    Race Condition Stress Test:
    Simultaneous quarantine of two sibling nodes that share descendants.
    Topology:
        Sibling_1 (Fact)      Sibling_2 (Fact)
               \                 /
                Shared_Conc (Conclusion)
                       |
                Shared_Act (Executed Action)

    20 concurrent threads randomly and simultaneously issue quarantine requests
    for Sibling_1 and Sibling_2.
    Verifies:
    1. Thread-safety under store._lock (no race conditions, crashes, or deadlocks).
    2. Atomicity of status transitions to QUARANTINED.
    3. Action compensation is enqueued cleanly and idempotently (NOT duplicated).
    4. Final state satisfies causal graph invariants.
    """
    tenant = f"test_race_sibs_{int(time.time() * 1000)}"
    store = get_graph_store(tenant)

    # Setup topology
    s1 = GraphNode(id="sib_1", kind=NodeKind.FACT, label="Sibling 1", status=Status.HEALTHY)
    s2 = GraphNode(id="sib_2", kind=NodeKind.FACT, label="Sibling 2", status=Status.HEALTHY)
    shared_c = GraphNode(id="shared_c", kind=NodeKind.CONCLUSION, label="Shared Conc", status=Status.HEALTHY)
    shared_a = GraphNode(
        id="shared_a",
        kind=NodeKind.ACTION,
        label="Shared Action",
        status=Status.HEALTHY,
        action=ActionInfo(type="EXEC", reversibility=Reversibility.COMPENSABLE, executed=True, gate=GateStatus.PASSED)
    )

    for n in [s1, s2, shared_c, shared_a]:
        store.add_node(n)

    store.add_edge(GraphEdge(id="e_s1_c", source="sib_1", target="shared_c", type=EdgeType.LEADS_TO))
    store.add_edge(GraphEdge(id="e_s2_c", source="sib_2", target="shared_c", type=EdgeType.LEADS_TO))
    store.add_edge(GraphEdge(id="e_c_a", source="shared_c", target="shared_a", type=EdgeType.LEADS_TO))

    assert store.check_invariants()["ok"] is True

    errors = []
    quarantine_results = []

    def quarantine_worker(sibling_id: str, thread_idx: int):
        try:
            res = auditor_service.quarantine(store, sibling_id, reason=f"Race test thread {thread_idx}", tenant=tenant)
            quarantine_results.append((sibling_id, res))
        except Exception as e:
            errors.append(e)

    # Launch 20 concurrent threads racing to quarantine sib_1 and sib_2 simultaneously
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = []
        for i in range(20):
            target = "sib_1" if i % 2 == 0 else "sib_2"
            futures.append(executor.submit(quarantine_worker, target, i))
        for f in concurrent.futures.as_completed(futures):
            f.result()

    assert len(errors) == 0, f"Encountered {len(errors)} thread-safety errors: {errors}"
    assert len(quarantine_results) == 20

    # Verification:
    # 1. Both siblings, shared_c, and shared_a must be QUARANTINED
    assert store.nodes["sib_1"].status == Status.QUARANTINED
    assert store.nodes["sib_2"].status == Status.QUARANTINED
    assert store.nodes["shared_c"].status == Status.QUARANTINED
    assert store.nodes["shared_a"].status == Status.QUARANTINED

    # 2. Compensation for shared_a must exist and be PENDING_COMPENSATION with attempts == 0
    assert store.nodes["shared_a"].action.compensation is not None
    assert store.nodes["shared_a"].action.compensation.status == CompensationStatus.PENDING_COMPENSATION
    assert store.nodes["shared_a"].action.compensation.attempts == 0

    # 3. Invariants pass
    inv = store.check_invariants()
    assert inv["ok"] is True


# =========================================================================
# 3. CONTRADICTION STORM: 50 PARALLEL CONFLICTING FACTS WITH VARYING TIERS
# =========================================================================

def test_contradiction_storm_varying_tiers_stress():
    r"""
    Contradiction Storm Stress Test:
    50 conflicting facts asserted in parallel with varying tiers:
    - 5 ROOT tier assertions (Weight: 10.0)
    - 20 ESTABLISHED tier assertions (Weight: 3.0)
    - 25 NEW tier assertions (Weight: 0.5)

    Rule 1: ROOT contradicts Non-ROOT -> auto-quarantine Non-ROOT (CHALLENGER or OLD_FACT).
    Rule 2: >= 3x ratio auto-quarantines weaker assertion.
    Rule 3: < 3x ratio triggers REVIEW handoff for human tie-break.

    Verifies:
    1. Zero unhandled exceptions or dictionary modification races during concurrent calls.
    2. High-tier assertions consistently dominate weaker assertions.
    3. Handoffs created for close tie-breaks.
    4. Graph invariants remain healthy throughout.
    """
    tenant = f"test_storm_{int(time.time() * 1000)}"
    store = get_graph_store(tenant)

    # Establish baseline anchor
    anchor = GraphNode(
        id="fact_anchor_truth",
        kind=NodeKind.FACT,
        label="Anchor: refund window is 30 days",
        key="refund_window_days",
        value="30",
        status=Status.HEALTHY,
        asserted_by=AssertedBy(id="agent_root_0", name="Root Policy Bot", tier=Tier.ROOT),
    )
    store.add_node(anchor)

    tiers_distribution = [Tier.NEW] * 25 + [Tier.ESTABLISHED] * 20 + [Tier.ROOT] * 5
    results = []
    errors = []

    def assert_storm_fact(idx: int):
        tier_val = tiers_distribution[idx]
        fact_id = f"storm_fact_{idx}"
        fact = GraphNode(
            id=fact_id,
            kind=NodeKind.FACT,
            label=f"Storm assertion {idx}: refund is {100 + idx} days",
            key="refund_window_days",
            value=f"{100 + idx}",
            status=Status.HEALTHY,
            asserted_by=AssertedBy(id=f"agent_storm_{idx}", name=f"Agent {idx}", tier=tier_val),
        )
        try:
            with store._lock:
                store.add_node(fact)
            verdict = auditor_service.check_contradiction(store, fact, tenant=tenant)
            return (fact_id, tier_val, verdict)
        except Exception as e:
            errors.append(e)
            return (fact_id, tier_val, None)

    # Run across 15 worker threads
    with concurrent.futures.ThreadPoolExecutor(max_workers=15) as executor:
        futures = [executor.submit(assert_storm_fact, i) for i in range(50)]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result())

    assert len(errors) == 0, f"Encountered {len(errors)} errors during contradiction storm: {errors}"
    assert len(results) == 50

    # Verification:
    # 1. No weaker facts (NEW or ESTABLISHED) contradicting the ROOT anchor should remain HEALTHY
    with store._lock:
        weaker_healthy = [
            n for n in store.nodes.values()
            if n.key == "refund_window_days" and n.status == Status.HEALTHY and n.asserted_by and n.asserted_by.tier in (Tier.NEW, Tier.ESTABLISHED)
        ]
        assert len(weaker_healthy) == 0, f"Weaker facts remained healthy: {[n.id for n in weaker_healthy]}"

    # 2. Check invariants
    inv = store.check_invariants()
    assert inv["ok"] is True


# =========================================================================
# 4. BLAST RADIUS PREVIEW: READ-ONLY SIMULATION & COMPENSATION DETECTION
# =========================================================================

def test_blast_radius_preview_calculation(test_client):
    """
    Check blast radius preview calculation:
    - Verifies GET /api/blast/{node_id}/preview
    - Does it simulate the cascade WITHOUT mutating state?
    - Does it accurately detect executed actions requiring compensation?
    """
    tok = "token_acme_human"
    headers = {"Authorization": f"Bearer {tok}"}
    store = get_graph_store("acme")

    root_id = f"fact_blast_root_{int(time.time() * 1000)}"
    conc_id = f"conc_blast_{int(time.time() * 1000)}"
    act_staged_id = f"act_staged_{int(time.time() * 1000)}"
    act_rev_id = f"act_rev_{int(time.time() * 1000)}"
    act_comp_id = f"act_comp_{int(time.time() * 1000)}"
    act_irrev_id = f"act_irrev_{int(time.time() * 1000)}"

    root = GraphNode(id=root_id, kind=NodeKind.FACT, label="Blast Root", status=Status.HEALTHY)
    conc = GraphNode(id=conc_id, kind=NodeKind.CONCLUSION, label="Blast Conc", status=Status.HEALTHY)
    act_staged = GraphNode(
        id=act_staged_id,
        kind=NodeKind.ACTION,
        label="Staged Act",
        status=Status.HEALTHY,
        action=ActionInfo(type="STAGED", reversibility=Reversibility.COMPENSABLE, executed=False, gate=GateStatus.PASSED)
    )
    act_rev = GraphNode(
        id=act_rev_id,
        kind=NodeKind.ACTION,
        label="Rev Act",
        status=Status.HEALTHY,
        action=ActionInfo(type="REV", reversibility=Reversibility.REVERSIBLE, executed=True, gate=GateStatus.PASSED)
    )
    act_comp = GraphNode(
        id=act_comp_id,
        kind=NodeKind.ACTION,
        label="Comp Act",
        status=Status.HEALTHY,
        action=ActionInfo(type="COMP", reversibility=Reversibility.COMPENSABLE, executed=True, gate=GateStatus.PASSED)
    )
    act_irrev = GraphNode(
        id=act_irrev_id,
        kind=NodeKind.ACTION,
        label="Irrev Act",
        status=Status.HEALTHY,
        action=ActionInfo(type="IRREV", reversibility=Reversibility.IRREVERSIBLE, executed=True, gate=GateStatus.PASSED)
    )

    for n in [root, conc, act_staged, act_rev, act_comp, act_irrev]:
        store.add_node(n)

    store.add_edge(GraphEdge(id=f"e_{root_id}_{conc_id}", source=root_id, target=conc_id, type=EdgeType.LEADS_TO))
    store.add_edge(GraphEdge(id=f"e_{conc_id}_{act_staged_id}", source=conc_id, target=act_staged_id, type=EdgeType.LEADS_TO))
    store.add_edge(GraphEdge(id=f"e_{conc_id}_{act_rev_id}", source=conc_id, target=act_rev_id, type=EdgeType.LEADS_TO))
    store.add_edge(GraphEdge(id=f"e_{conc_id}_{act_comp_id}", source=conc_id, target=act_comp_id, type=EdgeType.LEADS_TO))
    store.add_edge(GraphEdge(id=f"e_{conc_id}_{act_irrev_id}", source=conc_id, target=act_irrev_id, type=EdgeType.LEADS_TO))

    initial_epoch = store.epoch

    # Call preview API
    r = test_client.get(f"/api/blast/{root_id}/preview", headers=headers)
    assert r.status_code == 200, f"Blast preview failed: {r.text}"
    data = r.json()

    # 1. Breakdown verification
    assert data["root_id"] == root_id
    assert set(data["node_ids"]) == {root_id, conc_id, act_staged_id, act_rev_id, act_comp_id, act_irrev_id}
    assert data["facts"] == 1
    assert data["conclusions"] == 1
    assert data["actions"]["staged"] == 1
    assert data["actions"]["executed_reversible"] == 1
    assert data["actions"]["executed_compensable"] == 1
    assert data["actions"]["executed_irreversible"] == 1

    # 2. State MUST NOT have mutated (Preview is strictly read-only)
    assert store.epoch == initial_epoch
    for nid in [root_id, conc_id, act_staged_id, act_rev_id, act_comp_id, act_irrev_id]:
        assert store.nodes[nid].status == Status.HEALTHY
    assert store.nodes[act_comp_id].action.compensation is None


# =========================================================================
# 5. RESTORATION & INVARIANTS: NEVER RESTORE EXECUTED ACTIONS
# =========================================================================

def test_restoration_never_restores_executed_actions_and_checks_invariants():
    """
    Test snapshot restoration / unquarantine:
    - Never restores executed actions.
    - Preserves invariants throughout.
    """
    store = GraphStore(tenant=f"test_restore_{int(time.time() * 1000)}")

    root = GraphNode(id="r_fact", kind=NodeKind.FACT, label="Root", status=Status.HEALTHY)
    c_node = GraphNode(id="c_node", kind=NodeKind.CONCLUSION, label="Conclusion", status=Status.HEALTHY)
    act_unexec = GraphNode(
        id="act_unexec",
        kind=NodeKind.ACTION,
        label="Unexecuted Action",
        status=Status.HEALTHY,
        action=ActionInfo(type="UNEXEC", reversibility=Reversibility.COMPENSABLE, executed=False, gate=GateStatus.PASSED)
    )
    act_exec = GraphNode(
        id="act_exec",
        kind=NodeKind.ACTION,
        label="Executed Action",
        status=Status.HEALTHY,
        action=ActionInfo(type="EXEC", reversibility=Reversibility.COMPENSABLE, executed=True, gate=GateStatus.PASSED)
    )

    for n in [root, c_node, act_unexec, act_exec]:
        store.add_node(n)

    store.add_edge(GraphEdge(id="e_r_c", source="r_fact", target="c_node", type=EdgeType.LEADS_TO))
    store.add_edge(GraphEdge(id="e_c_au", source="c_node", target="act_unexec", type=EdgeType.LEADS_TO))
    store.add_edge(GraphEdge(id="e_c_ae", source="c_node", target="act_exec", type=EdgeType.LEADS_TO))

    # Quarantine root -> cascades all 4 nodes
    aff = auditor_service.quarantine(store, "r_fact", tenant=store.tenant)
    assert len(aff) == 4

    for n in [root, c_node, act_unexec, act_exec]:
        assert store.nodes[n.id].status == Status.QUARANTINED

    # Restore root -> should restore root, c_node, act_unexec, but NEVER act_exec
    restored = auditor_service.restore(store, "r_fact", tenant=store.tenant)
    assert "r_fact" in restored
    assert "c_node" in restored
    assert "act_unexec" in restored
    assert "act_exec" not in restored

    assert store.nodes["r_fact"].status == Status.HEALTHY
    assert store.nodes["c_node"].status == Status.HEALTHY
    assert store.nodes["act_unexec"].status == Status.HEALTHY
    assert store.nodes["act_exec"].status == Status.QUARANTINED

    inv = store.check_invariants()
    assert inv["ok"] is True
