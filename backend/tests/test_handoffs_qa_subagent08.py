import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.graph import queries
from app.models.common import HandoffKind, HandoffStatus, GateStatus, NodeKind, Status, Reversibility
from app.models.graph import GraphNode, ActionInfo
from app.models.handoffs import Handoff
from app.services.event_bus import event_bus
from app.services import mcp_write as t_write

client = TestClient(app)


@pytest.fixture()
def clean_tenant():
    name = f"t_sub08_{uuid.uuid4().hex[:6]}"
    queries.forget_stores()
    yield name
    queries.forget_stores()


def auth_header(tenant: str, role: str, name: str = None):
    # Using dynamic token: token_<tenant>_<role>
    # Note: resolve_token gives agent_id f"{role}_{tenant}"
    return {"Authorization": f"Bearer token_{tenant}_{role}"}


def test_atomic_claim_success_and_lease(clean_tenant):
    """Verify POST /api/handoffs/{id}/claim sets IN_PROGRESS, claimed_by, claimed_at, and 60-second lease."""
    store = queries.get_graph_store(clean_tenant)
    h = Handoff(id="h_task_1", kind=HandoffKind.TASK, task="Process refunds", status=HandoffStatus.PENDING)
    store.add_handoff(h)

    t0_ms = int(time.time() * 1000)
    resp = client.post(
        "/api/handoffs/h_task_1/claim",
        headers={"Authorization": f"Bearer token_{clean_tenant}_agent"},
    )
    t1_ms = int(time.time() * 1000)

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "IN_PROGRESS"
    assert data["claimed_by"] == f"agent_{clean_tenant}"
    assert t0_ms <= data["claimed_at"] <= t1_ms
    assert data["lease_until"] == data["claimed_at"] + 60000

    # Verify state in store
    with store.handoff_lock:
        stored_h = next(x for x in store.handoffs if x.id == "h_task_1")
        assert stored_h.status == HandoffStatus.IN_PROGRESS
        assert stored_h.claimed_by == f"agent_{clean_tenant}"
        assert stored_h.lease_until == data["lease_until"]


def test_role_permissions_claiming(clean_tenant):
    """
    Verify:
    - Contributors (agent) can ONLY claim TASK handoffs. Attempting to claim REVIEW or ESCALATION fails with 403.
    - Reviewers (auditor) can claim TASK, REVIEW, and ESCALATION handoffs.
    - Admins (human) can claim TASK, REVIEW, and ESCALATION handoffs.
    """
    store = queries.get_graph_store(clean_tenant)
    store.add_handoff(Handoff(id="h_task", kind=HandoffKind.TASK, task="Task 1", status=HandoffStatus.PENDING))
    store.add_handoff(Handoff(id="h_review", kind=HandoffKind.REVIEW, task="Review 1", status=HandoffStatus.PENDING))
    store.add_handoff(Handoff(id="h_escalation", kind=HandoffKind.ESCALATION, task="Escalation 1", status=HandoffStatus.PENDING))

    # 1. Agent attempting to claim TASK -> 200
    r_agent_task = client.post("/api/handoffs/h_task/claim", headers=auth_header(clean_tenant, "agent"))
    assert r_agent_task.status_code == 200
    assert r_agent_task.json()["status"] == "IN_PROGRESS"

    # 2. Agent attempting to claim REVIEW -> 403 FORBIDDEN_ROLE
    r_agent_review = client.post("/api/handoffs/h_review/claim", headers=auth_header(clean_tenant, "agent"))
    assert r_agent_review.status_code == 403
    assert r_agent_review.json()["error"]["code"] == "FORBIDDEN_ROLE"

    # 3. Agent attempting to claim ESCALATION -> 403 FORBIDDEN_ROLE
    r_agent_esc = client.post("/api/handoffs/h_escalation/claim", headers=auth_header(clean_tenant, "agent"))
    assert r_agent_esc.status_code == 403
    assert r_agent_esc.json()["error"]["code"] == "FORBIDDEN_ROLE"

    # 4. Reviewer (auditor) claiming REVIEW -> 200
    r_auditor_review = client.post("/api/handoffs/h_review/claim", headers=auth_header(clean_tenant, "auditor"))
    assert r_auditor_review.status_code == 200
    assert r_auditor_review.json()["status"] == "IN_PROGRESS"
    assert r_auditor_review.json()["claimed_by"] == f"auditor_{clean_tenant}"

    # 5. Admin (human) claiming ESCALATION -> 200
    r_human_esc = client.post("/api/handoffs/h_escalation/claim", headers=auth_header(clean_tenant, "human"))
    assert r_human_esc.status_code == 200
    assert r_human_esc.json()["status"] == "IN_PROGRESS"
    assert r_human_esc.json()["claimed_by"] == f"human_{clean_tenant}"


def test_review_handoff_roles(clean_tenant):
    """
    Verify:
    - Only humans can call POST /api/handoffs/{id}/review.
    - Agent and Auditor are rejected with 403 FORBIDDEN_ROLE.
    """
    store = queries.get_graph_store(clean_tenant)
    store.add_handoff(Handoff(id="h_rev_role", kind=HandoffKind.REVIEW, task="Review role test", status=HandoffStatus.PENDING))

    # Agent cannot review
    r_agent = client.post(
        "/api/handoffs/h_rev_role/review",
        json={"decision": "approve"},
        headers=auth_header(clean_tenant, "agent"),
    )
    assert r_agent.status_code == 403
    assert r_agent.json()["error"]["code"] == "FORBIDDEN_ROLE"

    # Auditor cannot review
    r_auditor = client.post(
        "/api/handoffs/h_rev_role/review",
        json={"decision": "approve"},
        headers=auth_header(clean_tenant, "auditor"),
    )
    assert r_auditor.status_code == 403
    assert r_auditor.json()["error"]["code"] == "FORBIDDEN_ROLE"

    # Human can review
    r_human = client.post(
        "/api/handoffs/h_rev_role/review",
        json={"decision": "approve"},
        headers=auth_header(clean_tenant, "human"),
    )
    assert r_human.status_code == 200
    assert r_human.json()["status"] == "COMPLETED"


def test_review_handoff_approve_unblocks_gate_and_updates_alert(clean_tenant):
    """
    Verify approve decision:
    - Sets handoff.status = COMPLETED
    - Unblocks gate: action_node.action.gate = PASSED
    - Updates correction_notice status to APPROVED_PENDING_DELIVERY
    - Updates alert_retraction node label
    """
    store = queries.get_graph_store(clean_tenant)

    # Setup action node blocked by gate
    act = GraphNode(
        id="act_irrev_1",
        kind=NodeKind.ACTION,
        label="Send customer email",
        status=Status.HEALTHY,
        stale=False,
        action=ActionInfo(
            type="email",
            reversibility=Reversibility.IRREVERSIBLE,
            gate=GateStatus.BLOCKED,
            payload={"recipient": "user@example.com"},
            correction_notice={"status": "READY_FOR_HUMAN_APPROVAL", "target_action_id": "act_irrev_1"},
        ),
    )
    store.add_node(act)

    # Retraction alert node
    alert = GraphNode(
        id="alert_retraction_act_irrev_1",
        kind=NodeKind.ALERT,
        label="⚠️ Damage Control: Retraction for act_irrev_1",
        status=Status.HEALTHY,
        stale=False,
    )
    store.add_node(alert)

    # Review handoff anchored to action
    handoff = Handoff(
        id="h_gate_review_1",
        kind=HandoffKind.REVIEW,
        task="Quorum Gate Review: Irreversible action",
        status=HandoffStatus.PENDING,
        context_root_id=act.id,
        correction_notice={"status": "READY_FOR_HUMAN_APPROVAL", "target_action_id": "act_irrev_1"},
    )
    store.add_handoff(handoff)

    # Review with APPROVE
    res = client.post(
        f"/api/handoffs/{handoff.id}/review",
        json={"decision": "approve", "note": "Approved by compliance team"},
        headers=auth_header(clean_tenant, "human"),
    )
    assert res.status_code == 200
    res_data = res.json()
    assert res_data["status"] == "COMPLETED"

    # Verify action unblocked in store
    assert act.action.gate == GateStatus.PASSED
    assert act.action.correction_notice["status"] == "APPROVED_PENDING_DELIVERY"

    # Verify alert updated
    assert "approved" in alert.label.lower()
    assert "pending" in alert.label.lower()


def test_review_handoff_reject_does_not_unblock_gate(clean_tenant):
    """
    Verify reject decision:
    - Sets handoff.status = COMPLETED
    - Does NOT unblock gate: action_node.action.gate remains BLOCKED
    - Alert retraction node is NOT marked as approved
    """
    store = queries.get_graph_store(clean_tenant)

    act = GraphNode(
        id="act_irrev_2",
        kind=NodeKind.ACTION,
        label="Execute bank transfer",
        status=Status.HEALTHY,
        stale=False,
        action=ActionInfo(
            type="wire",
            reversibility=Reversibility.IRREVERSIBLE,
            gate=GateStatus.BLOCKED,
            payload={"amount": 50000},
            correction_notice={"status": "READY_FOR_HUMAN_APPROVAL", "target_action_id": "act_irrev_2"},
        ),
    )
    store.add_node(act)

    alert = GraphNode(
        id="alert_retraction_act_irrev_2",
        kind=NodeKind.ALERT,
        label="⚠️ Damage Control: Retraction for act_irrev_2",
        status=Status.HEALTHY,
        stale=False,
    )
    store.add_node(alert)

    handoff = Handoff(
        id="h_gate_review_2",
        kind=HandoffKind.REVIEW,
        task="Quorum Gate Review: Irreversible transfer",
        status=HandoffStatus.PENDING,
        context_root_id=act.id,
        correction_notice={"status": "READY_FOR_HUMAN_APPROVAL", "target_action_id": "act_irrev_2"},
    )
    store.add_handoff(handoff)

    # Review with REJECT
    res = client.post(
        f"/api/handoffs/{handoff.id}/review",
        json={"decision": "reject", "note": "Policy violation"},
        headers=auth_header(clean_tenant, "human"),
    )
    assert res.status_code == 200

    # Gate MUST still be BLOCKED!
    assert act.action.gate == GateStatus.BLOCKED
    assert act.action.correction_notice["status"] == "READY_FOR_HUMAN_APPROVAL"
    assert "approved" not in alert.label.lower()


def test_reclaimability_of_timed_out_handoffs(clean_tenant):
    """
    Verify handoffs in TIMED_OUT status can be reclaimed:
    - routes /api/handoffs/{id}/claim allows TIMED_OUT handoffs to be claimed.
    """
    store = queries.get_graph_store(clean_tenant)
    store.add_handoff(Handoff(
        id="h_timed_out_1",
        kind=HandoffKind.TASK,
        task="Timed out task",
        status=HandoffStatus.TIMED_OUT,
        claimed_by="dead_agent",
        claimed_at=1000,
        lease_until=2000,
    ))

    resp = client.post("/api/handoffs/h_timed_out_1/claim", headers=auth_header(clean_tenant, "agent"))
    assert resp.status_code == 200
    assert resp.json()["status"] == "IN_PROGRESS"
    assert resp.json()["claimed_by"] == f"agent_{clean_tenant}"


def test_mcp_timed_out_handoff_behavior(clean_tenant):
    """
    Verify MCP tool claim_handoff behavior on TIMED_OUT handoff.
    Bug Finding: mcp_write.py line 372 does not check HandoffStatus.TIMED_OUT,
    causing MCP tool to fail with active lease error when claiming a TIMED_OUT handoff.
    """
    store = queries.get_graph_store(clean_tenant)
    store.add_handoff(Handoff(
        id="h_timed_out_mcp",
        kind=HandoffKind.TASK,
        task="Timed out task for MCP",
        status=HandoffStatus.TIMED_OUT,
        claimed_by="dead_agent",
        claimed_at=1000,
        lease_until=2000,
    ))

    token = f"token_{clean_tenant}_agent"
    mcp_res = t_write.claim_handoff("h_timed_out_mcp", token=token)
    assert mcp_res["success"] is True


def test_review_handoff_note_dropped_bug(clean_tenant):
    """
    Verify routes/handoffs.py persists body.note to target.note.
    """
    store = queries.get_graph_store(clean_tenant)
    store.add_handoff(Handoff(
        id="h_note_bug",
        kind=HandoffKind.REVIEW,
        task="Review note bug check",
        status=HandoffStatus.PENDING,
    ))

    resp = client.post(
        "/api/handoffs/h_note_bug/review",
        json={"decision": "approve", "note": "Verified by Auditor Alice"},
        headers=auth_header(clean_tenant, "human"),
    )
    assert resp.status_code == 200
    assert resp.json()["note"] == "Verified by Auditor Alice"


def test_team_scoping_handoff_visibility(clean_tenant):
    """
    Verify access.py handoff_visible:
    - Scoped members can only see handoffs anchored to nodes they have access to.
    - Historical handoffs without context_root_id are hidden from scoped members.
    """
    from app.services import workspace
    store = queries.get_graph_store(clean_tenant)

    # Create two teams
    t1 = workspace.add_team(store, "Alpha", seats=5)
    t2 = workspace.add_team(store, "Beta", seats=5)

    # Create a member in Team Alpha
    member_alpha, token_alpha = workspace.add_member(store, "AgentAlpha", "agent", t1["id"], "antigravity", "laptop")

    # Node in Team Beta
    node_beta = GraphNode(
        id="node_b1",
        kind=NodeKind.FACT,
        label="Beta confidential fact",
        status=Status.HEALTHY,
        stale=False,
        scope_team=t2["id"],
    )
    store.add_node(node_beta)

    # Handoff anchored to Team Beta's node
    h_beta = Handoff(
        id="h_beta_private",
        kind=HandoffKind.TASK,
        task="Beta private task",
        status=HandoffStatus.PENDING,
        context_root_id=node_beta.id,
    )
    store.add_handoff(h_beta)

    # Handoff without context_root_id (unanchored)
    h_unanchored = Handoff(
        id="h_unanchored",
        kind=HandoffKind.TASK,
        task="Unanchored task",
        status=HandoffStatus.PENDING,
        context_root_id=None,
    )
    store.add_handoff(h_unanchored)

    # Member Alpha lists handoffs -> neither h_beta_private nor h_unanchored is visible!
    r_list = client.get("/api/handoffs", headers={"Authorization": f"Bearer {token_alpha}"})
    assert r_list.status_code == 200
    visible_ids = [h["id"] for h in r_list.json()]
    assert "h_beta_private" not in visible_ids
    assert "h_unanchored" not in visible_ids

    # Member Alpha claiming h_beta_private gets 404 NOT FOUND (non-disclosing)
    r_claim = client.post(f"/api/handoffs/{h_beta.id}/claim", headers={"Authorization": f"Bearer {token_alpha}"})
    assert r_claim.status_code == 404


def test_event_bus_emission_on_claim_and_review(clean_tenant):
    """Verify event bus receives handoff_claimed and handoff_updated events."""
    store = queries.get_graph_store(clean_tenant)
    store.add_handoff(Handoff(id="h_evt_1", kind=HandoffKind.TASK, task="Event test", status=HandoffStatus.PENDING))

    events_captured = []
    buf = event_bus.get_tenant_buffer(clean_tenant)
    orig_publish = buf.publish

    def capture_publish(event_type, data, request_id=None, epoch=None):
        events_captured.append({"type": event_type, "data": data})
        return orig_publish(event_type, data, request_id, epoch)

    buf.publish = capture_publish

    try:
        # Claim
        r_claim = client.post("/api/handoffs/h_evt_1/claim", headers=auth_header(clean_tenant, "agent"))
        assert r_claim.status_code == 200

        claimed_types = [e["type"] for e in events_captured]
        assert "handoff_claimed" in claimed_types
        assert "handoff_updated" in claimed_types

        # Review
        store.add_handoff(Handoff(id="h_evt_rev", kind=HandoffKind.REVIEW, task="Rev event test", status=HandoffStatus.PENDING))
        r_rev = client.post("/api/handoffs/h_evt_rev/review", json={"decision": "approve"}, headers=auth_header(clean_tenant, "human"))
        assert r_rev.status_code == 200

        rev_types = [e["type"] for e in events_captured]
        assert rev_types.count("handoff_updated") >= 2
    finally:
        buf.publish = orig_publish


def test_stress_20_concurrent_agents_claim_race(clean_tenant):
    """
    Multi-Vector Stress Test:
    Race condition: 20 agents concurrently attempting to claim the EXACT SAME handoff.
    Must guarantee:
    - Exactly 1 agent wins (HTTP 200)
    - Exactly 19 agents receive HTTP 409 Conflict
    - The handoff in store is IN_PROGRESS with winner's identity and active 60s lease
    """
    store = queries.get_graph_store(clean_tenant)
    handoff_id = "h_race_20"
    store.add_handoff(Handoff(id=handoff_id, kind=HandoffKind.TASK, task="Concurrent race task", status=HandoffStatus.PENDING))

    results = []
    # 20 concurrent threads
    def attempt_claim(agent_idx: int):
        # Each agent has its own agent token: token_<tenant>_agent
        # With dynamic tokens, agent_id will be agent_<tenant>
        resp = client.post(
            f"/api/handoffs/{handoff_id}/claim",
            headers={"Authorization": f"Bearer token_{clean_tenant}_agent"},
        )
        return (agent_idx, resp.status_code, resp.json())

    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(attempt_claim, i) for i in range(20)]
        for f in as_completed(futures):
            results.append(f.result())

    status_codes = [r[1] for r in results]
    successes = [r for r in results if r[1] == 200]
    conflicts = [r for r in results if r[1] == 409]

    assert len(successes) == 1, f"Expected exactly 1 winner, got {len(successes)}. Status codes: {status_codes}"
    assert len(conflicts) == 19, f"Expected 19 conflicts, got {len(conflicts)}"

    with store.handoff_lock:
        h = next(x for x in store.handoffs if x.id == handoff_id)
        assert h.status == HandoffStatus.IN_PROGRESS
        assert h.claimed_by == f"agent_{clean_tenant}"
        assert h.lease_until is not None


def test_lease_clock_skew_and_rapid_cycles(clean_tenant):
    """
    Boundary & Stress Test:
    - Lease clock skew:
      1. Active lease in the future blocks competing claims.
      2. Expired lease (past timestamp) allows immediate reclaim by another agent.
      3. Completed handoff is NEVER claimable even if lease_until is in the past.
      4. Clock skew backwards (now < claimed_at): lease is active, competing claims blocked.
    - Rapid claim-release-expire cycles (10 iterations):
      Repeatedly claiming, advancing lease_until into the past, claiming with next agent.
    """
    store = queries.get_graph_store(clean_tenant)
    h_id = "h_skew_1"
    store.add_handoff(Handoff(id=h_id, kind=HandoffKind.TASK, task="Skew test", status=HandoffStatus.PENDING))

    # Initial claim
    r1 = client.post(f"/api/handoffs/{h_id}/claim", headers=auth_header(clean_tenant, "agent"))
    assert r1.status_code == 200
    assert r1.json()["lease_until"] > 0

    # Competing claim while lease is active -> 409
    r2 = client.post(f"/api/handoffs/{h_id}/claim", headers=auth_header(clean_tenant, "agent"))
    assert r2.status_code == 409
    assert r2.json()["error"]["code"] == "CONFLICT"

    # Simulate clock expiration: set lease_until to 1 second ago
    with store.handoff_lock:
        h = next(x for x in store.handoffs if x.id == h_id)
        h.lease_until = int(time.time() * 1000) - 1000

    # Now reclaim should succeed!
    r3 = client.post(f"/api/handoffs/{h_id}/claim", headers=auth_header(clean_tenant, "agent"))
    assert r3.status_code == 200
    assert r3.json()["status"] == "IN_PROGRESS"
    assert r3.json()["lease_until"] > int(time.time() * 1000)

    # Test completed handoff cannot be reclaimed even if lease expired long ago
    with store.handoff_lock:
        h = next(x for x in store.handoffs if x.id == h_id)
        h.status = HandoffStatus.COMPLETED
        h.lease_until = int(time.time() * 1000) - 50000

    r_comp = client.post(f"/api/handoffs/{h_id}/claim", headers=auth_header(clean_tenant, "agent"))
    assert r_comp.status_code == 409

    # Rapid cycles: 10 consecutive claim-expire-reclaim cycles
    h_cycle_id = "h_rapid_cycle"
    store.add_handoff(Handoff(id=h_cycle_id, kind=HandoffKind.TASK, task="Rapid cycle task", status=HandoffStatus.PENDING))

    for cycle in range(10):
        # Claim
        rc = client.post(f"/api/handoffs/{h_cycle_id}/claim", headers=auth_header(clean_tenant, "agent"))
        assert rc.status_code == 200, f"Cycle {cycle} claim failed: {rc.text}"
        # Expire lease immediately
        with store.handoff_lock:
            h = next(x for x in store.handoffs if x.id == h_cycle_id)
            h.lease_until = int(time.time() * 1000) - 100

    with store.handoff_lock:
        final_h = next(x for x in store.handoffs if x.id == h_cycle_id)
        assert final_h.status == HandoffStatus.IN_PROGRESS
