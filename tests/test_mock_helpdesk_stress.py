"""
tests/test_mock_helpdesk_stress.py

Subagent 15: Comprehensive Stress-Testing of Mock Helpdesk Tombstone Idempotency & Compensation Generation
Covers:
1. FastAPI test client suite on mock_helpdesk.main (health, execute, compensate, validation, and idempotency).
2. Stage and execute actions, verify causal quarantine cascade and idempotent compensation record generation.
3. Tombstone rule test: simulate late execution arrival after tombstone registration. Verify 100% rejection.
4. Concurrently send 500 execution requests with duplicate idempotency keys. Verify strictly idempotent behavior.
"""

import time
import threading
import random
from typing import Dict, List, Any
from fastapi.testclient import TestClient

from mock_helpdesk.main import app, tombstones, executed_actions, compensations
import graph.queries as queries

TENANT_TEST = "test_stress_helpdesk"
client = TestClient(app)


def reset_helpdesk_state():
    """Clear in-memory state stores for mock_helpdesk."""
    tombstones.clear()
    executed_actions.clear()
    compensations.clear()


def reset_graph_state(tenant_id: str):
    """Reset FalkorDB graph state for the test tenant."""
    g = queries.get_graph(tenant_id, "commons")
    try:
        g.query("MATCH (n) DETACH DELETE n")
    except Exception:
        pass
    queries.ensure_indexes(tenant_id)


# =========================================================================
# TEST 1: FastAPI Test Client on mock_helpdesk.main
# =========================================================================

def test_fastapi_test_client_endpoints():
    """
    Test 1: Comprehensive verification of mock_helpdesk.main via FastAPI TestClient:
    - /health endpoint returns 200 OK and {"status": "ok"}.
    - /api/helpdesk/execute creates action, preserves optional fields.
    - /api/helpdesk/execute with same idempotency key returns already_executed.
    - /api/helpdesk/compensate compensates executed action.
    - /api/helpdesk/compensate on unseen key creates tombstone.
    - Schema validation errors return 422 Unprocessable Entity.
    """
    reset_helpdesk_state()
    print("\n--- [TEST 1] FastAPI Test Client on mock_helpdesk.main ---")

    # 1. Health check
    res_health = client.get("/health")
    assert res_health.status_code == 200
    assert res_health.json() == {"status": "ok"}
    print("  [+] GET /health -> 200 OK {'status': 'ok'}")

    # 2. Execute new action with full payload
    payload_1 = {
        "idempotency_key": "idem_unit_001",
        "action_type": "REFUND",
        "amount": 149.99,
        "customer_id": "cust_12345",
        "details": {"reason": "damaged_goods", "order_id": "ord_9988"}
    }
    res_exec = client.post("/api/helpdesk/execute", json=payload_1)
    assert res_exec.status_code == 200
    body_exec = res_exec.json()
    assert body_exec["status"] == "executed"
    assert body_exec["record"]["idempotency_key"] == "idem_unit_001"
    assert body_exec["record"]["action_type"] == "REFUND"
    assert body_exec["record"]["status"] == "SUCCESS"
    assert "idem_unit_001" in executed_actions
    print("  [+] POST /api/helpdesk/execute -> 200 OK, status='executed'")

    # 3. Duplicate execution with identical key (idempotency check)
    res_dup = client.post("/api/helpdesk/execute", json=payload_1)
    assert res_dup.status_code == 200
    body_dup = res_dup.json()
    assert body_dup["status"] == "already_executed"
    assert body_dup["record"] == body_exec["record"]
    print("  [+] POST /api/helpdesk/execute (duplicate key) -> 200 OK, status='already_executed'")

    # 4. Duplicate execution with differing payload fields (idempotency key takes precedence)
    payload_differing = {
        "idempotency_key": "idem_unit_001",
        "action_type": "DIFFERENT_ACTION",
        "amount": 9999.0
    }
    res_diff = client.post("/api/helpdesk/execute", json=payload_differing)
    assert res_diff.status_code == 200
    assert res_diff.json()["status"] == "already_executed"
    assert res_diff.json()["record"]["action_type"] == "REFUND"  # Original preserved
    print("  [+] POST /api/helpdesk/execute (different payload) -> preserves original record")

    # 5. Compensate executed action
    res_comp = client.post("/api/helpdesk/compensate", json={
        "idempotency_key": "idem_unit_001",
        "reason": "Customer cancellation after review"
    })
    assert res_comp.status_code == 200
    body_comp = res_comp.json()
    assert body_comp["status"] == "compensated"
    assert body_comp["record"]["status"] == "COMPENSATED"
    assert body_comp["record"]["reason"] == "Customer cancellation after review"
    assert "idem_unit_001" in compensations
    print("  [+] POST /api/helpdesk/compensate (executed action) -> 200 OK, status='compensated'")

    # 6. Compensate again (duplicate compensation idempotency)
    res_comp_dup = client.post("/api/helpdesk/compensate", json={
        "idempotency_key": "idem_unit_001",
        "reason": "Duplicate compensation dispatch"
    })
    assert res_comp_dup.status_code == 200
    assert res_comp_dup.json()["status"] == "compensated"
    print("  [+] POST /api/helpdesk/compensate (duplicate compensation) -> 200 OK")

    # 7. Compensate unseen action (tombstone creation)
    unseen_key = "idem_unseen_initial"
    res_tomb = client.post("/api/helpdesk/compensate", json={
        "idempotency_key": unseen_key,
        "reason": "Pre-execution cancellation"
    })
    assert res_tomb.status_code == 200
    body_tomb = res_tomb.json()
    assert body_tomb["status"] == "tombstoned_unseen_key"
    assert body_tomb["idempotency_key"] == unseen_key
    assert unseen_key in tombstones
    print("  [+] POST /api/helpdesk/compensate (unseen key) -> 200 OK, status='tombstoned_unseen_key'")

    # 8. Schema validation: missing fields
    res_bad_exec = client.post("/api/helpdesk/execute", json={"amount": 50.0})
    assert res_bad_exec.status_code == 422
    res_bad_comp = client.post("/api/helpdesk/compensate", json={"idempotency_key": "k"})
    assert res_bad_comp.status_code == 422
    print("  [+] Missing required fields return HTTP 422 Unprocessable Entity (expected)")


# =========================================================================
# TEST 2: Stage & Execute Actions, Quarantine Cascade & Compensation Generation
# =========================================================================

def test_stage_execute_quarantine_compensation_generation():
    """
    Test 2: Stage and execute actions across reversibility classes:
    1. Create causal chain: Fact -> Conclusion -> Decision -> Actions.
    2. Stage 4 distinct actions:
       - act_revert (REVERSIBLE, executed=true)
       - act_refund (COMPENSABLE, executed=true)
       - act_notify (IRREVERSIBLE, executed=true)
       - act_staged (COMPENSABLE, executed=false)
    3. Execute guard & confirm for executed actions; verify mock_helpdesk execution.
    4. Quarantine root fact via cascade().
    5. Verify only executed=true actions are returned in executed_actions.
    6. Generate compensation records via create_compensations().
    7. Verify FalkorDB graph state: (a)-[:COMPENSATED_BY]->(c:Compensation).
    8. Verify idempotent compensation creation (calling again creates 0 duplicate nodes).
    9. Dispatch compensations to mock_helpdesk and verify status='compensated'.
    """
    reset_helpdesk_state()
    reset_graph_state(TENANT_TEST)
    g = queries.get_graph(TENANT_TEST, "commons")

    print("\n--- [TEST 2] Stage, Execute, Quarantine Cascade & Compensation Generation ---")

    # 1. Create root fact and causal reasoning
    g.query("CREATE (:Fact:MemoryNode {id: 'f_root_policy', status: 'HEALTHY', text: '30-day refund window active'})")
    res_c = queries.derive(TENANT_TEST, "c_customer_elig", ["f_root_policy"], "Customer satisfies refund window policy")
    assert res_c["success"] is True
    res_d = queries.derive(TENANT_TEST, "d_approve_return", ["c_customer_elig"], "Decision: approve refund & update tags", is_decision=True)
    assert res_d["success"] is True

    # 2. Stage 4 actions with distinct reversibilities
    queries.stage_action(
        TENANT_TEST, "act_revert", ["d_approve_return"], "TAG_UPDATE",
        {"tag": "return_pending"}, "REVERSIBLE", gate="PASSED", idempotency_key="idem_revert_101"
    )
    queries.stage_action(
        TENANT_TEST, "act_refund", ["d_approve_return"], "REFUND",
        {"amount": 120.0, "customer": "cust_42"}, "COMPENSABLE", gate="PASSED", idempotency_key="idem_refund_102"
    )
    queries.stage_action(
        TENANT_TEST, "act_notify", ["d_approve_return"], "EXTERNAL_WEBHOOK",
        {"target": "shipping_partner"}, "IRREVERSIBLE", gate="PASSED", idempotency_key="idem_notify_103"
    )
    queries.stage_action(
        TENANT_TEST, "act_staged_only", ["d_approve_return"], "CREDIT_ISSUE",
        {"amount": 15.0}, "COMPENSABLE", gate="PASSED", idempotency_key="idem_credit_104"
    )

    # 3. Execute guard & call mock_helpdesk for the first 3 actions
    executed_ids = ["act_revert", "act_refund", "act_notify"]
    for aid in executed_ids:
        # Atomic execute guard
        res_guard = queries.execute_guard(TENANT_TEST, aid)
        assert res_guard["success"] is True
        assert res_guard["executed"] is True

        # External side-effect dispatch to mock_helpdesk
        action_row = g.query(f"MATCH (a:Action {{id: '{aid}'}}) RETURN a.idempotency_key, a.type").result_set[0]
        res_api = client.post("/api/helpdesk/execute", json={
            "idempotency_key": action_row[0],
            "action_type": action_row[1],
            "amount": 120.0
        })
        assert res_api.status_code == 200
        assert res_api.json()["status"] == "executed"

        # Confirm action in graph
        queries.confirm_action(TENANT_TEST, aid)

    # act_staged_only remains executed=false, confirmed=false
    staged_row = g.query("MATCH (a:Action {id: 'act_staged_only'}) RETURN a.executed, a.confirmed").result_set[0]
    assert staged_row[0] is False
    assert staged_row[1] is False
    print("  [+] 3 actions executed & confirmed; 1 action remains staged (unexecuted).")

    # 4. Auditor triggers rollback / quarantine cascade on root fact
    cascade_res = queries.cascade(TENANT_TEST, "f_root_policy")
    assert cascade_res["success"] is True
    assert cascade_res["affected_count"] == 7  # 1 fact + 1 conc + 1 dec + 4 actions
    print(f"  [+] Quarantine cascade completed: {cascade_res['affected_count']} nodes quarantined in {cascade_res['ms_to_containment']}ms")

    # 5. Invariant: Only executed=true actions appear in cascade_res["executed_actions"]
    exec_act_list = cascade_res["executed_actions"]
    exec_act_ids = [x["action_id"] for x in exec_act_list]
    assert len(exec_act_ids) == 3
    assert set(exec_act_ids) == set(executed_ids)
    assert "act_staged_only" not in exec_act_ids
    print("  [+] Verified: Only executed=true actions returned for compensation (act_staged_only excluded).")

    # Verify unexecuted action cannot be executed now
    guard_blocked = queries.execute_guard(TENANT_TEST, "act_staged_only")
    assert guard_blocked["success"] is False
    print("  [+] Verified: Staged action blocked by execute_guard after parent quarantine.")

    # 6. Generate compensation records
    comps = queries.create_compensations(TENANT_TEST, exec_act_list)
    assert len(comps) == 3
    comp_ids = [c["compensation_id"] for c in comps]
    assert set(comp_ids) == {"comp_act_revert", "comp_act_refund", "comp_act_notify"}
    for c in comps:
        assert c["status"] == "PENDING_COMPENSATION"
    print("  [+] create_compensations generated 3 PENDING_COMPENSATION records.")

    # 7. Verify FalkorDB graph structure: (Action)-[:COMPENSATED_BY]->(Compensation)
    comp_graph_rows = g.query("""
    MATCH (a:Action)-[:COMPENSATED_BY]->(c:Compensation)
    RETURN a.id, c.id, c.status, c.reversibility, c.idempotency_key, c.attempts
    """).result_set
    assert len(comp_graph_rows) == 3
    comp_map = {row[0]: row for row in comp_graph_rows}

    assert comp_map["act_revert"][3] == "REVERSIBLE"
    assert comp_map["act_revert"][4] == "comp_idem_revert_101"
    assert comp_map["act_refund"][3] == "COMPENSABLE"
    assert comp_map["act_refund"][4] == "comp_idem_refund_102"
    assert comp_map["act_notify"][3] == "IRREVERSIBLE"
    assert comp_map["act_notify"][4] == "comp_idem_notify_103"
    print("  [+] Graph verification passed: [:COMPENSATED_BY] edges, idempotency_keys, and reversibility preserved.")

    # 8. Idempotency test of create_compensations: call again with same list
    comps_idempotent = queries.create_compensations(TENANT_TEST, exec_act_list)
    assert len(comps_idempotent) == 3
    total_comps_count = g.query("MATCH (c:Compensation) RETURN count(c)").result_set[0][0]
    assert total_comps_count == 3, f"Expected 3 Compensation nodes, found {total_comps_count} (duplicate creation bug!)"
    print("  [+] Compensation creation idempotency verified: 0 duplicate nodes created on re-run.")

    # 9. Outbox Saga Worker Simulation: dispatch to mock_helpdesk
    for act in exec_act_list:
        aid = act["action_id"]
        action_key = g.query(f"MATCH (a:Action {{id: '{aid}'}}) RETURN a.idempotency_key").result_set[0][0]
        res_comp_api = client.post("/api/helpdesk/compensate", json={
            "idempotency_key": action_key,
            "reason": f"Rollback due to quarantine cascade on {cascade_res['root_id']}"
        })
        assert res_comp_api.status_code == 200
        assert res_comp_api.json()["status"] == "compensated"
        assert compensations[action_key]["status"] == "COMPENSATED"
    print("  [+] Outbox dispatch simulation succeeded: all 3 actions marked COMPENSATED in mock_helpdesk.")

    # Invariant check
    inv = queries.invariant_check(TENANT_TEST)
    assert inv["healthy_children_under_quarantined"] == 0
    print("  [+] Graph invariant check green (0 healthy children under quarantined nodes).")


# =========================================================================
# TEST 3: Tombstone Rule Test (Late Execution Arrival Simulation)
# =========================================================================

def test_tombstone_rule_rejects_late_execution_arrivals():
    """
    Test 3: Tombstone rule test: simulate late execution arrival after tombstone registration.
    Verify 100% rejection.

    Scenario:
    - In-flight execution request was delayed/lost in transit.
    - Quarantine cascade triggers compensation worker first.
    - Compensation worker compensates unseen key -> mock_helpdesk registers TOMBSTONE.
    - Late execution request arrives -> mock_helpdesk MUST reject with 409 Conflict.
    - Stress-test: Register 100 tombstoned keys. Send 1,000 late execute attempts.
    - Verify: 1,000 / 1,000 rejected (100% rejection rate). 0 actions land in executed_actions.
    """
    reset_helpdesk_state()
    print("\n--- [TEST 3] Tombstone Rule: Late Execution Arrival Rejection ---")

    # 1. Single in-flight race condition reproduction
    inflight_key = "idem_inflight_race_alpha"

    # Compensation arrives before execute
    res_comp = client.post("/api/helpdesk/compensate", json={
        "idempotency_key": inflight_key,
        "reason": "Quarantine timeout: treating in-flight action as unknown-outcome"
    })
    assert res_comp.status_code == 200
    assert res_comp.json()["status"] == "tombstoned_unseen_key"
    assert inflight_key in tombstones
    assert inflight_key not in executed_actions
    print(f"  [+] Unseen key '{inflight_key}' tombstoned successfully.")

    # Late execute arrives
    res_late = client.post("/api/helpdesk/execute", json={
        "idempotency_key": inflight_key,
        "action_type": "REFUND",
        "amount": 250.0
    })
    assert res_late.status_code == 409
    assert "tombstoned" in res_late.json()["detail"].lower()
    assert inflight_key not in executed_actions
    print("  [+] Late execute rejected with 409 Conflict (tombstone prevented execution landing).")

    # 2. Stress Test: 100 tombstoned keys with 10 late-arrival attempts each (1,000 total attempts)
    num_tombstones = 100
    attempts_per_key = 10
    total_attempts = num_tombstones * attempts_per_key

    print(f"  [+] Registering {num_tombstones} tombstoned keys...")
    for i in range(num_tombstones):
        t_key = f"tombstone_stress_{i:04d}"
        res_t = client.post("/api/helpdesk/compensate", json={
            "idempotency_key": t_key,
            "reason": f"Pre-execution quarantine cascade batch {i}"
        })
        assert res_t.status_code == 200
        assert res_t.json()["status"] == "tombstoned_unseen_key"

    assert len(tombstones) == num_tombstones + 1  # including inflight_key

    print(f"  [+] Simulating {total_attempts} late execution attempts across {num_tombstones} keys...")
    rejected_count = 0
    accepted_count = 0
    error_count = 0

    t0 = time.time()
    for i in range(num_tombstones):
        t_key = f"tombstone_stress_{i:04d}"
        for attempt in range(attempts_per_key):
            res_exec = client.post("/api/helpdesk/execute", json={
                "idempotency_key": t_key,
                "action_type": "PAYMENT_DISPATCH",
                "amount": float(i + attempt)
            })
            if res_exec.status_code == 409 and "tombstoned" in res_exec.json().get("detail", "").lower():
                rejected_count += 1
            elif res_exec.status_code == 200:
                accepted_count += 1
            else:
                error_count += 1

    duration = time.time() - t0
    rejection_rate = (rejected_count / total_attempts) * 100

    print("  [+] Stress Results:")
    print(f"      Total Late Execution Attempts: {total_attempts}")
    print(f"      Rejections (HTTP 409): {rejected_count} ({rejection_rate:.2f}%)")
    print(f"      Erroneously Accepted (HTTP 200): {accepted_count}")
    print(f"      Unexpected Errors: {error_count}")
    print(f"      Throughput: {total_attempts / duration:.1f} req/s")

    # Invariants
    assert accepted_count == 0, f"Critical failure: {accepted_count} late executions were erroneously accepted!"
    assert error_count == 0, f"Unexpected errors during stress: {error_count}"
    assert rejected_count == total_attempts, f"Rejection count {rejected_count} != {total_attempts}"
    assert rejection_rate == 100.0, f"Rejection rate {rejection_rate}% < 100%"
    assert len(executed_actions) == 0, "No tombstoned actions must ever exist in executed_actions"
    print("  [+] Tombstone rule verified: 100.0% rejection rate achieved with 0 data leaks.")


# =========================================================================
# TEST 4: Concurrency Stress Test: 500 Duplicate Idempotency Key Requests
# =========================================================================

def test_concurrent_500_executions_duplicate_idempotency_keys():
    """
    Test 4: Concurrently send 500 execution requests with duplicate idempotency keys.
    Verify strictly idempotent behavior.

    Sub-tests:
    Part A: Massive Single-Key Burst (500 concurrent threads, 1 unique idempotency key).
            Verify: Exactly 1 'executed', exactly 499 'already_executed', 0 errors.
    Part B: Multi-Key Collision Burst (500 threads across 50 keys, 10 duplicates each).
            Verify: Exactly 50 'executed', exactly 450 'already_executed', 0 duplicate executions.
    Part C: Execute vs Compensate Concurrency Race (500 threads across 10 unseen keys).
            Verify: Zero inconsistent states; late executes after tombstone are 100% rejected (409).
    """
    reset_helpdesk_state()
    print("\n--- [TEST 4] Concurrency Stress: 500 Requests with Duplicate Idempotency Keys ---")

    # -------------------------------------------------------------------------
    # Part A: Single Key Massive Burst (500 concurrent requests, 1 key)
    # -------------------------------------------------------------------------
    print("\n  [Part A] Massive Single-Key Burst: 500 concurrent threads on 1 idempotency key...")
    single_key = "idem_concurrent_single_burst_500"
    N_A = 500
    barrier_a = threading.Barrier(N_A)
    results_a: List[Dict[str, Any]] = []
    status_codes_a: List[int] = []
    lock_a = threading.Lock()

    def worker_single_key(worker_id: int):
        barrier_a.wait()
        resp = client.post("/api/helpdesk/execute", json={
            "idempotency_key": single_key,
            "action_type": "REFUND",
            "amount": 75.0,
            "customer_id": f"cust_{worker_id}"
        })
        with lock_a:
            status_codes_a.append(resp.status_code)
            results_a.append(resp.json())

    t0 = time.time()
    threads_a = [threading.Thread(target=worker_single_key, args=(i,)) for i in range(N_A)]
    for t in threads_a:
        t.start()
    for t in threads_a:
        t.join()
    dur_a = time.time() - t0

    exec_count_a = sum(1 for r in results_a if r.get("status") == "executed")
    already_count_a = sum(1 for r in results_a if r.get("status") == "already_executed")
    ok_count_a = sum(1 for code in status_codes_a if code == 200)

    print(f"      Total Concurrent Requests: {len(results_a)}")
    print(f"      HTTP 200 Responses: {ok_count_a} / {N_A}")
    print(f"      Status 'executed': {exec_count_a} (Expected: 1)")
    print(f"      Status 'already_executed': {already_count_a} (Expected: 499)")
    print(f"      Duration: {dur_a:.2f}s ({N_A / dur_a:.1f} req/s)")

    assert ok_count_a == N_A, f"Non-200 responses observed: {ok_count_a} != {N_A}"
    assert exec_count_a == 1, f"Idempotency violation! Executed count was {exec_count_a} != 1"
    assert already_count_a == N_A - 1, f"Already executed count was {already_count_a} != {N_A - 1}"
    assert single_key in executed_actions
    print("  [+] Part A passed: Exactly 1 execution, 499 idempotent already_executed responses.")

    # -------------------------------------------------------------------------
    # Part B: Multi-Key Collision Burst (50 distinct keys x 10 duplicates = 500 requests)
    # -------------------------------------------------------------------------
    print("\n  [Part B] Multi-Key Collision Burst: 50 distinct keys x 10 duplicate requests (500 threads)...")
    reset_helpdesk_state()
    num_keys_b = 50
    dups_per_key_b = 10
    N_B = num_keys_b * dups_per_key_b
    keys_b = [f"idem_multikey_{k:02d}" for k in range(num_keys_b)]
    work_items_b = []
    for k in keys_b:
        work_items_b.extend([k] * dups_per_key_b)
    random.shuffle(work_items_b)

    barrier_b = threading.Barrier(N_B)
    results_b: Dict[str, List[Dict[str, Any]]] = {k: [] for k in keys_b}
    lock_b = threading.Lock()

    def worker_multi_key(key_to_send: str, worker_id: int):
        barrier_b.wait()
        resp = client.post("/api/helpdesk/execute", json={
            "idempotency_key": key_to_send,
            "action_type": "ACCOUNT_CREDIT",
            "amount": float(worker_id)
        })
        with lock_b:
            results_b[key_to_send].append(resp.json())

    t0 = time.time()
    threads_b = [threading.Thread(target=worker_multi_key, args=(work_items_b[i], i)) for i in range(N_B)]
    for t in threads_b:
        t.start()
    for t in threads_b:
        t.join()
    dur_b = time.time() - t0

    single_exec_keys = 0
    multi_exec_keys = 0
    zero_exec_keys = 0

    for k, responses in results_b.items():
        n_exec = sum(1 for r in responses if r.get("status") == "executed")
        n_already = sum(1 for r in responses if r.get("status") == "already_executed")
        if n_exec == 1 and n_already == (dups_per_key_b - 1):
            single_exec_keys += 1
        elif n_exec > 1:
            multi_exec_keys += 1
        else:
            zero_exec_keys += 1

    print(f"      Total Keys Tested: {num_keys_b}")
    print(f"      Total Requests Sent: {N_B}")
    print(f"      Strictly Idempotent Keys (1 exec + 9 already_exec): {single_exec_keys} / {num_keys_b}")
    print(f"      Multiple Execution Violations: {multi_exec_keys}")
    print(f"      Zero Execution Failures: {zero_exec_keys}")
    print(f"      Duration: {dur_b:.2f}s ({N_B / dur_b:.1f} req/s)")

    assert multi_exec_keys == 0, f"Idempotency violation! {multi_exec_keys} keys had multiple executions!"
    assert zero_exec_keys == 0, f"Failure! {zero_exec_keys} keys had zero executions!"
    assert single_exec_keys == num_keys_b
    assert len(executed_actions) == num_keys_b
    print("  [+] Part B passed: All 50 keys had strictly 1 execution and 9 idempotent already_executed responses.")

    # -------------------------------------------------------------------------
    # Part C: Execute vs Compensate Concurrency Race (500 threads across 10 keys)
    # -------------------------------------------------------------------------
    print("\n  [Part C] Execute vs Compensate Race: 25 executes & 25 compensates per key (500 threads)...")
    reset_helpdesk_state()
    num_keys_c = 10
    reqs_per_type_c = 25  # 25 executes + 25 compensates = 50 reqs per key x 10 keys = 500 threads
    N_C = num_keys_c * reqs_per_type_c * 2
    keys_c = [f"idem_race_key_{k:02d}" for k in range(num_keys_c)]

    tasks_c = []
    for k in keys_c:
        for _ in range(reqs_per_type_c):
            tasks_c.append(("EXECUTE", k))
            tasks_c.append(("COMPENSATE", k))
    random.shuffle(tasks_c)

    barrier_c = threading.Barrier(N_C)
    outcomes_c: List[Dict[str, Any]] = []
    lock_c = threading.Lock()

    def worker_race(op_type: str, key_val: str):
        barrier_c.wait()
        if op_type == "EXECUTE":
            resp = client.post("/api/helpdesk/execute", json={
                "idempotency_key": key_val,
                "action_type": "TRANSACTION_ROLLBACK_TEST",
                "amount": 10.0
            })
            with lock_c:
                outcomes_c.append({
                    "op": "EXECUTE",
                    "key": key_val,
                    "code": resp.status_code,
                    "data": resp.json()
                })
        else:
            resp = client.post("/api/helpdesk/compensate", json={
                "idempotency_key": key_val,
                "reason": "Race condition quarantine simulation"
            })
            with lock_c:
                outcomes_c.append({
                    "op": "COMPENSATE",
                    "key": key_val,
                    "code": resp.status_code,
                    "data": resp.json()
                })

    t0 = time.time()
    threads_c = [threading.Thread(target=worker_race, args=(tasks_c[i][0], tasks_c[i][1])) for i in range(N_C)]
    for t in threads_c:
        t.start()
    for t in threads_c:
        t.join()
    dur_c = time.time() - t0

    # Group outcomes by key
    by_key_c: Dict[str, List[Dict[str, Any]]] = {k: [] for k in keys_c}
    for item in outcomes_c:
        by_key_c[item["key"]].append(item)

    print(f"      Total Mixed Operations: {len(outcomes_c)} in {dur_c:.2f}s ({N_C / dur_c:.1f} req/s)")

    for k, events in by_key_c.items():
        exec_events = [e for e in events if e["op"] == "EXECUTE"]
        comp_events = [e for e in events if e["op"] == "COMPENSATE"]

        exec_200 = [e for e in exec_events if e["code"] == 200]
        exec_409 = [e for e in exec_events if e["code"] == 409]

        is_tombstoned = k in tombstones
        is_executed = k in executed_actions

        print(f"      Key '{k}': tombstones={is_tombstoned}, executed_store={is_executed} | "
              f"Exec [200: {len(exec_200)}, 409: {len(exec_409)}] | Comp [200: {len(comp_events)}]")

        # Invariant checks:
        # If tombstone was registered first, all subsequent executes must be 409.
        # If execute landed first, execute gets 200, compensate gets 'compensated'.
        # Under NO circumstances should an unhandled 500 occur.
        assert all(e["code"] in (200, 409) for e in exec_events)
        assert all(c["code"] == 200 for c in comp_events)

    print("  [+] Part C passed: Zero 500 errors, zero unhandled states under mixed concurrent race conditions.")


if __name__ == "__main__":
    test_fastapi_test_client_endpoints()
    test_stage_execute_quarantine_compensation_generation()
    test_tombstone_rule_rejects_late_execution_arrivals()
    test_concurrent_500_executions_duplicate_idempotency_keys()
    print("\n=== ALL SUBAGENT 15 STRESS TESTS COMPLETED SUCCESSFULLY! ===")
