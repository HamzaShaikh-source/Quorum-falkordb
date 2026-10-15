import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.graph.queries import get_graph_store
from app.models.common import (
    Status,
    NodeKind,
    Reversibility,
    GateStatus,
    HandoffKind,
    HandoffStatus,
)
from app.models.graph import GraphNode, ActionInfo
from app.services.auditor import auditor_service
from app.services.saga import generate_correction_notice
from handoff_compat import _handoff_store, _handoff_lock


@pytest.fixture
def client():
    return TestClient(app)


def test_quarantine_executed_irreversible_action_creates_correction_notice(client):
    """
    Asserts that when an executed IRREVERSIBLE action is hit by the quarantine cascade,
    Quorum automatically generates a structured CorrectionNotice payload attached to an ESCALATION handoff,
    stores an :Alert memory node in the graph, and exposes the approve-correction endpoint.
    """
    human_headers = {"Authorization": "Bearer token_human"}

    store = get_graph_store("acme")
    with store._lock:
        store.nodes.clear()
        store.edges.clear()
        store.epoch = 1

    with _handoff_lock:
        _handoff_store.clear()

    # 1. Seed root fact (Finance Ops Policy Doc v3.0 / 30-day window)
    fact_root = GraphNode(
        id="fact_30d",
        kind=NodeKind.FACT,
        label="Refund window is 30 days",
        key="refund_window_days",
        value="30",
        text="Official policy allows refunds up to 30 days.",
        status=Status.HEALTHY,
        stale=False,
        source_ref="Finance Ops Policy Doc v3.0",
    )
    store.add_node(fact_root)

    # 2. Poisoned fact
    fact_poison = GraphNode(
        id="fact_90d",
        kind=NodeKind.FACT,
        label="Refund window is 90 days",
        key="refund_window_days",
        value="90",
        text="Partner unverified feed claims 90 days.",
        status=Status.HEALTHY,
        stale=False,
    )
    store.add_node(fact_poison)

    # 3. Executed IRREVERSIBLE action (SEND_CUSTOMER_EMAIL)
    email_act = GraphNode(
        id="act_email_001",
        kind=NodeKind.ACTION,
        label="Send refund confirmation email to customer",
        text="Your refund of $150 has been approved under our new 90-day policy.",
        status=Status.HEALTHY,
        stale=False,
        action=ActionInfo(
            type="SEND_CUSTOMER_EMAIL",
            reversibility=Reversibility.IRREVERSIBLE,
            executed=True,
            confirmed=True,
            gate=GateStatus.PASSED,
        ),
    )
    store.add_node(email_act)

    from app.models.graph import GraphEdge
    from app.models.common import EdgeType
    store.add_edge(GraphEdge(id="e_poison_email", source="fact_90d", target="act_email_001", type=EdgeType.LEADS_TO))

    # 4. Auditor triggers quarantine cascade on fact_90d
    affected = auditor_service.quarantine(store, "fact_90d", reason="Contradicts verified root policy", tenant="acme")

    # Verify action node is quarantined (turned red)
    assert email_act.status == Status.QUARANTINED
    assert any(a["id"] == "act_email_001" for a in affected)

    # Verify structured CorrectionNotice payload on action node
    notice = email_act.action.correction_notice
    assert notice is not None
    assert notice["target_action_id"] == "act_email_001"
    assert notice["recipient"] is None
    assert "recipient" in notice["missing_information"]
    assert notice["original_subject"] is None
    assert "90-day policy" in notice["original_statement"]
    assert notice["correction_subject"] == f"Correction: {email_act.label}"
    assert "Human input required" in notice["drafted_body"]
    assert "Finance Ops Policy Doc v3.0" not in notice["verified_policy_citation"]
    assert notice["status"] == "NEEDS_HUMAN_INPUT"

    # Verify :Alert memory node was created in the graph
    alert_nodes = [n for n in store.nodes.values() if n.kind == NodeKind.ALERT or "alert_retraction" in n.id]
    assert len(alert_nodes) >= 1
    alert = alert_nodes[0]
    assert "Retraction" in alert.label
    assert alert.text == notice["drafted_body"]

    # Verify ESCALATION Handoff in handoffs store
    with _handoff_lock:
        matching_handoffs = [h for h in _handoff_store if h.context_root_id == "act_email_001"]
        assert len(matching_handoffs) == 1
        handoff = matching_handoffs[0]
        assert handoff.kind == HandoffKind.ESCALATION
        assert handoff.status == HandoffStatus.PENDING
        assert handoff.correction_notice is not None
        assert handoff.correction_notice["target_action_id"] == "act_email_001"

    # 5. Test approve-correction endpoint
    res_approve = client.post("/api/actions/act_email_001/approve-correction", headers=human_headers)
    assert res_approve.status_code == 200
    res_data = res_approve.json()
    assert res_data["status"] == "APPROVED_PENDING_DELIVERY"
    assert res_data["action_id"] == "act_email_001"
    assert res_data["notice"]["status"] == "APPROVED_PENDING_DELIVERY"

    # Verify action node and handoff updated to DISPATCHED / COMPLETED
    assert email_act.action.correction_notice["status"] == "APPROVED_PENDING_DELIVERY"
    with _handoff_lock:
        updated_handoff = [h for h in _handoff_store if h.context_root_id == "act_email_001"][0]
        assert updated_handoff.status == HandoffStatus.COMPLETED
        assert updated_handoff.correction_notice["status"] == "APPROVED_PENDING_DELIVERY"


def test_handoff_review_approval_dispatches_correction_notice(client):
    """Asserts that reviewing and approving an escalation handoff with a correction notice keeps delivery pending after approval."""
    human_headers = {"Authorization": "Bearer token_human"}
    store = get_graph_store("acme")
    with store._lock:
        store.nodes.clear()
        store.edges.clear()

    with _handoff_lock:
        _handoff_store.clear()

    email_act = GraphNode(
        id="act_email_002",
        kind=NodeKind.ACTION,
        label="Send refund confirmation email to customer",
        text="Your refund has been approved under 90-day policy.",
        status=Status.QUARANTINED,
        action=ActionInfo(
            type="SEND_CUSTOMER_EMAIL",
            reversibility=Reversibility.IRREVERSIBLE,
            executed=True,
            gate=GateStatus.PASSED,
        ),
    )
    store.add_node(email_act)
    notice = generate_correction_notice(email_act, store, tenant="acme")
    email_act.action.correction_notice = notice

    from app.models.handoffs import Handoff
    handoff = Handoff(
        id="hand_escl_002",
        kind=HandoffKind.ESCALATION,
        task="Irreversible action quarantined! Retraction notice drafted.",
        status=HandoffStatus.PENDING,
        context_root_id="act_email_002",
        correction_notice=notice,
    )
    with _handoff_lock:
        _handoff_store.append(handoff)

    res = client.post(
        "/api/handoffs/hand_escl_002/review",
        json={"decision": "APPROVE", "notes": "Approved damage control notice"},
        headers=human_headers,
    )
    assert res.status_code == 200
    assert handoff.status == HandoffStatus.COMPLETED
    assert handoff.correction_notice["status"] == "APPROVED_PENDING_DELIVERY"
    assert email_act.action.correction_notice["status"] == "APPROVED_PENDING_DELIVERY"

