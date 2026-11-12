import pytest
from app.graph.queries import GraphStore
from app.models.common import (
    Status,
    NodeKind,
    EdgeType,
    Tier,
    Reversibility,
    GateStatus,
    CompensationStatus,
)
from app.models.graph import GraphNode, GraphEdge, AssertedBy, ActionInfo
from app.services.gate import quorum_gate
from app.services.executor import action_executor
from app.services.helpdesk import helpdesk_service
from app.services.auditor import auditor_service
from app.services.saga import saga_worker
from fastapi import HTTPException


@pytest.fixture
def fresh_store():
    store = GraphStore(tenant="acme_test")
    helpdesk_service.reset()
    return store


def test_gate_blocks_irreversible_action_with_uncorroborated_leaf(fresh_store):
    store = fresh_store

    # Root policy fact (sibling leaf)
    fact_root = GraphNode(
        id="fact_root_policy",
        kind=NodeKind.FACT,
        label="Root Policy",
        status=Status.HEALTHY,
        asserted_by=AssertedBy(id="agent_root", name="Root", tier=Tier.ROOT),
    )
    store.add_node(fact_root)

    # Poisoned uncorroborated fact from webhook
    fact_webhook = GraphNode(
        id="fact_webhook_90d",
        kind=NodeKind.FACT,
        label="Webhook 90d",
        status=Status.HEALTHY,
        key="refund_window",
        value="90",
        origin="webhook:partner-feed",
        asserted_by=AssertedBy(id="agent_scout", name="Scout", tier=Tier.NEW),
    )
    store.add_node(fact_webhook)

    # Derived conclusion depending on BOTH facts
    conc = GraphNode(
        id="conc_eval",
        kind=NodeKind.CONCLUSION,
        label="Conclusion",
        status=Status.HEALTHY,
    )
    store.add_node(conc)
    store.add_edge(GraphEdge(id="e1", source=fact_root.id, target=conc.id, type=EdgeType.LEADS_TO))
    store.add_edge(GraphEdge(id="e2", source=fact_webhook.id, target=conc.id, type=EdgeType.LEADS_TO))

    # Irreversible email action
    act_email = GraphNode(
        id="act_email_external",
        kind=NodeKind.ACTION,
        label="Send Email",
        status=Status.HEALTHY,
        action=ActionInfo(
            type="email",
            reversibility=Reversibility.IRREVERSIBLE,
            executed=False,
            confirmed=False,
            gate=GateStatus.PASSED,
        ),
    )
    store.add_node(act_email)
    store.add_edge(GraphEdge(id="e3", source=conc.id, target=act_email.id, type=EdgeType.LEADS_TO))

    # Evaluate Gate
    status_result, reason, handoff = quorum_gate.evaluate(store, act_email, tenant="acme_test")

    # MUST be BLOCKED because fact_webhook_90d is uncorroborated, even though fact_root is root-backed!
    assert status_result == GateStatus.BLOCKED
    assert act_email.action.gate == GateStatus.BLOCKED
    assert handoff is not None
    assert "fact_webhook_90d" in reason


def test_gate_blocks_if_ancestor_is_stale(fresh_store):
    store = fresh_store

    fact_root = GraphNode(
        id="fact_old_policy",
        kind=NodeKind.FACT,
        label="Old Policy",
        status=Status.HEALTHY,
        stale=True,  # STALE
        asserted_by=AssertedBy(id="agent_root", name="Root", tier=Tier.ROOT),
    )
    store.add_node(fact_root)

    act_email = GraphNode(
        id="act_stale_email",
        kind=NodeKind.ACTION,
        label="Send Email",
        status=Status.HEALTHY,
        action=ActionInfo(
            type="email",
            reversibility=Reversibility.IRREVERSIBLE,
            executed=False,
            confirmed=False,
            gate=GateStatus.PASSED,
        ),
    )
    store.add_node(act_email)
    store.add_edge(GraphEdge(id="e1", source=fact_root.id, target=act_email.id, type=EdgeType.LEADS_TO))

    status_result, reason, handoff = quorum_gate.evaluate(store, act_email, tenant="acme_test")
    assert status_result == GateStatus.BLOCKED
    assert "stale" in reason.lower()


def test_gate_passes_with_corroboration_and_draws_supports_edge(fresh_store):
    store = fresh_store

    fact_a = GraphNode(
        id="fact_a",
        kind=NodeKind.FACT,
        label="Chat Fact",
        status=Status.HEALTHY,
        key="refund_window",
        value="45",
        origin="chat:slack",
        asserted_by=AssertedBy(id="agent_scout", name="Scout", tier=Tier.NEW),
    )
    store.add_node(fact_a)

    fact_b_corroborator = GraphNode(
        id="fact_b",
        kind=NodeKind.FACT,
        label="Doc Fact",
        status=Status.HEALTHY,
        key="refund_window",
        value="45",
        origin="doc:wiki",  # Different origin!
        asserted_by=AssertedBy(id="agent_analyst", name="Analyst", tier=Tier.ESTABLISHED),  # Established tier!
    )
    store.add_node(fact_b_corroborator)

    act_email = GraphNode(
        id="act_corroborated_email",
        kind=NodeKind.ACTION,
        label="Send Email",
        status=Status.HEALTHY,
        action=ActionInfo(
            type="email",
            reversibility=Reversibility.IRREVERSIBLE,
            executed=False,
            confirmed=False,
            gate=GateStatus.PASSED,
        ),
    )
    store.add_node(act_email)
    store.add_edge(GraphEdge(id="e1", source=fact_a.id, target=act_email.id, type=EdgeType.LEADS_TO))

    status_result, reason, handoff = quorum_gate.evaluate(store, act_email, tenant="acme_test")
    assert status_result == GateStatus.PASSED
    assert act_email.action.gate == GateStatus.PASSED

    # Assert SUPPORTS edge was drawn from corroborator to action!
    supports_edges = [e for e in store.edges if e.source == fact_b_corroborator.id and e.target == act_email.id and e.type == EdgeType.SUPPORTS]
    assert len(supports_edges) == 1


def test_quarantining_corroborator_propagates_via_supports(fresh_store):
    store = fresh_store

    # Corroborator connected via SUPPORTS
    corroborator = GraphNode(id="fact_corrob", kind=NodeKind.FACT, label="Corroborator", status=Status.HEALTHY)
    store.add_node(corroborator)

    action = GraphNode(
        id="act_supported",
        kind=NodeKind.ACTION,
        label="Action",
        status=Status.HEALTHY,
        action=ActionInfo(type="email", reversibility=Reversibility.IRREVERSIBLE, executed=False, confirmed=False, gate=GateStatus.PASSED),
    )
    store.add_node(action)
    store.add_edge(GraphEdge(id="supp_1", source=corroborator.id, target=action.id, type=EdgeType.SUPPORTS))

    # Quarantine the corroborator!
    auditor_service.quarantine(store, corroborator.id, reason="Found tainted", tenant="acme_test")

    # Action must be quarantined via the SUPPORTS edge!
    assert action.status == Status.QUARANTINED
    assert action.depth == 1


def test_atomic_execute_guard_refuses_when_parent_is_quarantined(fresh_store):
    store = fresh_store

    parent = GraphNode(id="fact_parent", kind=NodeKind.FACT, label="Parent", status=Status.QUARANTINED)  # Quarantined!
    store.add_node(parent)

    action = GraphNode(
        id="act_test",
        kind=NodeKind.ACTION,
        label="Refund",
        status=Status.HEALTHY,
        action=ActionInfo(
            type="refund",
            reversibility=Reversibility.COMPENSABLE,
            executed=False,
            confirmed=False,
            gate=GateStatus.PASSED,
        ),
    )
    store.add_node(action)
    store.add_edge(GraphEdge(id="e1", source=parent.id, target=action.id, type=EdgeType.LEADS_TO))

    # Execution MUST raise 409 GUARD_REFUSED
    with pytest.raises(HTTPException) as exc_info:
        action_executor.execute(store, action.id, tenant="acme_test")

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"]["code"] == "GUARD_REFUSED"
    assert action.action.executed is False  # Never marked executed


def test_tombstone_race_prevents_late_execution(fresh_store):
    store = fresh_store
    idempotency_key = "idemp_race_123"

    # Action staged
    action = GraphNode(
        id="act_race",
        kind=NodeKind.ACTION,
        label="Refund Race",
        status=Status.HEALTHY,
        action=ActionInfo(
            type="refund",
            reversibility=Reversibility.COMPENSABLE,
            executed=False,
            confirmed=False,
            gate=GateStatus.PASSED,
        ),
    )
    action.action.idempotency_key = idempotency_key
    store.add_node(action)

    # 1. Compensation runs FIRST (e.g. timeout on in-flight action)
    from app.services.helpdesk import external_key
    idempotency_key = external_key("acme", action)
    comp_resp = helpdesk_service.compensate_action(idempotency_key, action.id, "refund")
    assert comp_resp["result"] == "tombstoned"
    assert idempotency_key in helpdesk_service.tombstones

    # 2. Late execution call arrives
    with pytest.raises(HTTPException) as exc_info:
        action_executor.execute(store, action.id, tenant="acme_test")

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"]["code"] == "CONFLICT"
    assert action.action.confirmed is False


def test_auditor_trust_rule_contradicting_root_quarantines_challenger(fresh_store):
    store = fresh_store

    root_fact = GraphNode(
        id="fact_policy_30d",
        kind=NodeKind.FACT,
        label="30 days policy",
        status=Status.HEALTHY,
        key="refund_window",
        value="30",
        asserted_by=AssertedBy(id="agent_root", name="Root", tier=Tier.ROOT),
    )
    store.add_node(root_fact)

    challenger = GraphNode(
        id="fact_chat_90d",
        kind=NodeKind.FACT,
        label="90 days claim",
        status=Status.HEALTHY,
        key="refund_window",
        value="90",
        asserted_by=AssertedBy(id="agent_scout", name="Scout", tier=Tier.NEW),
    )
    store.add_node(challenger)

    # Contradiction check
    verdict = auditor_service.check_contradiction(store, challenger, tenant="acme_test")
    assert verdict["verdict"] == "CHALLENGER_QUARANTINED"
    assert challenger.status == Status.QUARANTINED
    assert root_fact.status == Status.HEALTHY


def test_saga_worker_compensates_executed_actions(fresh_store):
    store = fresh_store

    action = GraphNode(
        id="act_executed_comp",
        kind=NodeKind.ACTION,
        label="Executed Refund",
        status=Status.QUARANTINED,
        action=ActionInfo(
            type="refund",
            reversibility=Reversibility.COMPENSABLE,
            executed=True,
            confirmed=True,
            gate=GateStatus.PASSED,
        ),
    )
    action.action.idempotency_key = "idemp_saga_999"
    store.add_node(action)

    # Helpdesk marked it executed initially
    helpdesk_service.execute_action("idemp_saga_999", "refund", {})

    # Trigger quarantine cascade which enqueues compensation
    auditor_service.quarantine(store, action.id, tenant="acme_test")
    assert action.action.compensation.status == CompensationStatus.PENDING_COMPENSATION

    # Run saga worker
    processed = saga_worker.process_pending(store, tenant="acme_test")
    assert len(processed) == 1
    assert action.action.compensation.status == CompensationStatus.COMPENSATED
