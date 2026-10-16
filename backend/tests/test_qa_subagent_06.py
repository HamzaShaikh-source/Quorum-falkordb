import time
import uuid
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.graph import queries
from app.graph.queries import get_graph_store
from app.models.common import (
    NodeKind,
    Status,
    Reversibility,
    GateStatus,
    HandoffKind,
    HandoffStatus,
    EdgeType,
    ErrorCode,
)
from app.models.graph import GraphNode, ActionInfo, GraphEdge
from app.models.handoffs import Handoff
from app.services.executor import action_executor
from app.services.helpdesk import MockHelpdeskService, helpdesk_service
from app.services.auditor import auditor_service
from app.services.saga import generate_correction_notice, saga_worker

client = TestClient(app)


def auth_header(tenant: str, role: str = "human"):
    return {"Authorization": f"Bearer token_{tenant}_{role}"}


# ============================================================================
# 1. DOUBLE-EXECUTE RACE TEST: 50 Concurrent Requests on Same Action ID
# ============================================================================

def test_double_execute_race_50_concurrent_threads():
    """
    Multi-Vector Stress Test:
    50 concurrent threads simultaneously attempt to execute the exact same action ID.
    Requirements:
    - Exactly 1 thread succeeds (HTTP 200).
    - Exactly 49 threads are rejected with HTTP 409 (GUARD_REFUSED).
    - Helpdesk service executes the action exactly once.
    - Final state: action.action.executed == True, confirmed == True.
    """
    tenant = f"t_race_{uuid.uuid4().hex[:6]}"
    store = get_graph_store(tenant)

    # 1. Setup healthy parent fact
    fact_node = GraphNode(
        id=f"f_{uuid.uuid4().hex[:6]}",
        kind=NodeKind.FACT,
        label="Customer policy check",
        status=Status.HEALTHY,
        stale=False,
    )
    store.add_node(fact_node)

    # 2. Stage action
    action_id = f"act_{uuid.uuid4().hex[:6]}"
    action_node = GraphNode(
        id=action_id,
        kind=NodeKind.ACTION,
        label="Process refund for double-execute test",
        status=Status.HEALTHY,
        stale=False,
        action=ActionInfo(
            type="REFUND",
            reversibility=Reversibility.COMPENSABLE,
            executed=False,
            confirmed=False,
            gate=GateStatus.PASSED,
            payload={"amount": 50, "customer_id": "cust_race"},
        ),
    )
    store.add_node(action_node)
    store.add_edge(GraphEdge(
        id=f"e_{uuid.uuid4().hex[:6]}",
        source=fact_node.id,
        target=action_node.id,
        type=EdgeType.LEADS_TO,
    ))

    # Mock the helpdesk service call counter
    call_count = 0
    call_lock = threading.Lock()
    orig_execute_action = helpdesk_service.execute_action

    def mock_execute_action(*args, **kwargs):
        nonlocal call_count
        with call_lock:
            call_count += 1
        time.sleep(0.01)  # tiny delay to increase concurrency window
        return orig_execute_action(*args, **kwargs)

    barrier = threading.Barrier(50)
    results = []

    def execute_worker():
        barrier.wait()
        res = client.post(f"/api/actions/{action_id}/execute", headers=auth_header(tenant, "agent"))
        return res.status_code, res.json()

    with patch.object(helpdesk_service, "execute_action", side_effect=mock_execute_action):
        with ThreadPoolExecutor(max_workers=50) as pool:
            futures = [pool.submit(execute_worker) for _ in range(50)]
            results = [f.result() for f in futures]

    successes = [r for r in results if r[0] == 200]
    conflicts = [r for r in results if r[0] == 409]

    print(f"\n[Double-Execute Race] Total: {len(results)}, 200 OK: {len(successes)}, 409 Conflict: {len(conflicts)}")
    assert len(successes) == 1, f"Expected exactly 1 success, got {len(successes)}"
    assert len(conflicts) == 49, f"Expected exactly 49 conflicts, got {len(conflicts)}"
    assert call_count == 1, f"External service must be called exactly once, was called {call_count} times"

    # Verify error detail for conflicts
    for status_code, body in conflicts:
        assert body["error"]["code"] == ErrorCode.GUARD_REFUSED.value

    # Verify final node state
    node = store.nodes[action_id]
    assert node.action.executed is True
    assert node.action.confirmed is True


# ============================================================================
# 2. ANCESTOR QUARANTINED A MILLISECOND PRIOR: Race and Invariant Verification
# ============================================================================

def test_ancestor_quarantined_prior_blocks_execution():
    """
    Verify: Can an action be executed if an ancestor was quarantined a millisecond prior?
    Must fail with 409 GUARD_REFUSED, and external helpdesk API must NEVER be called.
    """
    tenant = f"t_quar_{uuid.uuid4().hex[:6]}"
    store = get_graph_store(tenant)

    fact_root = GraphNode(
        id=f"f_root_{uuid.uuid4().hex[:6]}",
        kind=NodeKind.FACT,
        label="Root policy fact",
        status=Status.HEALTHY,
        stale=False,
    )
    fact_child = GraphNode(
        id=f"f_child_{uuid.uuid4().hex[:6]}",
        kind=NodeKind.FACT,
        label="Derived fact",
        status=Status.HEALTHY,
        stale=False,
    )
    action_node = GraphNode(
        id=f"act_{uuid.uuid4().hex[:6]}",
        kind=NodeKind.ACTION,
        label="Quarantine guard test action",
        status=Status.HEALTHY,
        stale=False,
        action=ActionInfo(
            type="REFUND",
            reversibility=Reversibility.COMPENSABLE,
            executed=False,
            confirmed=False,
            gate=GateStatus.PASSED,
        ),
    )
    store.add_node(fact_root)
    store.add_node(fact_child)
    store.add_node(action_node)

    store.add_edge(GraphEdge(id="e1", source=fact_root.id, target=fact_child.id, type=EdgeType.LEADS_TO))
    store.add_edge(GraphEdge(id="e2", source=fact_child.id, target=action_node.id, type=EdgeType.LEADS_TO))

    # Quarantine root fact
    auditor_service.quarantine(store, fact_root.id, reason="Policy revoked", tenant=tenant)

    # Immediately attempt to execute action
    res = client.post(f"/api/actions/{action_node.id}/execute", headers=auth_header(tenant, "agent"))
    assert res.status_code == 409
    assert res.json()["error"]["code"] == ErrorCode.GUARD_REFUSED.value

    # Verify action remains unexecuted
    assert action_node.action.executed is False
    assert action_node.action.confirmed is False


def test_concurrent_quarantine_vs_execution_race():
    """
    Race test: Concurrently execute actions while an ancestor is quarantined.
    Zero actions with quarantined ancestors should ever be confirmed.
    """
    tenant = f"t_race_quar_{uuid.uuid4().hex[:6]}"
    store = get_graph_store(tenant)

    fact_root = GraphNode(
        id=f"f_root_{uuid.uuid4().hex[:6]}",
        kind=NodeKind.FACT,
        label="Root policy fact for race",
        status=Status.HEALTHY,
        stale=False,
    )
    store.add_node(fact_root)

    action_ids = []
    for i in range(20):
        aid = f"act_race_{i}_{uuid.uuid4().hex[:4]}"
        anode = GraphNode(
            id=aid,
            kind=NodeKind.ACTION,
            label=f"Action {i}",
            status=Status.HEALTHY,
            stale=False,
            action=ActionInfo(
                type="REFUND",
                reversibility=Reversibility.COMPENSABLE,
                executed=False,
                confirmed=False,
                gate=GateStatus.PASSED,
                payload={"index": i},
            ),
        )
        store.add_node(anode)
        store.add_edge(GraphEdge(id=f"e_{i}", source=fact_root.id, target=aid, type=EdgeType.LEADS_TO))
        action_ids.append(aid)

    barrier = threading.Barrier(21)
    exec_results = {}

    def exec_worker(aid):
        barrier.wait()
        res = client.post(f"/api/actions/{aid}/execute", headers=auth_header(tenant, "agent"))
        exec_results[aid] = res.status_code

    def quar_worker():
        barrier.wait()
        time.sleep(0.001)
        auditor_service.quarantine(store, fact_root.id, reason="Emergency revocation", tenant=tenant)

    threads = [threading.Thread(target=exec_worker, args=(aid,)) for aid in action_ids]
    quar_thread = threading.Thread(target=quar_worker)

    for t in threads:
        t.start()
    quar_thread.start()

    for t in threads:
        t.join()
    quar_thread.join()

    # Invariant checks:
    # 1. Any action that executed before quarantine must be QUARANTINED now and have compensation enqueued.
    # 2. Any action that was not executed before quarantine must be refused (409) and not executed.
    for aid in action_ids:
        anode = store.nodes[aid]
        assert anode.status == Status.QUARANTINED
        if exec_results[aid] == 200:
            assert anode.action.executed is True
            assert anode.action.compensation is not None
        else:
            assert exec_results[aid] == 409
            assert anode.action.executed is False


# ============================================================================
# 3. FAILURE INJECTION: Helpdesk 500 / Timeout / Network Error Rollback Verification
# ============================================================================

def test_failure_injection_helpdesk_http_500():
    """
    Failure Injection: Helpdesk returns HTTP 500.
    Verify:
    - Execute endpoint returns HTTP 500.
    - Action executed remains True (committed pre-dispatch), but confirmed remains False.
    - No ghost confirmation.
    - Reconciler / saga handles it via durable key.
    """
    tenant = f"t_fail500_{uuid.uuid4().hex[:6]}"
    store = get_graph_store(tenant)

    fact = GraphNode(id="f_ok", kind=NodeKind.FACT, label="Fact", status=Status.HEALTHY, stale=False)
    act = GraphNode(
        id="act_fail500",
        kind=NodeKind.ACTION,
        label="Action Fail 500",
        status=Status.HEALTHY,
        stale=False,
        action=ActionInfo(type="REFUND", reversibility=Reversibility.COMPENSABLE, gate=GateStatus.PASSED),
    )
    store.add_node(fact)
    store.add_node(act)
    store.add_edge(GraphEdge(id="e_f", source="f_ok", target="act_fail500", type=EdgeType.LEADS_TO))

    with patch.object(helpdesk_service, "execute_action", return_value={"status": 500, "error": "Internal Server Error"}):
        res = client.post("/api/actions/act_fail500/execute", headers=auth_header(tenant, "agent"))
        assert res.status_code == 500
        assert res.json()["error"]["code"] == ErrorCode.INTERNAL.value

    # Verify action node state
    node = store.nodes["act_fail500"]
    assert node.action.executed is True
    assert node.action.confirmed is False


def test_failure_injection_helpdesk_timeout_network_error():
    """
    Failure Injection: Helpdesk connection times out or throws exception.
    Verify:
    - Execute endpoint catches exception, sets delivery_error, and raises HTTP 502 Bad Gateway.
    - executed remains True (execution intent durable), confirmed remains False.
    """
    tenant = f"t_timeout_{uuid.uuid4().hex[:6]}"
    store = get_graph_store(tenant)

    fact = GraphNode(id="f_ok_2", kind=NodeKind.FACT, label="Fact", status=Status.HEALTHY, stale=False)
    act = GraphNode(
        id="act_timeout",
        kind=NodeKind.ACTION,
        label="Action Timeout",
        status=Status.HEALTHY,
        stale=False,
        action=ActionInfo(type="REFUND", reversibility=Reversibility.COMPENSABLE, gate=GateStatus.PASSED),
    )
    store.add_node(fact)
    store.add_node(act)
    store.add_edge(GraphEdge(id="e_f2", source="f_ok_2", target="act_timeout", type=EdgeType.LEADS_TO))

    with patch.object(helpdesk_service, "execute_action", side_effect=TimeoutError("Helpdesk timed out")):
        res = client.post("/api/actions/act_timeout/execute", headers=auth_header(tenant, "agent"))
        assert res.status_code == 502

    node = store.nodes["act_timeout"]
    assert node.action.executed is True
    assert node.action.confirmed is False
    assert node.action.delivery_error == "TimeoutError"


# ============================================================================
# 4. MOCK HELPDESK SERVICE: Idempotency, Tombstones, and State
# ============================================================================

def test_mock_helpdesk_service_unit():
    """
    Unit test for MockHelpdeskService in app/services/helpdesk.py:
    - Idempotency key replay returns 200 with replayed=True.
    - Tombstone on unseen key returns 200 with result='tombstoned'.
    - Execute with tombstoned key returns 409 Conflict.
    """
    service = MockHelpdeskService()

    # 1. First execution
    res1 = service.execute_action("key_123", "REFUND", {"amount": 100})
    assert res1["status"] == 200
    assert res1["replayed"] is False
    assert res1["data"]["payload"] == {"amount": 100}

    # 2. Replay with identical key
    res2 = service.execute_action("key_123", "REFUND", {"amount": 999})
    assert res2["status"] == 200
    assert res2["replayed"] is True
    assert res2["data"]["payload"] == {"amount": 100}  # preserved original

    # 3. Compensate on unseen key -> tombstone!
    res_comp = service.compensate_action("key_unseen", "act_x", "REFUND")
    assert res_comp["status"] == 200
    assert res_comp["result"] == "tombstoned"
    assert "key_unseen" in service.tombstones

    # 4. Late execution on tombstoned key -> 409 Conflict!
    res_late = service.execute_action("key_unseen", "REFUND", {})
    assert res_late["status"] == 409
    assert res_late["error"] == "CONFLICT"


# ============================================================================
# 5. APPROVE_CORRECTION ENDPOINT: Role Checks, Lock Coverage, Retraction Notices
# ============================================================================

def test_approve_correction_role_checks_and_execution():
    """
    Check approve_correction endpoint:
    - Role 'agent' -> 403 Forbidden
    - Role 'auditor' -> 403 Forbidden
    - Role 'human' -> 200 OK with APPROVED_PENDING_DELIVERY
    """
    tenant = f"t_appr_{uuid.uuid4().hex[:6]}"
    store = get_graph_store(tenant)

    act = GraphNode(
        id="act_email_test",
        kind=NodeKind.ACTION,
        label="Send misleading email",
        status=Status.QUARANTINED,
        stale=False,
        action=ActionInfo(
            type="SEND_EMAIL",
            reversibility=Reversibility.IRREVERSIBLE,
            executed=True,
            confirmed=True,
            gate=GateStatus.PASSED,
            payload={"recipient": "user@example.com", "subject": "Notice"},
        ),
    )
    store.add_node(act)

    # 1. Non-human roles rejected
    res_agent = client.post("/api/actions/act_email_test/approve-correction", headers=auth_header(tenant, "agent"))
    assert res_agent.status_code == 403

    res_auditor = client.post("/api/actions/act_email_test/approve-correction", headers=auth_header(tenant, "auditor"))
    assert res_auditor.status_code == 403

    # 2. Human role accepted
    res_human = client.post("/api/actions/act_email_test/approve-correction", headers=auth_header(tenant, "human"))
    assert res_human.status_code == 200
    data = res_human.json()
    assert data["status"] == "APPROVED_PENDING_DELIVERY"
    assert data["notice"]["status"] == "APPROVED_PENDING_DELIVERY"
    assert data["notice"]["recipient"] == "user@example.com"


def test_approve_correction_completes_unrelated_handoffs_finding():
    """
    Finding Verification:
    In backend/app/routes/actions.py lines 148-154:
    The loop checks `if h.context_root_id == action_id:` and marks ANY handoff
    matching context_root_id as COMPLETED, even if it is a REVIEW handoff or an
    unrelated ESCALATION handoff.
    """
    tenant = f"t_appr_bug_{uuid.uuid4().hex[:6]}"
    store = get_graph_store(tenant)

    act = GraphNode(
        id="act_handoff_bug",
        kind=NodeKind.ACTION,
        label="Action with multiple handoffs",
        status=Status.QUARANTINED,
        action=ActionInfo(
            type="SEND_EMAIL",
            reversibility=Reversibility.IRREVERSIBLE,
            executed=True,
            gate=GateStatus.PASSED,
        ),
    )
    store.add_node(act)

    # Add a REVIEW handoff (not an escalation handoff for correction)
    rev_handoff = Handoff(
        id=f"hand_rev_{uuid.uuid4().hex[:6]}",
        kind=HandoffKind.REVIEW,
        task="Gate review for policy",
        status=HandoffStatus.PENDING,
        context_root_id=act.id,
    )
    store.add_handoff(rev_handoff)

    # Call approve-correction
    res = client.post(f"/api/actions/{act.id}/approve-correction", headers=auth_header(tenant, "human"))
    assert res.status_code == 200

    # REVIEW handoff must NOT be marked COMPLETED by approve-correction
    assert rev_handoff.status == HandoffStatus.PENDING


def test_mock_helpdesk_main_drops_payload_fields():
    """
    Finding Verification for mock_helpdesk/main.py:
    Lines 51-56:
    ExecuteRequest accepts amount, customer_id, details,
    but they are omitted from the stored record dict.
    """
    from mock_helpdesk.main import app as helpdesk_app, executed_actions, tombstones
    from fastapi.testclient import TestClient as HDTestClient

    hd_client = HDTestClient(helpdesk_app)
    tombstones.clear()
    executed_actions.clear()

    req_data = {
        "idempotency_key": "key_mock_hd_test",
        "action_type": "REFUND",
        "amount": 250.0,
        "customer_id": "cust_999",
        "details": {"reason": "defective_item"},
    }
    res = hd_client.post("/api/helpdesk/execute", json=req_data)
    assert res.status_code == 200
    rec = res.json()["record"]

    # Verify that amount, customer_id, and details are NOT in the returned record
    assert "amount" not in rec
    assert "customer_id" not in rec
    assert "details" not in rec

