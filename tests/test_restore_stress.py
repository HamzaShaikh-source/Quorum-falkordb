"""
tests/test_restore_stress.py

Subagent 14: Comprehensive Stress-Testing of Causal Restoration (restore)
Covers:
1. Quarantine a tree of depth 4.
2. Call restore(root_id).
3. Verify root node and ALL valid descendants whose parents are healthy revert to HEALTHY status.
   (Including verifying executed actions remain QUARANTINED).
4. Test partial restoration: if one descendant has another parent that is still QUARANTINED,
   verify that descendant remains QUARANTINED until the second parent is restored.
5. Concurrently run restore and cascade on adjacent nodes to verify race resilience
   and causal invariant preservation (healthy_children_under_quarantined == 0).
"""

import time
import threading
import random
import pytest
import graph.queries as queries

TENANT = "test_restore_stress"


def reset_graph(tenant_id: str):
    g = queries.get_graph(tenant_id, "commons")
    try:
        g.query("MATCH (n) DETACH DELETE n")
    except Exception:
        pass
    queries.ensure_indexes(tenant_id)


# =========================================================================
# TEST 1, 2, 3: Depth 4 Tree Quarantine & Full Causal Restoration
# =========================================================================

def test_depth_4_quarantine_and_restore():
    """
    1. Quarantine a tree of depth 4.
    2. Call restore(root_id).
    3. Verify root node and ALL valid descendants whose parents are healthy revert to HEALTHY status.
    """
    reset_graph(TENANT)
    g = queries.get_graph(TENANT, "commons")

    # Build depth 4 tree:
    # Level 0 (depth 0): 1 root fact (f_root)
    # Level 1 (depth 1): 2 conclusions (c_1_0, c_1_1)
    # Level 2 (depth 2): 4 conclusions (c_2_0..c_2_3)
    # Level 3 (depth 3): 4 decisions (d_3_0..d_3_3)
    # Level 4 (depth 4): 4 actions (act_4_0..act_4_3)

    g.query("CREATE (:Fact:MemoryNode {id: 'f_root', status: 'HEALTHY', text: 'Root Fact'})")

    # Level 1
    for i in range(2):
        g.query(f"""
        MATCH (p:MemoryNode {{id: 'f_root'}})
        CREATE (c:Conclusion:MemoryNode {{id: 'c_1_{i}', status: 'HEALTHY', text: 'Conclusion L1_{i}'}})
        CREATE (p)-[:LEADS_TO]->(c)
        """)

    # Level 2
    for i in range(2):
        for j in range(2):
            idx = i * 2 + j
            edge = "LEADS_TO" if idx % 2 == 0 else "SUPPORTS"
            g.query(f"""
            MATCH (p:MemoryNode {{id: 'c_1_{i}'}})
            CREATE (c:Conclusion:MemoryNode {{id: 'c_2_{idx}', status: 'HEALTHY', text: 'Conclusion L2_{idx}'}})
            CREATE (p)-[:{edge}]->(c)
            """)

    # Level 3
    for idx in range(4):
        edge = "SUPPORTS" if idx % 2 == 0 else "LEADS_TO"
        g.query(f"""
        MATCH (p:MemoryNode {{id: 'c_2_{idx}'}})
        CREATE (d:Decision:Conclusion:MemoryNode {{id: 'd_3_{idx}', status: 'HEALTHY', text: 'Decision L3_{idx}', stale: false}})
        CREATE (p)-[:{edge}]->(d)
        """)

    # Level 4
    for idx in range(4):
        g.query(f"""
        MATCH (p:MemoryNode {{id: 'd_3_{idx}'}})
        CREATE (a:Action:MemoryNode {{id: 'act_4_{idx}', status: 'HEALTHY', action_type: 'EMAIL', executed: false, reversibility: 'COMPENSABLE'}})
        CREATE (p)-[:LEADS_TO]->(a)
        """)

    # Verify initial tree size: 1 + 2 + 4 + 4 + 4 = 15 nodes
    count_res = g.query("MATCH (m:MemoryNode) RETURN count(m)").result_set[0][0]
    assert count_res == 15, f"Expected 15 nodes, got {count_res}"

    # Step 1: Quarantine the tree of depth 4
    t0 = time.time()
    casc_res = queries.cascade(TENANT, "f_root")
    dur_casc = (time.time() - t0) * 1000

    assert casc_res["success"] is True
    assert casc_res["affected_count"] == 15
    assert len(casc_res["node_ids_by_depth"]) == 5  # depths 0, 1, 2, 3, 4
    assert "f_root" in casc_res["node_ids_by_depth"][0]
    assert len(casc_res["node_ids_by_depth"][4]) == 4  # 4 actions at depth 4

    # Verify all 15 nodes are in QUARANTINED status in FalkorDB
    q_quarantined = g.query("MATCH (m:MemoryNode) WHERE m.status = 'QUARANTINED' RETURN count(m)").result_set[0][0]
    assert q_quarantined == 15, f"Expected 15 QUARANTINED nodes, got {q_quarantined}"

    inv = queries.invariant_check(TENANT)
    assert inv["healthy_children_under_quarantined"] == 0
    assert inv["all_passed"] is True

    # Step 2: Call restore(root_id)
    t1 = time.time()
    rest_res = queries.restore(TENANT, "f_root")
    dur_rest = (time.time() - t1) * 1000

    # Step 3: Verify root node and ALL valid descendants revert to HEALTHY
    assert rest_res["success"] is True
    assert rest_res["restored_count"] == 15
    assert "f_root" in rest_res["restored_nodes"]

    # Check all 15 nodes in DB
    db_healthy = g.query("MATCH (m:MemoryNode) WHERE m.status = 'HEALTHY' RETURN count(m)").result_set[0][0]
    assert db_healthy == 15, f"Expected all 15 nodes HEALTHY, got {db_healthy}"

    # Verify released_at is set on all nodes
    db_released = g.query("MATCH (m:MemoryNode) WHERE m.released_at IS NOT NULL RETURN count(m)").result_set[0][0]
    assert db_released == 15

    # Invariant verification
    inv_after = queries.invariant_check(TENANT)
    assert inv_after["healthy_children_under_quarantined"] == 0
    assert inv_after["all_passed"] is True

    print(f"\n[Test 1, 2, 3 Passed] Depth 4 tree (15 nodes) cascaded in {dur_casc:.2f}ms, restored in {dur_rest:.2f}ms")


def test_depth_4_with_executed_action_safety():
    """
    Sub-test for Test 3:
    Verify that an EXECUTED action (executed = true) NEVER reverts to HEALTHY status,
    and any child below it also remains QUARANTINED (causal integrity).
    """
    reset_graph(TENANT)
    g = queries.get_graph(TENANT, "commons")

    # Tree depth 4 where one level 3 action is executed=true, and has a level 4 child
    g.query("CREATE (:Fact:MemoryNode {id: 'f_root', status: 'HEALTHY'})")
    g.query("MATCH (f:Fact {id: 'f_root'}) CREATE (c1:Conclusion:MemoryNode {id: 'c1', status: 'HEALTHY'}) CREATE (f)-[:LEADS_TO]->(c1)")
    g.query("MATCH (c1:Conclusion {id: 'c1'}) CREATE (c2:Conclusion:MemoryNode {id: 'c2', status: 'HEALTHY'}) CREATE (c1)-[:LEADS_TO]->(c2)")
    # Level 3: one executed action, one unexecuted action
    g.query("MATCH (c2:Conclusion {id: 'c2'}) CREATE (a_exec:Action:MemoryNode {id: 'act_exec', status: 'HEALTHY', executed: true, executed_at: timestamp()}) CREATE (c2)-[:LEADS_TO]->(a_exec)")
    g.query("MATCH (c2:Conclusion {id: 'c2'}) CREATE (a_unexec:Action:MemoryNode {id: 'act_unexec', status: 'HEALTHY', executed: false}) CREATE (c2)-[:LEADS_TO]->(a_unexec)")
    # Level 4: child of executed action, and child of unexecuted action
    g.query("MATCH (a:Action {id: 'act_exec'}) CREATE (c_under_exec:Conclusion:MemoryNode {id: 'c_under_exec', status: 'HEALTHY'}) CREATE (a)-[:LEADS_TO]->(c_under_exec)")
    g.query("MATCH (a:Action {id: 'act_unexec'}) CREATE (c_under_unexec:Conclusion:MemoryNode {id: 'c_under_unexec', status: 'HEALTHY'}) CREATE (a)-[:LEADS_TO]->(c_under_unexec)")

    # Quarantine
    casc = queries.cascade(TENANT, "f_root")
    assert casc["success"] is True
    assert casc["affected_count"] == 7

    # Restore
    rest = queries.restore(TENANT, "f_root")
    assert rest["success"] is True

    # Nodes that should be restored: f_root, c1, c2, act_unexec, c_under_unexec (5 nodes)
    # Nodes that MUST REMAIN QUARANTINED: act_exec (executed=true), c_under_exec (parent act_exec is quarantined) (2 nodes)
    assert "act_exec" not in rest["restored_nodes"]
    assert "c_under_exec" not in rest["restored_nodes"]
    assert rest["restored_count"] == 5

    res_statuses = dict(g.query("MATCH (m:MemoryNode) RETURN m.id, m.status").result_set)
    assert res_statuses["f_root"] == "HEALTHY"
    assert res_statuses["c1"] == "HEALTHY"
    assert res_statuses["c2"] == "HEALTHY"
    assert res_statuses["act_unexec"] == "HEALTHY"
    assert res_statuses["c_under_unexec"] == "HEALTHY"
    assert res_statuses["act_exec"] == "QUARANTINED"
    assert res_statuses["c_under_exec"] == "QUARANTINED"

    # Crucial invariant check: no healthy child under quarantined parent!
    inv = queries.invariant_check(TENANT)
    assert inv["healthy_children_under_quarantined"] == 0
    assert inv["all_passed"] is True
    print("\n[Executed Action Safety Test Passed] Executed action and its descendants remained QUARANTINED.")


# =========================================================================
# TEST 4: Partial Restoration with Multi-Parent Quarantined Confluence
# =========================================================================

def test_partial_restoration_multi_parent():
    """
    4. Test partial restoration:
       If one descendant has another parent that is still QUARANTINED,
       verify that descendant remains QUARANTINED.
       Then restore the second parent and verify full restoration.
    """
    reset_graph(TENANT)
    g = queries.get_graph(TENANT, "commons")

    # Topology:
    # Root A (f_root_a) ----(LEADS_TO)----> c_shared ----(LEADS_TO)----> c_child ----(LEADS_TO)----> act_leaf
    # Root B (f_root_b) --(SUPPORTS)------> c_shared
    # Root A ------------(LEADS_TO)--------> c_a_only

    g.query("CREATE (:Fact:MemoryNode {id: 'f_root_a', status: 'HEALTHY', text: 'Root A'})")
    g.query("CREATE (:Fact:MemoryNode {id: 'f_root_b', status: 'HEALTHY', text: 'Root B'})")
    g.query("CREATE (:Conclusion:MemoryNode {id: 'c_a_only', status: 'HEALTHY', text: 'Conclusion A only'})")
    g.query("CREATE (:Conclusion:MemoryNode {id: 'c_shared', status: 'HEALTHY', text: 'Shared Conclusion'})")
    g.query("CREATE (:Conclusion:MemoryNode {id: 'c_child', status: 'HEALTHY', text: 'Child of Shared'})")
    g.query("CREATE (:Action:MemoryNode {id: 'act_leaf', status: 'HEALTHY', action_type: 'LOG', executed: false})")

    # Connect
    g.query("MATCH (a:Fact {id: 'f_root_a'}), (c:Conclusion {id: 'c_a_only'}) CREATE (a)-[:LEADS_TO]->(c)")
    g.query("MATCH (a:Fact {id: 'f_root_a'}), (c:Conclusion {id: 'c_shared'}) CREATE (a)-[:LEADS_TO]->(c)")
    g.query("MATCH (b:Fact {id: 'f_root_b'}), (c:Conclusion {id: 'c_shared'}) CREATE (b)-[:SUPPORTS]->(c)")
    g.query("MATCH (s:Conclusion {id: 'c_shared'}), (c:Conclusion {id: 'c_child'}) CREATE (s)-[:LEADS_TO]->(c)")
    g.query("MATCH (c:Conclusion {id: 'c_child'}), (act:Action {id: 'act_leaf'}) CREATE (c)-[:LEADS_TO]->(act)")

    # Quarantine both roots
    queries.cascade(TENANT, "f_root_a")
    queries.cascade(TENANT, "f_root_b")

    # Verify all 6 nodes are QUARANTINED
    q_count = g.query("MATCH (m:MemoryNode) WHERE m.status = 'QUARANTINED' RETURN count(m)").result_set[0][0]
    assert q_count == 6

    # Call restore on Root A ONLY
    res_a = queries.restore(TENANT, "f_root_a")
    assert res_a["success"] is True

    # Root A and c_a_only must be restored
    assert "f_root_a" in res_a["restored_nodes"]
    assert "c_a_only" in res_a["restored_nodes"]

    # c_shared, c_child, act_leaf MUST NOT be restored because parent f_root_b is still QUARANTINED!
    assert "c_shared" not in res_a["restored_nodes"]
    assert "c_child" not in res_a["restored_nodes"]
    assert "act_leaf" not in res_a["restored_nodes"]
    assert res_a["restored_count"] == 2

    # Check status directly from database
    statuses = dict(g.query("MATCH (m:MemoryNode) RETURN m.id, m.status").result_set)
    assert statuses["f_root_a"] == "HEALTHY"
    assert statuses["c_a_only"] == "HEALTHY"
    assert statuses["f_root_b"] == "QUARANTINED"
    assert statuses["c_shared"] == "QUARANTINED", "c_shared MUST remain QUARANTINED while parent f_root_b is QUARANTINED"
    assert statuses["c_child"] == "QUARANTINED", "c_child MUST remain QUARANTINED"
    assert statuses["act_leaf"] == "QUARANTINED", "act_leaf MUST remain QUARANTINED"

    # Verify invariant
    inv1 = queries.invariant_check(TENANT)
    assert inv1["healthy_children_under_quarantined"] == 0
    assert inv1["all_passed"] is True

    # Now restore Root B
    res_b = queries.restore(TENANT, "f_root_b")
    assert res_b["success"] is True

    # Root B should be restored, and now c_shared, c_child, and act_leaf should all be restored!
    assert "f_root_b" in res_b["restored_nodes"]
    assert "c_shared" in res_b["restored_nodes"]
    assert "c_child" in res_b["restored_nodes"]
    assert "act_leaf" in res_b["restored_nodes"]
    assert res_b["restored_count"] == 4

    # Final DB check: ALL 6 nodes must now be HEALTHY
    statuses_final = dict(g.query("MATCH (m:MemoryNode) RETURN m.id, m.status").result_set)
    for nid, stat in statuses_final.items():
        assert stat == "HEALTHY", f"Node {nid} expected HEALTHY but found {stat}"

    inv2 = queries.invariant_check(TENANT)
    assert inv2["healthy_children_under_quarantined"] == 0
    assert inv2["all_passed"] is True

    print("\n[Test 4 Passed] Partial restoration blocked by quarantined co-parent; unblocked upon second parent restore.")


def test_restore_fact_with_quarantined_parent_edge_case():
    """
    Edge case analysis: What happens if a Fact is not a true root (it has a parent Fact),
    both are QUARANTINED, and restore(child_fact) is called?
    """
    tenant_edge = f"{TENANT}_fact_parent"
    reset_graph(tenant_edge)
    g = queries.get_graph(tenant_edge, "commons")

    g.query("""
    CREATE (f1:Fact:MemoryNode {id: 'f_parent', status: 'QUARANTINED', quarantined_at: timestamp()})
    CREATE (f2:Fact:MemoryNode {id: 'f_child', status: 'QUARANTINED', quarantined_at: timestamp()})
    CREATE (f1)-[:LEADS_TO]->(f2)
    """)

    # Attempt to restore f_child directly
    res = queries.restore(tenant_edge, "f_child")
    print(f"\n[Edge Case: Restore child fact directly] Result: {res}")

    inv = queries.invariant_check(tenant_edge)
    print(f"[Edge Case Invariant Check]: {inv}")
def test_depth_4_scale_121_nodes():
    """
    Scale test: Depth 4 tree with branching factor 3:
    1 root + 3 L1 + 9 L2 + 27 L3 + 81 L4 = 121 nodes.
    Quarantines all 121 nodes, then restores all 121 nodes.
    Measures latency and verifies zero orphan nodes.
    """
    tenant_scale = f"{TENANT}_scale_121"
    reset_graph(tenant_scale)
    g = queries.get_graph(tenant_scale, "commons")

    # Root
    g.query("CREATE (:Fact:MemoryNode {id: 'f_root', status: 'HEALTHY', text: 'Root Fact'})")

    # Level 1 (3 nodes)
    for i in range(3):
        g.query(f"""
        MATCH (p:Fact {{id: 'f_root'}})
        CREATE (c:Conclusion:MemoryNode {{id: 'c_1_{i}', status: 'HEALTHY'}})
        CREATE (p)-[:LEADS_TO]->(c)
        """)

    # Level 2 (9 nodes)
    for i in range(3):
        for j in range(3):
            idx2 = i * 3 + j
            g.query(f"""
            MATCH (p:Conclusion {{id: 'c_1_{i}'}})
            CREATE (c:Conclusion:MemoryNode {{id: 'c_2_{idx2}', status: 'HEALTHY'}})
            CREATE (p)-[:SUPPORTS]->(c)
            """)

    # Level 3 (27 nodes)
    for idx2 in range(9):
        for k in range(3):
            idx3 = idx2 * 3 + k
            g.query(f"""
            MATCH (p:Conclusion {{id: 'c_2_{idx2}'}})
            CREATE (d:Decision:Conclusion:MemoryNode {{id: 'd_3_{idx3}', status: 'HEALTHY', stale: false}})
            CREATE (p)-[:LEADS_TO]->(d)
            """)

    # Level 4 (81 nodes)
    for idx3 in range(27):
        for m in range(3):
            idx4 = idx3 * 3 + m
            g.query(f"""
            MATCH (p:Decision {{id: 'd_3_{idx3}'}})
            CREATE (a:Action:MemoryNode {{id: 'act_4_{idx4}', status: 'HEALTHY', executed: false, reversibility: 'COMPENSABLE'}})
            CREATE (p)-[:LEADS_TO]->(a)
            """)

    total_count = g.query("MATCH (m:MemoryNode) RETURN count(m)").result_set[0][0]
    assert total_count == 121, f"Expected 121 nodes, found {total_count}"

    # Cascade
    t0 = time.time()
    casc_res = queries.cascade(tenant_scale, "f_root")
    dur_casc = (time.time() - t0) * 1000
    assert casc_res["affected_count"] == 121

    # Restore
    t1 = time.time()
    rest_res = queries.restore(tenant_scale, "f_root")
    dur_rest = (time.time() - t1) * 1000

    assert rest_res["success"] is True
    assert rest_res["restored_count"] == 121

    healthy_count = g.query("MATCH (m:MemoryNode) WHERE m.status = 'HEALTHY' RETURN count(m)").result_set[0][0]
    assert healthy_count == 121

    inv = queries.invariant_check(tenant_scale)
    assert inv["healthy_children_under_quarantined"] == 0
    assert inv["all_passed"] is True
    print(f"\n[Scale Test 121 Nodes] Cascade: {dur_casc:.2f}ms | Restore: {dur_rest:.2f}ms | Invariants: 100% passed")


def test_skip_level_edges_dag():
    """
    Test DAG with skip-level edges (e.g., Depth 0 -> Depth 2, Depth 1 -> Depth 3).
    Ensures iterative restore resolves multi-depth dependencies in correct sequence.
    """
    tenant_skip = f"{TENANT}_skip_level"
    reset_graph(tenant_skip)
    g = queries.get_graph(tenant_skip, "commons")

    g.query("""
    CREATE (f:Fact:MemoryNode {id: 'f_root', status: 'HEALTHY'})
    CREATE (c1:Conclusion:MemoryNode {id: 'c1', status: 'HEALTHY'})
    CREATE (c2:Conclusion:MemoryNode {id: 'c2', status: 'HEALTHY'})
    CREATE (d3:Decision:Conclusion:MemoryNode {id: 'd3', status: 'HEALTHY'})
    CREATE (a4:Action:MemoryNode {id: 'a4', status: 'HEALTHY', executed: false})

    CREATE (f)-[:LEADS_TO]->(c1)
    CREATE (c1)-[:LEADS_TO]->(c2)
    CREATE (f)-[:SUPPORTS]->(c2)          // Skip-level edge: L0 -> L2
    CREATE (c1)-[:SUPPORTS]->(d3)         // Skip-level edge: L1 -> L3
    CREATE (c2)-[:LEADS_TO]->(d3)
    CREATE (d3)-[:LEADS_TO]->(a4)
    """)

    queries.cascade(tenant_skip, "f_root")
    q_count = g.query("MATCH (m:MemoryNode) WHERE m.status = 'QUARANTINED' RETURN count(m)").result_set[0][0]
    assert q_count == 5

    rest_res = queries.restore(tenant_skip, "f_root")
    assert rest_res["success"] is True
    assert rest_res["restored_count"] == 5

    h_count = g.query("MATCH (m:MemoryNode) WHERE m.status = 'HEALTHY' RETURN count(m)").result_set[0][0]
    assert h_count == 5

    inv = queries.invariant_check(tenant_skip)
    assert inv["all_passed"] is True
    print("\n[Skip-Level DAG Test] Verified correct multi-hop resolution across skip edges.")


def test_multi_parent_trio_convergence():
    """
    Test 3 parents converging on a single decision node:
    - Root 1, Root 2, Root 3
    - Restoring 1/3 keeps descendant quarantined
    - Restoring 2/3 keeps descendant quarantined
    - Restoring 3/3 restores descendant to healthy
    """
    tenant_trio = f"{TENANT}_trio"
    reset_graph(tenant_trio)
    g = queries.get_graph(tenant_trio, "commons")

    g.query("""
    CREATE (f1:Fact:MemoryNode {id: 'f1', status: 'HEALTHY'})
    CREATE (f2:Fact:MemoryNode {id: 'f2', status: 'HEALTHY'})
    CREATE (f3:Fact:MemoryNode {id: 'f3', status: 'HEALTHY'})
    CREATE (d:Decision:Conclusion:MemoryNode {id: 'd_converge', status: 'HEALTHY'})

    CREATE (f1)-[:LEADS_TO]->(d)
    CREATE (f2)-[:SUPPORTS]->(d)
    CREATE (f3)-[:LEADS_TO]->(d)
    """)

    queries.cascade(tenant_trio, "f1")
    queries.cascade(tenant_trio, "f2")
    queries.cascade(tenant_trio, "f3")

    # Step A: Restore 1 of 3
    r1 = queries.restore(tenant_trio, "f1")
    assert "d_converge" not in r1["restored_nodes"]
    stat = g.query("MATCH (d:Decision {id: 'd_converge'}) RETURN d.status").result_set[0][0]
    assert stat == "QUARANTINED"
    assert queries.invariant_check(tenant_trio)["all_passed"] is True

    # Step B: Restore 2 of 3
    r2 = queries.restore(tenant_trio, "f2")
    assert "d_converge" not in r2["restored_nodes"]
    stat = g.query("MATCH (d:Decision {id: 'd_converge'}) RETURN d.status").result_set[0][0]
    assert stat == "QUARANTINED"
    assert queries.invariant_check(tenant_trio)["all_passed"] is True

    # Step C: Restore 3 of 3
    r3 = queries.restore(tenant_trio, "f3")
    assert "d_converge" in r3["restored_nodes"]
    stat = g.query("MATCH (d:Decision {id: 'd_converge'}) RETURN d.status").result_set[0][0]
    assert stat == "HEALTHY"
    assert queries.invariant_check(tenant_trio)["all_passed"] is True
    print("\n[Trio Convergence Test] Descendant remained quarantined until 3/3 parents became healthy.")


def test_quarantined_cycle_termination():
    """
    Verify that if an intentional cycle exists between quarantined nodes (e.g. c1 -> c2 -> c1),
    restore does not loop infinitely and safely terminates.
    """
    tenant_cycle = f"{TENANT}_cycle"
    reset_graph(tenant_cycle)
    g = queries.get_graph(tenant_cycle, "commons")

    g.query("""
    CREATE (f:Fact:MemoryNode {id: 'f_root', status: 'QUARANTINED', quarantined_at: timestamp()})
    CREATE (c1:Conclusion:MemoryNode {id: 'c1', status: 'QUARANTINED', quarantined_at: timestamp()})
    CREATE (c2:Conclusion:MemoryNode {id: 'c2', status: 'QUARANTINED', quarantined_at: timestamp()})

    CREATE (f)-[:LEADS_TO]->(c1)
    CREATE (c1)-[:LEADS_TO]->(c2)
    CREATE (c2)-[:LEADS_TO]->(c1)
    """)

    # Calling restore on f_root:
    # f_root will be restored to HEALTHY.
    # Pass 1: c1 has parents f_root (HEALTHY) and c2 (QUARANTINED).
    # Since c2 is QUARANTINED, c1 cannot be restored!
    # c2 has parent c1 (QUARANTINED), so c2 cannot be restored!
    # Therefore, while loop must terminate after 1 iteration!
    res = queries.restore(tenant_cycle, "f_root")
    assert res["success"] is True
    assert res["restored_nodes"] == ["f_root"]
    assert res["restored_count"] == 1

    c1_stat = g.query("MATCH (c:Conclusion {id: 'c1'}) RETURN c.status").result_set[0][0]
    c2_stat = g.query("MATCH (c:Conclusion {id: 'c2'}) RETURN c.status").result_set[0][0]
    assert c1_stat == "QUARANTINED"
    assert c2_stat == "QUARANTINED"
    print("\n[Cycle Termination Test] Restore terminated safely with cycle nodes remaining QUARANTINED.")


# =========================================================================
# TEST 5: Concurrent Restore and Cascade on Adjacent Nodes (Race Resilience)
# =========================================================================

def test_concurrent_restore_and_cascade_adjacent():
    """
    5. Concurrently run restore and cascade on adjacent nodes to verify race resilience.
    We test 30 race cycles with varying barriers and topologies.
    At EVERY step, the causal invariant MUST HOLD:
    healthy_children_under_quarantined == 0.
    """
    print("\n--- [TEST 5] Concurrency Stress: Restore vs Cascade on Adjacent Nodes ---")

    # Test across 30 iterations of concurrent races
    num_rounds = 30
    invariant_violations = 0
    errors_encountered = []

    for round_idx in range(num_rounds):
        tenant_round = f"{TENANT}_race_{round_idx}"
        reset_graph(tenant_round)
        g = queries.get_graph(tenant_round, "commons")

        # Create topology:
        # Fact f_main (quarantined) -> Fact f_adj (healthy or quarantined) -> Conclusion c_mid -> Action act_end
        # Another Fact f_cross -> Conclusion c_mid
        g.query("""
        CREATE (f1:Fact:MemoryNode {id: 'f_main', status: 'QUARANTINED', quarantined_at: timestamp()})
        CREATE (f2:Fact:MemoryNode {id: 'f_adj', status: 'HEALTHY'})
        CREATE (c:Conclusion:MemoryNode {id: 'c_mid', status: 'QUARANTINED', quarantined_at: timestamp()})
        CREATE (a:Action:MemoryNode {id: 'act_end', status: 'QUARANTINED', quarantined_at: timestamp(), executed: false})
        CREATE (f1)-[:LEADS_TO]->(c)
        CREATE (f2)-[:SUPPORTS]->(c)
        CREATE (c)-[:LEADS_TO]->(a)
        """)

        barrier = threading.Barrier(2)
        results = {}

        def runner_restore():
            barrier.wait()
            try:
                results["restore"] = queries.restore(tenant_round, "f_main")
            except Exception as e:
                results["restore_err"] = str(e)

        def runner_cascade():
            barrier.wait()
            try:
                results["cascade"] = queries.cascade(tenant_round, "f_adj")
            except Exception as e:
                results["cascade_err"] = str(e)

        t1 = threading.Thread(target=runner_restore)
        t2 = threading.Thread(target=runner_cascade)

        t1.start()
        t2.start()
        t1.join()
        t2.join()

        if "restore_err" in results:
            errors_encountered.append(f"Round {round_idx} restore error: {results['restore_err']}")
        if "cascade_err" in results:
            errors_encountered.append(f"Round {round_idx} cascade error: {results['cascade_err']}")

        # Invariant check
        inv = queries.invariant_check(tenant_round)
        if inv["healthy_children_under_quarantined"] > 0:
            invariant_violations += 1
            print(f"VIOLATION in round {round_idx}: {inv}")

        # Cleanup graph for round
        try:
            g.query("MATCH (n) DETACH DELETE n")
        except Exception:
            pass

    print(f"Rounds executed: {num_rounds}")
    print(f"Invariant violations (healthy child under quarantined parent): {invariant_violations}")
    print(f"Errors encountered: {len(errors_encountered)}")

    assert invariant_violations == 0, f"Invariant violated in {invariant_violations} rounds!"
    assert len(errors_encountered) == 0, f"Errors encountered: {errors_encountered}"


def test_concurrent_multi_thread_adjacent_bombardment():
    """
    Stress-test with 10 threads concurrently executing restore and cascade
    on interconnected adjacent facts in the same graph over 20 iterations.
    """
    print("\n--- [TEST 5.2] Multi-Thread Bombardment (10 threads racing restore & cascade) ---")
    tenant_stress = f"{TENANT}_bombard"
    reset_graph(tenant_stress)
    g = queries.get_graph(tenant_stress, "commons")

    # Create 5 root facts connected to 10 conclusions and 5 actions
    # f0, f1, f2, f3, f4
    for i in range(5):
        g.query(f"CREATE (:Fact:MemoryNode {{id: 'f_{i}', status: 'QUARANTINED', quarantined_at: timestamp()}})")

    for c in range(10):
        p1 = f"f_{c % 5}"
        p2 = f"f_{(c + 1) % 5}"
        g.query(f"""
        MATCH (a:Fact {{id: '{p1}'}}), (b:Fact {{id: '{p2}'}})
        CREATE (con:Conclusion:MemoryNode {{id: 'con_{c}', status: 'QUARANTINED', quarantined_at: timestamp()}})
        CREATE (a)-[:LEADS_TO]->(con)
        CREATE (b)-[:SUPPORTS]->(con)
        """)

    for a in range(5):
        g.query(f"""
        MATCH (c:Conclusion {{id: 'con_{a}'}})
        CREATE (act:Action:MemoryNode {{id: 'act_{a}', status: 'QUARANTINED', quarantined_at: timestamp(), executed: false}})
        CREATE (c)-[:LEADS_TO]->(act)
        """)

    num_threads = 10
    num_cycles = 20
    barrier = threading.Barrier(num_threads)
    violations = 0
    exceptions = []

    def worker(worker_id: int):
        fact_id = f"f_{worker_id % 5}"
        for cycle in range(num_cycles):
            # Alternate between restore and cascade
            try:
                if worker_id % 2 == 0:
                    queries.restore(tenant_stress, fact_id)
                else:
                    queries.cascade(tenant_stress, fact_id)
            except Exception as e:
                exceptions.append(str(e))
            time.sleep(0.005 * random.random())

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Final post-race audit
    inv = queries.invariant_check(tenant_stress)
    print(f"Post-bombardment Invariant Check: {inv}")
    print(f"Exceptions caught: {len(exceptions)}")

    assert inv["healthy_children_under_quarantined"] == 0, "Invariant failed after bombardment!"
    assert inv["all_passed"] is True
    print("[Multi-Thread Bombardment Passed] Zero invariant violations under intense restore/cascade crossfire.")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
