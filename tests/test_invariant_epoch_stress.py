"""
Comprehensive Stress & Invariant Test Suite:
Subagent 17: Stress-testing Invariant Check & Epoch Bumps (invariant_check, bump_epoch)

Tests:
1. Invariant check on healthy graph:
   - Complex DAG with Facts, Conclusions, staged Actions, executed Actions, diamond topologies.
   - Verify passed=True, violations=0, violating_node_ids=[], all_passed=True.
2. Artificially create illegal states:
   - Healthy child under quarantined parent (direct and multi-hop).
   - Executed action under quarantined parent/ancestor (executed_at > quarantined_at).
   - Executed action under quarantined parent with missing timestamps.
3. Exact violating node ID detection:
   - Verify invariant_check detects and reports exact violating node IDs for each illegal scenario.
   - Mixed topology with multiple violations across different branches.
   - Healing verification: resolving violations returns graph to clean state (passed=True, violations=0).
4. High-concurrency stress test on bump_epoch:
   - 1000 atomic epoch increments under 50 concurrent threads.
   - Strictly monotonically increasing sequence without duplicates or dropped updates.
   - Multi-tenant epoch isolation under concurrent load.
"""

import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List
from graph import queries

TENANT_HEALTHY = "test_inv_healthy"
TENANT_ILLEGAL = "test_inv_illegal"
TENANT_EPOCH = "test_epoch_stress"
TENANT_EPOCH_A = "test_epoch_tenant_a"
TENANT_EPOCH_B = "test_epoch_tenant_b"


def clean_graph(tenant_id: str):
    g = queries.get_graph(tenant_id, "commons")
    try:
        g.query("MATCH (n) DETACH DELETE n")
    except Exception:
        pass
    queries.ensure_indexes(tenant_id)


# =========================================================================
# 1. HEALTHY GRAPH INVARIANT AUDIT
# =========================================================================

def test_invariant_check_on_healthy_graph():
    """
    Test 1: Run invariant_check on a healthy graph.
    Creates a multi-level diamond DAG with facts, conclusions, staged actions,
    and valid executed actions (executed before any quarantine, with all healthy parents).
    Verifies: passed=True, violations=0, and empty violating node sets.
    """
    clean_graph(TENANT_HEALTHY)
    g = queries.get_graph(TENANT_HEALTHY, "commons")

    # 1. Construct diamond DAG
    # Root Fact 1 & Root Fact 2
    g.query("""
    CREATE (f1:Fact:MemoryNode {id: 'f_pol_1', status: 'HEALTHY', text: 'Policy 1', valid_from: 100})
    CREATE (f2:Fact:MemoryNode {id: 'f_pol_2', status: 'HEALTHY', text: 'Policy 2', valid_from: 100})
    CREATE (c1:Conclusion:MemoryNode {id: 'c_elig_1', status: 'HEALTHY', text: 'Eligible 1'})
    CREATE (c2:Conclusion:MemoryNode {id: 'c_elig_2', status: 'HEALTHY', text: 'Eligible 2'})
    CREATE (f1)-[:LEADS_TO]->(c1)
    CREATE (f2)-[:LEADS_TO]->(c2)
    CREATE (c_merge:Conclusion:MemoryNode {id: 'c_decision', status: 'HEALTHY', text: 'Final Decision'})
    CREATE (c1)-[:LEADS_TO]->(c_merge)
    CREATE (c2)-[:LEADS_TO]->(c_merge)
    """)

    # Stage and execute action 1 legally
    queries.stage_action(TENANT_HEALTHY, "act_healthy_1", ["c_decision"], "NOTIFY", {"msg": "hello"}, "COMPENSABLE", gate="PASSED")
    exec_res1 = queries.execute_guard(TENANT_HEALTHY, "act_healthy_1")
    assert exec_res1["success"] is True

    # Stage action 2 (unexecuted)
    queries.stage_action(TENANT_HEALTHY, "act_healthy_2", ["c_decision"], "AUDIT_LOG", {"type": "log"}, "NON_COMPENSABLE", gate="PASSED")

    # Audit invariants
    inv = queries.invariant_check(TENANT_HEALTHY)

    print(f"\n[Test 1] Healthy graph invariant check results: {inv}")
    assert inv["passed"] is True
    assert inv["all_passed"] is True
    assert inv["violations"] == 0
    assert inv["healthy_children_under_quarantined"] == 0
    assert inv["actions_executed_after_ancestor_quarantined"] == 0
    assert inv["violating_child_ids"] == []
    assert inv["violating_action_ids"] == []
    assert inv["violating_node_ids"] == []


# =========================================================================
# 2. ARTIFICIALLY CREATE ILLEGAL STATES
# =========================================================================

def test_illegal_state_healthy_child_under_quarantined_parent():
    """
    Test 2A: Artificially inject healthy child nodes under a quarantined parent.
    Verifies that invariant_check flags the violation and reports the exact child IDs.
    """
    clean_graph(TENANT_ILLEGAL)
    g = queries.get_graph(TENANT_ILLEGAL, "commons")

    # Parent is QUARANTINED, children are HEALTHY
    g.query("""
    CREATE (parent:Fact:MemoryNode {id: 'f_poison_parent', status: 'QUARANTINED', quarantined_at: 1000})
    CREATE (child1:Conclusion:MemoryNode {id: 'c_illegal_orphan_1', status: 'HEALTHY', text: 'Illegal Child 1'})
    CREATE (child2:Conclusion:MemoryNode {id: 'c_illegal_orphan_2', status: 'HEALTHY', text: 'Illegal Child 2'})
    CREATE (parent)-[:LEADS_TO]->(child1)
    CREATE (parent)-[:SUPPORTS]->(child2)
    """)

    inv = queries.invariant_check(TENANT_ILLEGAL)
    print(f"\n[Test 2A] Illegal healthy children results: {inv}")

    assert inv["passed"] is False
    assert inv["all_passed"] is False
    assert inv["healthy_children_under_quarantined"] == 2
    assert set(inv["violating_child_ids"]) == {"c_illegal_orphan_1", "c_illegal_orphan_2"}
    assert "c_illegal_orphan_1" in inv["violating_node_ids"]
    assert "c_illegal_orphan_2" in inv["violating_node_ids"]


def test_illegal_state_executed_action_after_ancestor_quarantined():
    """
    Test 2B: Artificially create an action executed after an ancestor was quarantined.
    Quarantined at T=1000, Action executed at T=2000.
    Verifies detection and exact reporting of the action ID.
    """
    clean_graph(TENANT_ILLEGAL)
    g = queries.get_graph(TENANT_ILLEGAL, "commons")

    # Ancestor quarantined at 1000, intermediate node quarantined, action executed at 2000
    g.query("""
    CREATE (anc:Fact:MemoryNode {id: 'f_anc_quar', status: 'QUARANTINED', quarantined_at: 1000})
    CREATE (mid:Conclusion:MemoryNode {id: 'c_mid_quar', status: 'QUARANTINED', quarantined_at: 1000})
    CREATE (act:Action:MemoryNode {id: 'act_illegal_late_exec', status: 'QUARANTINED', executed: true, executed_at: 2000})
    CREATE (anc)-[:LEADS_TO]->(mid)
    CREATE (mid)-[:LEADS_TO]->(act)
    """)

    inv = queries.invariant_check(TENANT_ILLEGAL)
    print(f"\n[Test 2B] Illegal late action execution results: {inv}")

    assert inv["passed"] is False
    assert inv["actions_executed_after_ancestor_quarantined"] == 1
    assert inv["violating_action_ids"] == ["act_illegal_late_exec"]
    assert inv["violating_node_ids"] == ["act_illegal_late_exec"]


def test_illegal_state_executed_action_under_quarantined_missing_timestamps():
    """
    Test 2C: Artificially create an executed action directly under a quarantined parent
    without proper timestamp attributes (corrupted/tampered state).
    Verifies detection as illegal executed action.
    """
    clean_graph(TENANT_ILLEGAL)
    g = queries.get_graph(TENANT_ILLEGAL, "commons")

    g.query("""
    CREATE (p:Fact:MemoryNode {id: 'f_quar_no_ts', status: 'QUARANTINED'})
    CREATE (act:Action:MemoryNode {id: 'act_exec_no_ts', status: 'QUARANTINED', executed: true})
    CREATE (p)-[:LEADS_TO]->(act)
    """)

    inv = queries.invariant_check(TENANT_ILLEGAL)
    print(f"\n[Test 2C] Missing timestamps executed action results: {inv}")

    assert inv["passed"] is False
    assert inv["actions_executed_after_ancestor_quarantined"] >= 1
    assert "act_exec_no_ts" in inv["violating_action_ids"]


# =========================================================================
# 3. VERIFY DETECTION OF EXACT VIOLATING NODE IDS ACROSS COMPLEX GRAPH
# =========================================================================

def test_mixed_illegal_states_exact_node_ids():
    """
    Test 3: Complex graph with both healthy children of quarantined parents
    AND illegally executed actions on separate branches.
    Verifies exact isolation and collection of all violating IDs without false positives.
    """
    clean_graph(TENANT_ILLEGAL)
    g = queries.get_graph(TENANT_ILLEGAL, "commons")

    # Branch 1: Healthy root, healthy conclusion, valid executed action (NO violation)
    # Branch 2: Quarantined fact, healthy conclusion (Violation: c_orphan_x)
    # Branch 3: Quarantined fact (T=500), Quarantined conclusion, Action executed at T=1500 (Violation: act_late_y)
    # Branch 4: Quarantined fact (T=1000), Legitimate action executed at T=200 before quarantine (NO violation)
    g.query("""
    CREATE (f_clean:Fact:MemoryNode {id: 'f_clean', status: 'HEALTHY'})
    CREATE (c_clean:Conclusion:MemoryNode {id: 'c_clean', status: 'HEALTHY'})
    CREATE (act_clean:Action:MemoryNode {id: 'act_clean', status: 'HEALTHY', executed: true, executed_at: 300})
    CREATE (f_clean)-[:LEADS_TO]->(c_clean)
    CREATE (c_clean)-[:LEADS_TO]->(act_clean)

    CREATE (f_q1:Fact:MemoryNode {id: 'f_q1', status: 'QUARANTINED', quarantined_at: 500})
    CREATE (c_orphan_x:Conclusion:MemoryNode {id: 'c_orphan_x', status: 'HEALTHY'})
    CREATE (f_q1)-[:LEADS_TO]->(c_orphan_x)

    CREATE (f_q2:Fact:MemoryNode {id: 'f_q2', status: 'QUARANTINED', quarantined_at: 500})
    CREATE (c_q2:Conclusion:MemoryNode {id: 'c_q2', status: 'QUARANTINED', quarantined_at: 500})
    CREATE (act_late_y:Action:MemoryNode {id: 'act_late_y', status: 'QUARANTINED', executed: true, executed_at: 1500})
    CREATE (f_q2)-[:LEADS_TO]->(c_q2)
    CREATE (c_q2)-[:LEADS_TO]->(act_late_y)

    CREATE (f_q3:Fact:MemoryNode {id: 'f_q3', status: 'QUARANTINED', quarantined_at: 1000})
    CREATE (act_early_ok:Action:MemoryNode {id: 'act_early_ok', status: 'QUARANTINED', executed: true, executed_at: 200})
    CREATE (f_q3)-[:LEADS_TO]->(act_early_ok)
    """)

    inv = queries.invariant_check(TENANT_ILLEGAL)
    print(f"\n[Test 3] Mixed illegal states audit: {inv}")

    assert inv["passed"] is False
    assert inv["violations"] == 2
    assert inv["healthy_children_under_quarantined"] == 1
    assert inv["actions_executed_after_ancestor_quarantined"] == 1

    # Exact ID assertions
    assert inv["violating_child_ids"] == ["c_orphan_x"]
    assert inv["violating_action_ids"] == ["act_late_y"]
    assert set(inv["violating_node_ids"]) == {"c_orphan_x", "act_late_y"}

    # Verify no false positives
    assert "f_clean" not in inv["violating_node_ids"]
    assert "c_clean" not in inv["violating_node_ids"]
    assert "act_clean" not in inv["violating_node_ids"]
    assert "act_early_ok" not in inv["violating_node_ids"]
    assert "f_q1" not in inv["violating_node_ids"]
    assert "f_q2" not in inv["violating_node_ids"]
    assert "f_q3" not in inv["violating_node_ids"]


def test_healing_resolves_invariants():
    """
    Test 3B: Verify that fixing/quarantining violating nodes clears invariant violations.
    """
    clean_graph(TENANT_ILLEGAL)
    g = queries.get_graph(TENANT_ILLEGAL, "commons")

    g.query("""
    CREATE (f:Fact:MemoryNode {id: 'f_sick', status: 'QUARANTINED', quarantined_at: 1000})
    CREATE (c:Conclusion:MemoryNode {id: 'c_orphan', status: 'HEALTHY'})
    CREATE (f)-[:LEADS_TO]->(c)
    """)

    inv1 = queries.invariant_check(TENANT_ILLEGAL)
    assert inv1["passed"] is False
    assert inv1["violating_child_ids"] == ["c_orphan"]

    # Heal: cascade quarantine down to the orphan
    g.query("MATCH (c:Conclusion {id: 'c_orphan'}) SET c.status = 'QUARANTINED', c.quarantined_at = 1000")

    inv2 = queries.invariant_check(TENANT_ILLEGAL)
    assert inv2["passed"] is True
    assert inv2["violations"] == 0
    assert inv2["violating_node_ids"] == []


# =========================================================================
# 4. HIGH-CONCURRENCY STRESS TEST ON BUMP_EPOCH
# =========================================================================

def test_bump_epoch_1000_concurrency():
    """
    Test 4A: Stress test bump_epoch.
    Simulate 1000 atomic epoch increments under high concurrency (50 worker threads).
    Verify:
      - Strictly monotonically increasing integer sequence.
      - 0 duplicates.
      - 0 drops.
      - Exact final value stored in graph Meta node.
    """
    clean_graph(TENANT_EPOCH)
    g = queries.get_graph(TENANT_EPOCH, "commons")

    # Explicitly initialize epoch to 0
    g.query("MERGE (m:Meta {id: 'meta'}) SET m.epoch = 0")

    N = 1000
    workers = 50
    results: List[int] = []
    errors: List[str] = []
    lock = threading.Lock()

    print(f"\n[Test 4A] Launching 1000 concurrent bump_epoch calls across {workers} threads...")
    t0 = time.time()

    def worker_bump():
        try:
            val = queries.bump_epoch(TENANT_EPOCH)
            with lock:
                results.append(val)
        except Exception as e:
            with lock:
                errors.append(str(e))

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(worker_bump) for _ in range(N)]
        for f in as_completed(futures):
            f.result()

    t_total = time.time() - t0
    ops_sec = N / t_total

    print(f"  --> Completed {N} epoch increments in {t_total:.3f}s ({ops_sec:.1f} ops/sec)")
    print(f"  --> Errors: {len(errors)}")

    assert len(errors) == 0, f"Encountered errors: {errors[:5]}"
    assert len(results) == N, f"Expected {N} results, got {len(results)}"

    # Check for duplicates
    unique_results = set(results)
    duplicate_count = len(results) - len(unique_results)
    print(f"  --> Duplicate values: {duplicate_count}")
    assert duplicate_count == 0, f"Found {duplicate_count} duplicate epoch increments!"

    # Verify strictly monotonic sequence 1..1000
    sorted_results = sorted(results)
    expected_sequence = list(range(1, N + 1))
    assert sorted_results == expected_sequence, "Epoch sequence has gaps or is not strictly monotonic 1..1000"

    # Verify persistence in Meta node
    meta_res = g.query("MATCH (m:Meta {id: 'meta'}) RETURN m.epoch").result_set
    assert meta_res and meta_res[0][0] == N, f"Meta node epoch is {meta_res[0][0]}, expected {N}"
    print(f"  --> Meta node epoch verified: {meta_res[0][0]}")


def test_bump_epoch_multi_tenant_isolation_concurrency():
    """
    Test 4B: Concurrent epoch increments across multiple tenants simultaneously.
    500 bumps on Tenant A and 500 bumps on Tenant B racing in parallel.
    Verifies total tenant isolation: each tenant has its own clean 1..500 sequence.
    """
    clean_graph(TENANT_EPOCH_A)
    clean_graph(TENANT_EPOCH_B)

    g_a = queries.get_graph(TENANT_EPOCH_A, "commons")
    g_b = queries.get_graph(TENANT_EPOCH_B, "commons")

    g_a.query("MERGE (m:Meta {id: 'meta'}) SET m.epoch = 0")
    g_b.query("MERGE (m:Meta {id: 'meta'}) SET m.epoch = 0")

    N_per_tenant = 500
    results_a: List[int] = []
    results_b: List[int] = []
    lock = threading.Lock()

    print("\n[Test 4B] Racing 500 bumps on Tenant A and 500 on Tenant B...")

    def worker_a():
        val = queries.bump_epoch(TENANT_EPOCH_A)
        with lock:
            results_a.append(val)

    def worker_b():
        val = queries.bump_epoch(TENANT_EPOCH_B)
        with lock:
            results_b.append(val)

    with ThreadPoolExecutor(max_workers=40) as executor:
        futures_a = [executor.submit(worker_a) for _ in range(N_per_tenant)]
        futures_b = [executor.submit(worker_b) for _ in range(N_per_tenant)]
        for f in as_completed(futures_a + futures_b):
            f.result()

    assert sorted(results_a) == list(range(1, N_per_tenant + 1))
    assert sorted(results_b) == list(range(1, N_per_tenant + 1))

    meta_a = g_a.query("MATCH (m:Meta {id: 'meta'}) RETURN m.epoch").result_set[0][0]
    meta_b = g_b.query("MATCH (m:Meta {id: 'meta'}) RETURN m.epoch").result_set[0][0]
    assert meta_a == N_per_tenant
    assert meta_b == N_per_tenant
    print(f"  --> Multi-tenant isolation verified: Tenant A final={meta_a}, Tenant B final={meta_b}")
