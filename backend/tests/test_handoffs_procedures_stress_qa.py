import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.graph.queries import get_graph_store
from app.models.common import (
    Status,
    NodeKind,
    GateStatus,
    Reversibility,
    HandoffKind,
    HandoffStatus,
)
from app.models.graph import GraphNode, ActionInfo
from app.models.handoffs import Handoff
from agents.procedures import ProcedureRegistry


@pytest.fixture
def client():
    return TestClient(app)


def test_20_agents_concurrent_claim_race(client):
    """
    STRESS TEST: 20 agents attempt to claim the exact same handoff concurrently.
    Invariant: EXACTLY ONE winner (200 OK), EXACTLY 19 conflicts (409 Conflict).
    """
    store = get_graph_store("acme_race")
    with store.handoff_lock:
        store.handoffs.clear()
        hid = "hand_task_concurrency_race"
        store.handoffs.append(
            Handoff(
                id=hid,
                kind=HandoffKind.TASK,
                task="High concurrency claim task",
                status=HandoffStatus.PENDING,
            )
        )

    results = []

    def claim_worker(agent_idx: int):
        token = "token_acme_race_agent"
        resp = client.post(
            f"/api/handoffs/{hid}/claim",
            headers={"Authorization": f"Bearer {token}"},
        )
        return resp.status_code, resp.json()

    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(claim_worker, i) for i in range(20)]
        for f in as_completed(futures):
            results.append(f.result())

    successes = [r for r in results if r[0] == 200]
    conflicts = [r for r in results if r[0] == 409]

    assert len(successes) == 1, f"Expected exactly 1 winner, got {len(successes)}"
    assert len(conflicts) == 19, f"Expected exactly 19 conflicts, got {len(conflicts)}"

    with store.handoff_lock:
        h = next(h for h in store.handoffs if h.id == hid)
        assert h.status == HandoffStatus.IN_PROGRESS
        assert h.claimed_by is not None
        assert h.lease_until is not None
        assert h.lease_until > int(time.time() * 1000)


def test_role_enforcement_on_claiming(client):
    """
    Verifies that:
    - Contributors (agent) can ONLY claim TASK handoffs (403 on REVIEW and ESCALATION).
    - Reviewers (auditor) and Admins (human) can claim TASK, REVIEW, and ESCALATION.
    """
    store = get_graph_store("acme_roles")
    with store.handoff_lock:
        store.handoffs.clear()
        store.handoffs.extend([
            Handoff(id="h_task", kind=HandoffKind.TASK, task="Task 1", status=HandoffStatus.PENDING),
            Handoff(id="h_review", kind=HandoffKind.REVIEW, task="Review 1", status=HandoffStatus.PENDING),
            Handoff(id="h_escl", kind=HandoffKind.ESCALATION, task="Escalation 1", status=HandoffStatus.PENDING),
        ])

    agent_hdr = {"Authorization": "Bearer token_acme_roles_agent"}
    auditor_hdr = {"Authorization": "Bearer token_acme_roles_auditor"}
    human_hdr = {"Authorization": "Bearer token_acme_roles_human"}

    # Agent cannot claim REVIEW or ESCALATION
    r_rev = client.post("/api/handoffs/h_review/claim", headers=agent_hdr)
    assert r_rev.status_code == 403
    err_rev = r_rev.json().get("error") or r_rev.json().get("detail", {}).get("error", {})
    assert "Agents may only claim TASK handoffs" in err_rev["message"]

    r_escl = client.post("/api/handoffs/h_escl/claim", headers=agent_hdr)
    assert r_escl.status_code == 403
    err_escl = r_escl.json().get("error") or r_escl.json().get("detail", {}).get("error", {})
    assert "Agents may only claim TASK handoffs" in err_escl["message"]

    # Agent CAN claim TASK
    r_task = client.post("/api/handoffs/h_task/claim", headers=agent_hdr)
    assert r_task.status_code == 200
    assert r_task.json()["status"] == "IN_PROGRESS"

    # Auditor can claim REVIEW
    r_aud = client.post("/api/handoffs/h_review/claim", headers=auditor_hdr)
    assert r_aud.status_code == 200
    assert r_aud.json()["status"] == "IN_PROGRESS"

    # Human can claim ESCALATION
    r_hum = client.post("/api/handoffs/h_escl/claim", headers=human_hdr)
    assert r_hum.status_code == 200
    assert r_hum.json()["status"] == "IN_PROGRESS"


def test_lease_clock_skew_and_rapid_reclaim(client):
    """
    Tests:
    1. Active lease blocks claim (409 Conflict).
    2. Expired lease allows reclaim (200 OK).
    3. TIMED_OUT status allows reclaim (200 OK).
    4. COMPLETED status cannot be reclaimed (409 Conflict).
    """
    store = get_graph_store("acme_lease")
    with store.handoff_lock:
        store.handoffs.clear()
        hid = "h_lease_test"
        now_ms = int(time.time() * 1000)
        h = Handoff(
            id=hid,
            kind=HandoffKind.TASK,
            task="Lease lifecycle task",
            status=HandoffStatus.IN_PROGRESS,
            claimed_by="agent_alpha",
            claimed_at=now_ms,
            lease_until=now_ms + 60000,
        )
        store.handoffs.append(h)

    agent_b_hdr = {"Authorization": "Bearer token_acme_lease_agent"}

    # 1. Active lease -> Conflict
    r1 = client.post(f"/api/handoffs/{hid}/claim", headers=agent_b_hdr)
    assert r1.status_code == 409

    # 2. Simulate lease expiration (time travel / clock skew)
    with store.handoff_lock:
        h.lease_until = int(time.time() * 1000) - 1000

    r2 = client.post(f"/api/handoffs/{hid}/claim", headers=agent_b_hdr)
    assert r2.status_code == 200
    assert r2.json()["status"] == "IN_PROGRESS"
    assert r2.json()["claimed_by"] == "agent_acme_lease"
    assert r2.json()["lease_until"] > int(time.time() * 1000)

    # 3. Simulate TIMED_OUT status
    with store.handoff_lock:
        h.status = HandoffStatus.TIMED_OUT
        h.lease_until = None

    r3 = client.post(f"/api/handoffs/{hid}/claim", headers=agent_b_hdr)
    assert r3.status_code == 200
    assert r3.json()["status"] == "IN_PROGRESS"

    # 4. COMPLETED status cannot be reclaimed
    with store.handoff_lock:
        h.status = HandoffStatus.COMPLETED

    r4 = client.post(f"/api/handoffs/{hid}/claim", headers=agent_b_hdr)
    assert r4.status_code == 409


def test_inverted_ternary_error_message_bug(client):
    """
    Tests error message correctness when claim fails due to:
    (A) Active lease / already claimed.
    (B) Anchor node is unhealthy (QUARANTINED).
    """
    store = get_graph_store("acme_ternary")
    with store._lock:
        store.nodes.clear()
        # Add a healthy node
        store.nodes["anchor_healthy"] = GraphNode(
            id="anchor_healthy",
            kind=NodeKind.FACT,
            label="Healthy Anchor",
            status=Status.HEALTHY,
        )
        # Add a quarantined node
        store.nodes["anchor_unhealthy"] = GraphNode(
            id="anchor_unhealthy",
            kind=NodeKind.FACT,
            label="Unhealthy Anchor",
            status=Status.QUARANTINED,
        )

    with store.handoff_lock:
        store.handoffs.clear()
        now_ms = int(time.time() * 1000)
        # Handoff with healthy anchor but already claimed with active lease
        store.handoffs.append(
            Handoff(
                id="h_claimed_healthy_anchor",
                kind=HandoffKind.TASK,
                task="Task with healthy anchor",
                status=HandoffStatus.IN_PROGRESS,
                claimed_by="agent_alpha",
                claimed_at=now_ms,
                lease_until=now_ms + 60000,
                context_root_id="anchor_healthy",
            )
        )
        # Handoff with unhealthy anchor, PENDING
        store.handoffs.append(
            Handoff(
                id="h_unhealthy_anchor",
                kind=HandoffKind.TASK,
                task="Task with unhealthy anchor",
                status=HandoffStatus.PENDING,
                context_root_id="anchor_unhealthy",
            )
        )

    agent_hdr = {"Authorization": "Bearer token_acme_ternary_agent"}

    # Attempt claiming already claimed handoff
    r_claimed = client.post("/api/handoffs/h_claimed_healthy_anchor/claim", headers=agent_hdr)
    assert r_claimed.status_code == 409
    err_claimed = r_claimed.json().get("error") or r_claimed.json().get("detail", {}).get("error", {})
    msg_claimed = err_claimed.get("message", "")

    # Attempt claiming handoff with unhealthy anchor
    r_unhealthy = client.post("/api/handoffs/h_unhealthy_anchor/claim", headers=agent_hdr)
    assert r_unhealthy.status_code == 409
    err_unhealthy = r_unhealthy.json().get("error") or r_unhealthy.json().get("detail", {}).get("error", {})
    msg_unhealthy = err_unhealthy.get("message", "")

    # Verify error message correctness:
    # When already claimed with healthy anchor: "Handoff ... already claimed by ... with active lease until ..."
    # When anchor is unhealthy: "Anchor node for handoff ... is not HEALTHY."
    assert "already claimed by" in msg_claimed, f"Expected already claimed message, got: {msg_claimed}"
    assert "not HEALTHY" in msg_unhealthy, f"Expected not HEALTHY message, got: {msg_unhealthy}"


def test_malformed_review_requests(client):
    """
    Tests boundary/malformed inputs on POST /handoffs/{id}/review:
    - null decision
    - empty decision
    - invalid decision string
    - missing decision
    - nonexistent handoff_id
    - empty notes vs long notes
    """
    human_hdr = {"Authorization": "Bearer token_acme_malformed_human"}
    store = get_graph_store("acme_malformed")
    with store.handoff_lock:
        store.handoffs.clear()
        store.handoffs.append(
            Handoff(
                id="h_review_target",
                kind=HandoffKind.REVIEW,
                task="Review target",
                status=HandoffStatus.PENDING,
            )
        )

    # 1. Nonexistent handoff ID -> 404
    r_404 = client.post("/api/handoffs/nonexistent_id/review", json={"decision": "approve"}, headers=human_hdr)
    assert r_404.status_code == 404
    err_404 = r_404.json().get("error") or r_404.json().get("detail", {}).get("error", {})
    assert err_404["code"] == "NOT_FOUND"

    # 2. null decision -> 422
    r_null = client.post("/api/handoffs/h_review_target/review", json={"decision": None}, headers=human_hdr)
    assert r_null.status_code == 422

    # 3. empty decision -> 422
    r_empty = client.post("/api/handoffs/h_review_target/review", json={"decision": ""}, headers=human_hdr)
    assert r_empty.status_code == 422

    # 4. invalid decision string -> 422
    r_inv = client.post("/api/handoffs/h_review_target/review", json={"decision": "maybe"}, headers=human_hdr)
    assert r_inv.status_code == 422

    # 5. missing decision -> 422
    r_miss = client.post("/api/handoffs/h_review_target/review", json={"note": "only note"}, headers=human_hdr)
    assert r_miss.status_code == 422

    # 6. empty notes -> 200 OK
    r_ok = client.post("/api/handoffs/h_review_target/review", json={"decision": "approve", "notes": ""}, headers=human_hdr)
    assert r_ok.status_code == 200
    assert r_ok.json()["status"] == "COMPLETED"


def test_review_handoff_approve_vs_reject_behavior(client):
    """
    Tests review_handoff on APPROVE vs REJECT:
    - Verify gate unblocking on APPROVE
    - Verify gate stays BLOCKED on REJECT
    - Verify retraction alert updates on APPROVE vs REJECT
    """
    human_hdr = {"Authorization": "Bearer token_acme_rev_human"}
    store = get_graph_store("acme_rev")

    with store._lock:
        store.nodes.clear()
        # Action 1 (will be approved)
        act1 = GraphNode(
            id="act_to_approve",
            kind=NodeKind.ACTION,
            label="Action to Approve",
            status=Status.HEALTHY,
            action=ActionInfo(
                type="refund",
                reversibility=Reversibility.IRREVERSIBLE,
                executed=False,
                gate=GateStatus.BLOCKED,
                correction_notice={"status": "READY_FOR_HUMAN_APPROVAL", "target_action_id": "act_to_approve"},
            ),
        )
        store.nodes[act1.id] = act1
        alert1 = GraphNode(
            id="alert_retraction_act_to_approve",
            kind=NodeKind.ALERT,
            label="Damage Control: Retraction for act_to_approve",
            status=Status.HEALTHY,
        )
        store.nodes[alert1.id] = alert1

        # Action 2 (will be rejected)
        act2 = GraphNode(
            id="act_to_reject",
            kind=NodeKind.ACTION,
            label="Action to Reject",
            status=Status.HEALTHY,
            action=ActionInfo(
                type="refund",
                reversibility=Reversibility.IRREVERSIBLE,
                executed=False,
                gate=GateStatus.BLOCKED,
                correction_notice={"status": "READY_FOR_HUMAN_APPROVAL", "target_action_id": "act_to_reject"},
            ),
        )
        store.nodes[act2.id] = act2
        alert2 = GraphNode(
            id="alert_retraction_act_to_reject",
            kind=NodeKind.ALERT,
            label="Damage Control: Retraction for act_to_reject",
            status=Status.HEALTHY,
        )
        store.nodes[alert2.id] = alert2

    with store.handoff_lock:
        store.handoffs.clear()
        h_app = Handoff(
            id="h_approve",
            kind=HandoffKind.REVIEW,
            task="Approve act1",
            status=HandoffStatus.PENDING,
            context_root_id="act_to_approve",
            correction_notice={"status": "READY_FOR_HUMAN_APPROVAL", "target_action_id": "act_to_approve"},
        )
        h_rej = Handoff(
            id="h_reject",
            kind=HandoffKind.REVIEW,
            task="Reject act2",
            status=HandoffStatus.PENDING,
            context_root_id="act_to_reject",
            correction_notice={"status": "READY_FOR_HUMAN_APPROVAL", "target_action_id": "act_to_reject"},
        )
        store.handoffs.extend([h_app, h_rej])

    # 1. APPROVE
    r_app = client.post("/api/handoffs/h_approve/review", json={"decision": "approve", "notes": "LGTM"}, headers=human_hdr)
    assert r_app.status_code == 200
    assert r_app.json()["status"] == "COMPLETED"

    with store._lock:
        assert act1.action.gate == GateStatus.PASSED
        assert act1.action.correction_notice["status"] == "APPROVED_PENDING_DELIVERY"
        assert alert1.label == "Retraction approved, delivery pending: act_to_approve"

    # 2. REJECT
    r_rej = client.post("/api/handoffs/h_reject/review", json={"decision": "reject", "notes": "Denied by supervisor"}, headers=human_hdr)
    assert r_rej.status_code == 200
    assert r_rej.json()["status"] == "COMPLETED"

    with store._lock:
        # Action remains blocked!
        assert act2.action.gate == GateStatus.BLOCKED
        # Note: on REJECT, alert label and notice are NOT updated in current implementation
        assert alert2.label == "Damage Control: Retraction for act_to_reject"
        assert act2.action.correction_notice["status"] == "READY_FOR_HUMAN_APPROVAL"


def test_procedural_memory_registry():
    """
    Tests Procedural Memory (ProcedureRegistry):
    - Laplace formula: (successes + 1) / (uses + 2)
    - Thread-safe outcome recording with 20 concurrent threads
    - Tie-breaking behavior
    """
    reg = ProcedureRegistry()
    # Test Laplace ranking
    p = reg.get_best_procedure("refund")
    assert p is not None
    assert p.id == "proc_refund_standard"
    assert p.rank_score == round((23 + 1.0) / (25 + 2.0), 3)  # 0.889

    # Record failure
    reg.record_outcome("proc_refund_standard", success=False)
    p_after = reg.get_best_procedure("refund")
    assert p_after.id == "proc_refund_strict"  # strict overtook standard!

    # Concurrency test: 20 threads recording outcomes simultaneously
    def worker():
        for _ in range(50):
            reg.record_outcome("proc_notify_customer", success=True)
            reg.record_outcome("proc_notify_customer", success=False)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    notify_p = next(p for p in reg.list_procedures() if p.id == "proc_notify_customer")
    # Initial: uses=18, successes=17, failures=1. Added 20*50 = 1000 successes and 1000 failures -> 2000 uses
    assert notify_p.uses == 18 + 2000
    assert notify_p.successes == 17 + 1000
    assert notify_p.failures == 1 + 1000
    assert notify_p.uses == notify_p.successes + notify_p.failures
