"""
Subagent 12: Stress-testing Atomic Execute Guard & Quarantine Race Conditions
Module: graph/queries.py (execute_guard, cascade, confirm_action)

Specific Tests:
1. Reproduce the Execute-vs-Quarantine race: 50 action nodes.
   Concurrently trigger execute_guard and quarantine on the root parent fact.
2. Verify invariant: NO action whose parent was quarantined can ever execute (violations = 0).
3. Test double-execute: attempt to execute the same action twice concurrently.
   Verify exactly one execution is confirmed (idempotency).
4. Test execution of action with stale=True parent -> verify rejection.
"""

import time
import random
import threading
from typing import Dict, List, Any
import graph.queries as queries

TENANT = "test_stress_execute"


def clean_graph(tenant_id: str):
    """Resets the graph and ensures indexes exist."""
    g = queries.get_graph(tenant_id, "commons")
    try:
        g.query("MATCH (n) DETACH DELETE n")
    except Exception:
        pass
    queries.ensure_indexes(tenant_id)


# =========================================================================
# TEST 1 & 2: EXECUTE-VS-QUARANTINE RACE (50 ACTIONS CONCURRENT WITH CASCADE)
# =========================================================================

def test_execute_vs_quarantine_race_50_actions():
    """
    Test 1 & 2:
    - 50 action nodes staged under a conclusion derived from a root parent fact.
    - Concurrently trigger execute_guard on all 50 actions while simultaneously
      triggering quarantine cascade on the root parent fact.
    - Synchronized via threading.Barrier to maximize collision probability.
    - Invariant: Zero actions executed with quarantined ancestors (violations = 0).
    """
    clean_graph(TENANT)
    g = queries.get_graph(TENANT, "commons")
    num_actions = 50

    print(f"\n[Test 1 & 2] Running Execute-vs-Quarantine race with {num_actions} actions...")

    # 1. Setup Root Fact and Conclusion
    g.query("CREATE (:Fact:MemoryNode {id: 'f_root_race', status: 'HEALTHY', stale: false, text: 'Root Policy'})")
    der_res = queries.derive(TENANT, "c_race_elig", ["f_root_race"], "Customer Eligible")
    assert der_res["success"] is True

    # 2. Stage 50 action nodes
    for i in range(num_actions):
        aid = f"act_race_{i}"
        st_res = queries.stage_action(
            TENANT,
            action_id=aid,
            parent_ids=["c_race_elig"],
            action_type="REFUND",
            payload={"amount": 10 + i, "user_id": f"u_{i}"},
            reversibility="COMPENSABLE",
            gate="PASSED",
        )
        assert st_res["success"] is True

    # 3. Concurrent race using Barrier(51) [50 execute workers + 1 quarantine worker]
    barrier = threading.Barrier(num_actions + 1)
    executed_results: Dict[str, Dict[str, Any]] = {}
    refused_results: Dict[str, Dict[str, Any]] = {}
    lock = threading.Lock()
    cascade_output: Dict[str, Any] = {}

    def execute_worker(idx: int):
        aid = f"act_race_{idx}"
        # Synchronized barrier release
        barrier.wait()
        # Optional micro-jitter (0 to 1.5ms) to interleave with cascade execution
        time.sleep(random.uniform(0.000, 0.0015))
        res = queries.execute_guard(TENANT, aid)
        with lock:
            if res.get("success"):
                executed_results[aid] = res
            else:
                refused_results[aid] = res

    def quarantine_worker():
        barrier.wait()
        # Small delay to allow some actions to interleave and execute before cascade lands
        time.sleep(0.001)
        res = queries.cascade(TENANT, "f_root_race")
        cascade_output.update(res)

    threads = []
    for i in range(num_actions):
        t = threading.Thread(target=execute_worker, args=(i,))
        threads.append(t)
        t.start()

    q_thread = threading.Thread(target=quarantine_worker)
    threads.append(q_thread)
    q_thread.start()

    for t in threads:
        t.join()

    # 4. Partition analysis
    total_finished = len(executed_results) + len(refused_results)
    assert total_finished == num_actions, f"Expected {num_actions} processed, got {total_finished}"

    print(f"  -> Total actions: {num_actions}")
    print(f"  -> Executed before quarantine: {len(executed_results)}")
    print(f"  -> Refused due to quarantine/bad ancestor: {len(refused_results)}")
    print(f"  -> Cascade affected nodes: {cascade_output.get('affected_count')}")

    # 5. INVARIANT VERIFICATION:
    # Query FalkorDB directly to check if ANY executed action executed after ancestor was quarantined
    inv = queries.invariant_check(TENANT)
    violations = inv["actions_executed_after_ancestor_quarantined"]
    print(f"  -> Actions executed after ancestor quarantined (violations): {violations}")
    assert violations == 0, f"VIOLATION: {violations} actions executed after ancestor was quarantined!"

    # Also check invariant 1: Zero healthy children under quarantined parents
    assert inv["healthy_children_under_quarantined"] == 0, (
        f"VIOLATION: {inv['healthy_children_under_quarantined']} healthy children under quarantined parent!"
    )
    assert inv["all_passed"] is True

    # Detailed timestamp verification for all executed actions
    # Fetch root fact and intermediate conclusion quarantined_at timestamps
    root_props = g.query("MATCH (f:Fact {id: 'f_root_race'}) RETURN f.quarantined_at, f.status").result_set[0]
    root_quarantined_at = root_props[0]
    root_status = root_props[1]
    assert root_status == "QUARANTINED"

    for aid, res in executed_results.items():
        exec_at = res["executed_at"]
        assert exec_at <= root_quarantined_at, (
            f"Action {aid} executed at {exec_at} which is AFTER root quarantined_at {root_quarantined_at}!"
        )

    # 6. Verify that trying to execute ANY refused action now MUST FAIL
    for aid in refused_results.keys():
        retry_res = queries.execute_guard(TENANT, aid)
        assert retry_res["success"] is False, f"Refused action {aid} was unexpectedly permitted to execute on retry!"

    print("[Test 1 & 2 PASSED] Invariant verified: violations = 0.")


def test_execute_vs_quarantine_multi_run_stress():
    """
    Stresses the race across multiple randomized trials to test edge interleavings.
    """
    clean_graph(TENANT)
    g = queries.get_graph(TENANT, "commons")

    for run_id in range(3):
        tenant_run = f"{TENANT}_run_{run_id}"
        clean_graph(tenant_run)
        gr = queries.get_graph(tenant_run, "commons")

        gr.query(f"CREATE (:Fact:MemoryNode {{id: 'f_root_{run_id}', status: 'HEALTHY', stale: false, text: 'Policy' }})")
        queries.derive(tenant_run, f"c_elig_{run_id}", [f"f_root_{run_id}"], "Eligible")

        num_actions = 30
        for i in range(num_actions):
            queries.stage_action(
                tenant_run,
                f"act_{run_id}_{i}",
                [f"c_elig_{run_id}"],
                "PAYMENT",
                {"amount": i},
                "COMPENSABLE",
                gate="PASSED",
            )

        barrier = threading.Barrier(num_actions + 1)
        exec_count = 0
        refused_count = 0
        lock = threading.Lock()

        def worker(idx: int):
            nonlocal exec_count, refused_count
            aid = f"act_{run_id}_{idx}"
            barrier.wait()
            # Random microsecond jitter
            time.sleep(random.uniform(0.0001, 0.002))
            res = queries.execute_guard(tenant_run, aid)
            with lock:
                if res.get("success"):
                    exec_count += 1
                else:
                    refused_count += 1

        def q_worker():
            barrier.wait()
            time.sleep(random.uniform(0.0002, 0.001))
            queries.cascade(tenant_run, f"f_root_{run_id}")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_actions)]
        threads.append(threading.Thread(target=q_worker))
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        inv = queries.invariant_check(tenant_run)
        assert inv["actions_executed_after_ancestor_quarantined"] == 0
        assert inv["healthy_children_under_quarantined"] == 0
        assert exec_count + refused_count == num_actions
        print(f"  -> Run {run_id}: {exec_count} executed, {refused_count} refused, 0 invariant violations.")


# =========================================================================
# TEST 3: DOUBLE-EXECUTE CONCURRENCY & IDEMPOTENCY
# =========================================================================

def test_double_execute_concurrent_idempotency_single_action():
    """
    Test 3 (Part A):
    - Attempt to execute the SAME action 20 times concurrently.
    - Synchronize with Barrier(20).
    - Invariant: Exactly 1 execution succeeds (executed=True).
    - All other 19 calls are rejected.
    - External confirmation is called once, verifying idempotency.
    """
    clean_graph(TENANT)
    g = queries.get_graph(TENANT, "commons")

    print("\n[Test 3A] Testing double-execute concurrency on a single action (20 concurrent threads)...")

    # Setup parent fact, conclusion, and a single action
    g.query("CREATE (:Fact:MemoryNode {id: 'f_double', status: 'HEALTHY', stale: false, text: 'Base Fact'})")
    queries.derive(TENANT, "c_double", ["f_double"], "Double Elig")
    queries.stage_action(
        TENANT,
        action_id="act_double_target",
        parent_ids=["c_double"],
        action_type="REFUND",
        payload={"amount": 250},
        reversibility="COMPENSABLE",
        gate="PASSED",
        idempotency_key="idem_key_unique_999",
    )

    concurrency = 20
    barrier = threading.Barrier(concurrency)
    successes: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []
    lock = threading.Lock()

    def try_execute(thread_id: int):
        barrier.wait()
        res = queries.execute_guard(TENANT, "act_double_target")
        with lock:
            if res.get("success"):
                successes.append({"thread_id": thread_id, "res": res})
            else:
                failures.append({"thread_id": thread_id, "res": res})

    threads = [threading.Thread(target=try_execute, args=(i,)) for i in range(concurrency)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    print(f"  -> Successes: {len(successes)} | Refusals: {len(failures)}")
    assert len(successes) == 1, f"Expected exactly 1 execution to succeed, but got {len(successes)}!"
    assert len(failures) == concurrency - 1, f"Expected {concurrency - 1} failures, got {len(failures)}"

    winner = successes[0]
    assert winner["res"]["executed"] is True
    assert winner["res"]["idempotency_key"] == "idem_key_unique_999"

    # Confirm action
    confirm_ok = queries.confirm_action(TENANT, "act_double_target")
    assert confirm_ok is True

    # Check node state in FalkorDB
    node_state = g.query("""
    MATCH (a:Action {id: 'act_double_target'})
    RETURN a.executed, a.confirmed, a.executed_at, a.idempotency_key
    """).result_set[0]
    assert node_state[0] is True  # executed
    assert node_state[1] is True  # confirmed
    assert node_state[2] is not None  # executed_at timestamp
    assert node_state[3] == "idem_key_unique_999"

    # Subsequent execute attempts MUST fail
    subsequent_res = queries.execute_guard(TENANT, "act_double_target")
    assert subsequent_res["success"] is False

    # Confirm action idempotency: calling confirm_action again succeeds idempotently
    confirm_again = queries.confirm_action(TENANT, "act_double_target")
    assert confirm_again is True

    print("[Test 3A PASSED] Exactly 1 execution confirmed. Idempotency verified.")


def test_double_execute_concurrent_multiple_actions():
    """
    Test 3 (Part B):
    - 10 distinct actions, each hammered by 10 concurrent threads simultaneously.
    - Total: 100 concurrent execute_guard calls.
    - Exactly 1 winner per action (10 total winners, 90 refusals).
    """
    clean_graph(TENANT)
    g = queries.get_graph(TENANT, "commons")
    num_actions = 10
    threads_per_action = 10
    total_calls = num_actions * threads_per_action

    print(f"\n[Test 3B] Testing double-execute across {num_actions} actions x {threads_per_action} threads ({total_calls} calls)...")

    g.query("CREATE (:Fact:MemoryNode {id: 'f_multi', status: 'HEALTHY', stale: false, text: 'Multi Fact'})")
    queries.derive(TENANT, "c_multi", ["f_multi"], "Multi Conclusion")

    for i in range(num_actions):
        queries.stage_action(
            TENANT,
            action_id=f"act_multi_{i}",
            parent_ids=["c_multi"],
            action_type="PAYMENT",
            payload={"id": i},
            reversibility="COMPENSABLE",
            gate="PASSED",
        )

    barrier = threading.Barrier(total_calls)
    action_winners: Dict[str, List[int]] = {f"act_multi_{i}": [] for i in range(num_actions)}
    action_refusals: Dict[str, int] = {f"act_multi_{i}": 0 for i in range(num_actions)}
    lock = threading.Lock()

    def worker(act_idx: int, thread_idx: int):
        aid = f"act_multi_{act_idx}"
        barrier.wait()
        res = queries.execute_guard(TENANT, aid)
        with lock:
            if res.get("success"):
                action_winners[aid].append(thread_idx)
            else:
                action_refusals[aid] += 1

    threads = []
    for a in range(num_actions):
        for t in range(threads_per_action):
            th = threading.Thread(target=worker, args=(a, t))
            threads.append(th)
            th.start()

    for th in threads:
        th.join()

    for aid in action_winners:
        winners = action_winners[aid]
        refusals = action_refusals[aid]
        assert len(winners) == 1, f"Action {aid} had {len(winners)} winners: {winners}"
        assert refusals == threads_per_action - 1
        # Confirm action
        queries.confirm_action(TENANT, aid)

    # Verify all 10 actions are executed and confirmed in FalkorDB
    res_db = g.query("MATCH (a:Action) WHERE a.executed = true AND a.confirmed = true RETURN count(a)").result_set[0][0]
    assert res_db == num_actions

    print(f"[Test 3B PASSED] All {num_actions} actions had exactly 1 winner out of {threads_per_action} concurrent attempts.")


# =========================================================================
# TEST 4: EXECUTION OF ACTION WITH STALE=TRUE PARENT -> VERIFY REJECTION
# =========================================================================

def test_execute_guard_rejects_stale_direct_parent():
    """
    Test 4 (Part A):
    Action has a direct parent Fact with stale=True (and status='HEALTHY').
    execute_guard must reject the execution.
    """
    clean_graph(TENANT)
    g = queries.get_graph(TENANT, "commons")

    print("\n[Test 4A] Testing execute_guard rejection with stale=True direct parent...")

    g.query("""
    CREATE (f:Fact:MemoryNode {id: 'f_stale_direct', status: 'HEALTHY', stale: true, text: 'Old Policy Fact'})
    CREATE (a:Action:MemoryNode {
        id: 'act_stale_direct', status: 'HEALTHY', executed: false, gate: 'PASSED',
        reversibility: 'COMPENSABLE', type: 'REFUND', payload: '{}',
        stale: false, created_at: timestamp()
    })
    CREATE (f)-[:LEADS_TO]->(a)
    """)

    res = queries.execute_guard(TENANT, "act_stale_direct")
    print(f"  -> execute_guard result: {res}")

    assert res["success"] is False, "execute_guard MUST REJECT action when parent is stale=True!"
    assert "refused" in res["reason"].lower() or "stale" in res["reason"].lower()

    # Verify in DB that action remains UNEXECUTED
    db_act = g.query("MATCH (a:Action {id: 'act_stale_direct'}) RETURN a.executed").result_set[0][0]
    assert db_act is False, "Action in database must remain executed=false"
    print("[Test 4A PASSED] Direct stale parent was correctly rejected.")


def test_execute_guard_rejects_stale_multi_hop_ancestor():
    """
    Test 4 (Part B):
    Multi-hop causal lineage: Root Fact (stale=True) -> Conclusion (stale=False) -> Action (stale=False).
    execute_guard must traverse upstream [:LEADS_TO|SUPPORTS*] and reject execution.
    """
    clean_graph(TENANT)
    g = queries.get_graph(TENANT, "commons")

    print("\n[Test 4B] Testing execute_guard rejection with multi-hop stale ancestor...")

    g.query("""
    CREATE (f:Fact:MemoryNode {id: 'f_stale_root', status: 'HEALTHY', stale: true, text: 'Root Outdated Policy'})
    CREATE (c:Conclusion:MemoryNode {id: 'c_derived', status: 'HEALTHY', stale: false, text: 'Derived conclusion'})
    CREATE (a:Action:MemoryNode {
        id: 'act_deep_stale', status: 'HEALTHY', executed: false, gate: 'PASSED',
        reversibility: 'COMPENSABLE', type: 'REFUND', payload: '{}',
        stale: false, created_at: timestamp()
    })
    CREATE (f)-[:LEADS_TO]->(c)
    CREATE (c)-[:LEADS_TO]->(a)
    """)

    res = queries.execute_guard(TENANT, "act_deep_stale")
    print(f"  -> execute_guard result: {res}")

    assert res["success"] is False, "execute_guard MUST REJECT action when multi-hop ancestor is stale=True!"
    db_act = g.query("MATCH (a:Action {id: 'act_deep_stale'}) RETURN a.executed").result_set[0][0]
    assert db_act is False
    print("[Test 4B PASSED] Multi-hop stale ancestor was correctly rejected.")


def test_execute_guard_rejects_stale_action_itself():
    """
    Test 4 (Part C):
    The Action node itself has stale=True (e.g., invalidated directly).
    execute_guard must reject execution.
    """
    clean_graph(TENANT)
    g = queries.get_graph(TENANT, "commons")

    print("\n[Test 4C] Testing execute_guard rejection when action itself is stale=True...")

    g.query("""
    CREATE (f:Fact:MemoryNode {id: 'f_healthy', status: 'HEALTHY', stale: false, text: 'Healthy Fact'})
    CREATE (a:Action:MemoryNode {
        id: 'act_self_stale', status: 'HEALTHY', executed: false, gate: 'PASSED',
        reversibility: 'COMPENSABLE', type: 'REFUND', payload: '{}',
        stale: true, created_at: timestamp()
    })
    CREATE (f)-[:LEADS_TO]->(a)
    """)

    res = queries.execute_guard(TENANT, "act_self_stale")
    print(f"  -> execute_guard result: {res}")

    assert res["success"] is False, "execute_guard MUST REJECT action when action itself is stale=True!"
    db_act = g.query("MATCH (a:Action {id: 'act_self_stale'}) RETURN a.executed").result_set[0][0]
    assert db_act is False
    print("[Test 4C PASSED] Self-stale action was correctly rejected.")


def test_execute_guard_after_supersede_stale_propagation():
    """
    Test 4 (Part D):
    End-to-end integration test:
    1. Fact A is created.
    2. Conclusion C is derived from Fact A.
    3. Action Act is staged from Conclusion C.
    4. Fact A is superseded via queries.supersede() -> propagates stale=True to Conclusion C.
    5. execute_guard is called on Act -> must be rejected because its parent Conclusion is now stale!
    """
    clean_graph(TENANT)
    g = queries.get_graph(TENANT, "commons")

    print("\n[Test 4D] Testing execute_guard after supersede stale propagation...")

    g.query("""
    CREATE (f:Fact:MemoryNode {
        id: 'f_refund_policy_v1', key: 'refund.window', value: '30',
        status: 'HEALTHY', stale: false, text: '30 days refund window'
    })
    """)

    der = queries.derive(TENANT, "c_refund_allowed", ["f_refund_policy_v1"], "Refund allowed under 30 days")
    assert der["success"] is True

    stage = queries.stage_action(
        TENANT,
        "act_refund_payout",
        ["c_refund_allowed"],
        "REFUND",
        {"amount": 50},
        "COMPENSABLE",
        gate="PASSED",
    )
    assert stage["success"] is True

    # Supersede the old policy
    dummy_vec = [0.05] * 384
    sup_res = queries.supersede(
        TENANT,
        old_fact_id="f_refund_policy_v1",
        new_fact_id="f_refund_policy_v2",
        new_value="14",
        new_text="14 days updated refund window",
        new_embedding=dummy_vec,
    )
    assert sup_res["success"] is True
    assert sup_res["stale_conclusions_count"] >= 1

    # Verify conclusion is now stale=true
    c_stale = g.query("MATCH (c:Conclusion {id: 'c_refund_allowed'}) RETURN c.stale").result_set[0][0]
    assert c_stale is True

    # Now attempt execute_guard on the staged action
    exec_res = queries.execute_guard(TENANT, "act_refund_payout")
    print(f"  -> execute_guard result after supersede: {exec_res}")

    assert exec_res["success"] is False, (
        "execute_guard MUST REJECT action whose parent conclusion was flagged stale by policy supersede!"
    )

    db_act = g.query("MATCH (a:Action {id: 'act_refund_payout'}) RETURN a.executed").result_set[0][0]
    assert db_act is False
    print("[Test 4D PASSED] End-to-end supersede stale propagation correctly blocked action execution.")


# =========================================================================
# MAIN RUNNER
# =========================================================================

if __name__ == "__main__":
    print("=================================================================")
    print("SUBAGENT 12: STRESS-TESTING ATOMIC EXECUTE GUARD & QUARANTINE")
    print("=================================================================")
    test_execute_vs_quarantine_race_50_actions()
    test_execute_vs_quarantine_multi_run_stress()
    test_double_execute_concurrent_idempotency_single_action()
    test_double_execute_concurrent_multiple_actions()
    test_execute_guard_rejects_stale_direct_parent()
    test_execute_guard_rejects_stale_multi_hop_ancestor()
    test_execute_guard_rejects_stale_action_itself()
    test_execute_guard_after_supersede_stale_propagation()
    print("\n=================================================================")
    print("ALL STRESS TESTS & INVARIANT CHECKS COMPLETED SUCCESSFULLY (100% PASS)")
    print("=================================================================")
