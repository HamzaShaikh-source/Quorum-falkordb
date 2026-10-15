import time
from typing import List, Optional
from app.models.common import (
    Status,
    NodeKind,
    EdgeType,
    Tier,
    SourceKind,
    Reversibility,
    GateStatus,
    CompensationStatus,
    HandoffKind,
    HandoffStatus,
)
from app.models.graph import (
    GraphNode,
    GraphEdge,
    GraphResponse,
    AssertedBy,
    SourceInfo,
    ActionInfo,
    CompensationInfo,
)
from app.models.handoffs import Handoff
from app.models.agents import AgentItem, ProcedureItem
from app.config import settings


def get_fixture_graph(tenant: str = "acme", as_of: Optional[int] = None) -> GraphResponse:
    now_ms = int(time.time() * 1000)

    nodes = [
        GraphNode(
            id="src_policy_v3",
            kind=NodeKind.SOURCE,
            label="Refund policy v3",
            status=Status.HEALTHY,
            stale=False,
            valid_from=now_ms - 86400000,
            source=SourceInfo(
                kind=SourceKind.DOC,
                title="Refund policy",
                url="https://acme.example/policy",
                version="3",
            ),
            owner_team="Support Ops",
        ),
        GraphNode(
            id="fact_30d",
            kind=NodeKind.FACT,
            label="Refund window is 30 days",
            status=Status.HEALTHY,
            stale=False,
            key="refund_window_days",
            value="30",
            text="Refunds are allowed within 30 days of purchase.",
            quote="Refunds are allowed within 30 days.",
            valid_from=now_ms - 86400000,
            asserted_by=AssertedBy(
                id="agent_root",
                name="Policy owner",
                tier=Tier.ROOT,
            ),
            owner_team="Support Ops",
        ),
        GraphNode(
            id="src_webhook_partner",
            kind=NodeKind.SOURCE,
            label="Partner webhook stream",
            status=Status.HEALTHY,
            stale=False,
            valid_from=now_ms - 3600000,
            origin="webhook:partner-feed",
            source=SourceInfo(
                kind=SourceKind.WEBHOOK,
                title="Partner support feed",
                url="https://partner.example/webhook",
                version="1.0",
            ),
        ),
        GraphNode(
            id="fact_90d",
            kind=NodeKind.FACT,
            label="Refund window is 90 days",
            status=Status.QUARANTINED,
            stale=False,
            key="refund_window_days",
            value="90",
            text="Partner claims refunds are permitted up to 90 days.",
            quote="Partner policy states 90 day coverage.",
            origin="webhook:partner-feed",
            quarantined_at=now_ms - 120000,
            quarantine_root="fact_90d",
            depth=0,
            asserted_by=AssertedBy(
                id="agent_scout",
                name="Scout Agent",
                tier=Tier.NEW,
            ),
        ),
        GraphNode(
            id="conc_eligible_1042",
            kind=NodeKind.CONCLUSION,
            is_decision=True,
            label="Order 1042 eligible for refund (45d)",
            status=Status.QUARANTINED,
            stale=False,
            text="Order 1042 was placed 45 days ago, valid under 90-day rule.",
            quarantined_at=now_ms - 120000,
            quarantine_root="fact_90d",
            depth=1,
            owner_team="Billing Tier 1",
        ),
        GraphNode(
            id="act_refund_2",
            kind=NodeKind.ACTION,
            label="Refund order 1042 ($180)",
            status=Status.QUARANTINED,
            stale=False,
            depth=2,
            quarantined_at=now_ms - 120000,
            quarantine_root="fact_90d",
            action=ActionInfo(
                type="refund",
                reversibility=Reversibility.COMPENSABLE,
                executed=True,
                confirmed=True,
                gate=GateStatus.PASSED,
                procedure_id="proc_refund_standard",
                compensation=CompensationInfo(
                    status=CompensationStatus.COMPENSATED,
                    attempts=1,
                ),
            ),
        ),
        GraphNode(
            id="act_email_cust",
            kind=NodeKind.ACTION,
            label="Send approval email to customer",
            text="Your refund of $150 has been approved under our new 90-day policy.",
            status=Status.QUARANTINED,
            depth=2,
            quarantined_at=now_ms - 120000,
            quarantine_root="fact_90d",
            stale=False,
            action=ActionInfo(
                type="SEND_CUSTOMER_EMAIL",
                reversibility=Reversibility.IRREVERSIBLE,
                executed=True,
                confirmed=True,
                gate=GateStatus.PASSED,
                procedure_id="proc_notify_customer",
                correction_notice={
                    "target_action_id": "act_email_cust",
                    "recipient": "customer@acme.com",
                    "original_subject": "Update: Your Refund Request is Approved",
                    "original_statement": "Your refund of $150 has been approved under our new 90-day policy.",
                    "correction_subject": "CORRECTION: Important update regarding your refund request",
                    "drafted_body": "Dear Customer,\n\nWe recently sent an email stating that your refund request was approved under a 90-day refund policy. That communication was sent in error due to an unverified policy update.\n\nOur official refund policy remains 30 days (per Finance Ops Policy v3.0). We sincerely apologize for any confusion. Please find our verified policy details attached.\n\nSincerely,\nAcme Support Operations",
                    "verified_policy_citation": "Finance Ops Policy Doc v3.0 (30-day window)",
                    "status": "READY_FOR_HUMAN_APPROVAL",
                },
            ),
            correction_notice={
                "target_action_id": "act_email_cust",
                "recipient": "customer@acme.com",
                "original_subject": "Update: Your Refund Request is Approved",
                "original_statement": "Your refund of $150 has been approved under our new 90-day policy.",
                "correction_subject": "CORRECTION: Important update regarding your refund request",
                "drafted_body": "Dear Customer,\n\nWe recently sent an email stating that your refund request was approved under a 90-day refund policy. That communication was sent in error due to an unverified policy update.\n\nOur official refund policy remains 30 days (per Finance Ops Policy v3.0). We sincerely apologize for any confusion. Please find our verified policy details attached.\n\nSincerely,\nAcme Support Operations",
                "verified_policy_citation": "Finance Ops Policy Doc v3.0 (30-day window)",
                "status": "READY_FOR_HUMAN_APPROVAL",
            },
        ),
    ]

    edges = [
        GraphEdge(id="e1", source="src_policy_v3", target="fact_30d", type=EdgeType.FROM_SOURCE),
        GraphEdge(id="e2", source="src_webhook_partner", target="fact_90d", type=EdgeType.FROM_SOURCE),
        GraphEdge(id="e3", source="fact_90d", target="conc_eligible_1042", type=EdgeType.LEADS_TO),
        GraphEdge(id="e4", source="conc_eligible_1042", target="act_refund_2", type=EdgeType.LEADS_TO),
        GraphEdge(id="e5", source="conc_eligible_1042", target="act_email_cust", type=EdgeType.LEADS_TO),
    ]

    return GraphResponse(
        contract_version=settings.CONTRACT_VERSION,
        tenant=tenant,
        epoch=14,
        seq=412,
        as_of=as_of,
        generated_at=now_ms,
        truncated=False,
        nodes=nodes,
        edges=edges,
    )


def get_fixture_handoffs() -> List[Handoff]:
    return [
        Handoff(
            id="hand_email_gate",
            kind=HandoffKind.REVIEW,
            task="Approve sending refund email to customer (Quorum Gate: Irreversible action resting on uncorroborated fact_90d)",
            status=HandoffStatus.PENDING,
            assigned_to="Support Manager",
            context_root_id="act_email_cust",
        ),
        Handoff(
            id="hand_escl_email_damage_control",
            kind=HandoffKind.ESCALATION,
            task="Irreversible action 'act_email_cust' quarantined! Automated damage control retraction email drafted.",
            status=HandoffStatus.PENDING,
            assigned_to="Support Manager",
            context_root_id="act_email_cust",
            correction_notice={
                "target_action_id": "act_email_cust",
                "recipient": "customer@acme.com",
                "original_subject": "Update: Your Refund Request is Approved",
                "original_statement": "Your refund of $150 has been approved under our new 90-day policy.",
                "correction_subject": "CORRECTION: Important update regarding your refund request",
                "drafted_body": "Dear Customer,\n\nWe recently sent an email stating that your refund request was approved under a 90-day refund policy. That communication was sent in error due to an unverified policy update.\n\nOur official refund policy remains 30 days (per Finance Ops Policy v3.0). We sincerely apologize for any confusion. Please find our verified policy details attached.\n\nSincerely,\nAcme Support Operations",
                "verified_policy_citation": "Finance Ops Policy Doc v3.0 (30-day window)",
                "status": "READY_FOR_HUMAN_APPROVAL",
            },
        ),
        Handoff(
            id="hand_policy_v4",
            kind=HandoffKind.REVIEW,
            task="Review stale refund decisions after Policy v4 update (45 days)",
            status=HandoffStatus.PENDING,
            assigned_to="Billing Tier 1",
            context_root_id="fact_30d",
        ),
    ]


def get_fixture_agents() -> List[AgentItem]:
    now_ms = int(time.time() * 1000)
    return [
        AgentItem(id="agent_scout", name="Scout Agent", role="Ingests webhook and customer messages", tier=Tier.NEW, last_active=now_ms - 10000),
        AgentItem(id="agent_analyst", name="Analyst Agent", role="Evaluates refund rules and derives claims", tier=Tier.ESTABLISHED, last_active=now_ms - 8000),
        AgentItem(id="agent_refunder", name="Refunder Agent", role="Stages and executes payment compensations", tier=Tier.ESTABLISHED, last_active=now_ms - 5000),
        AgentItem(id="agent_comms", name="Comms Agent", role="Dispatches customer notices and updates FAQ", tier=Tier.NEW, last_active=now_ms - 3000),
    ]


def get_fixture_procedures() -> List[ProcedureItem]:
    return [
        ProcedureItem(
            id="proc_refund_standard",
            task_type="refund",
            steps=["verify_policy", "check_order_receipt", "stage_refund", "execute_refund"],
            uses=42,
            successes=39,
            failures=3,
            rank_score=0.909,
            seeded=True,
        ),
        ProcedureItem(
            id="proc_notify_customer",
            task_type="notify",
            steps=["draft_template", "verify_gate", "send_email"],
            uses=18,
            successes=17,
            failures=1,
            rank_score=0.900,
            seeded=True,
        ),
    ]


def seed_store(store) -> None:
    """
    Loads the fixture graph and handoffs into a store. For tests and local experiments only: no product code path
    calls this, so a real tenant never shows demo data.
    """
    g = get_fixture_graph(tenant=store.tenant)
    with store._lock:
        for n in g.nodes:
            store.nodes[n.id] = n
        store.edges.extend(g.edges)
        store.epoch = g.epoch
    with store.handoff_lock:
        store.handoffs.extend(get_fixture_handoffs())
