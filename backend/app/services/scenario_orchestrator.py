import time
import threading
import uuid
from typing import Dict, Any, Optional
from app.graph.queries import get_graph_store
from app.models.common import (
    Status,
    NodeKind,
    EdgeType,
    Tier,
    SourceKind,
    Reversibility,
    GateStatus,
)
from app.models.graph import (
    GraphNode,
    GraphEdge,
    SourceInfo,
    AssertedBy,
    ActionInfo,
)
from app.services.event_bus import event_bus
from app.services.executor import action_executor
from app.services.auditor import auditor_service
from app.services.saga import saga_worker
from app.services.helpdesk import helpdesk_service, MockHelpdeskService
from agents.procedures import procedure_registry


class ScenarioOrchestrator:
    """
    End-to-End Live Scenario Orchestrator.
    Drives the 90-second demo through the 6 scenario steps:
    1. reset: Clears state and seeds baseline policy v3 (30-day window).
    2. inject_poison: Ingests partner webhook payload (90-day window).
    3. run_agents: Analyst derives conclusion; Refunder stages 3 and executes 2; Comms blocked by Gate into REVIEW.
    4. audit: Contradiction detected; cascade quarantine (depths 0, 1, 2); execute guard blocks 3rd refund; saga worker compensates executed refunds.
    5. supersede: Verified policy v4 updates window to 45 days; old fact SUPERSEDED; dependent decisions flagged stale.
    6. restore: Demonstrates branch restoration if parent dependencies are healthy.
    """

    def __init__(self):
        self._lock = threading.RLock()

    def execute_step(self, step: str, tenant: str, request_id: Optional[str] = None) -> Dict[str, Any]:
        with self._lock:
            method = getattr(self, step, None)
            if not method or step.startswith("_") or step == "execute_step":
                raise ValueError(f"Unknown scenario step '{step}'")
            return method(tenant=tenant, request_id=request_id)

    def reset(self, tenant: str, request_id: Optional[str] = None) -> Dict[str, Any]:
        store = get_graph_store(tenant)
        with store._lock:
            store.nodes.clear()
            store.edges.clear()
            store.epoch = 1
            store.workspace["demo_run_id"] = uuid.uuid4().hex

        if isinstance(helpdesk_service, MockHelpdeskService):
            helpdesk_service.reset()
        procedure_registry.reset()
        with store.handoff_lock:
            store.handoffs.clear()

        # Seed initial canonical policy (Beat 0)
        now_ms = int(time.time() * 1000)
        src_policy = GraphNode(
            id="src_policy_v3",
            kind=NodeKind.SOURCE,
            label="Refund policy v3",
            status=Status.HEALTHY,
            stale=False,
            source=SourceInfo(
                kind=SourceKind.DOC,
                title="Refund policy",
                url="https://acme.example/policy",
                version="3",
            ),
            valid_from=now_ms - 86400000,
        )
        fact_policy = GraphNode(
            id="fact_30d",
            kind=NodeKind.FACT,
            label="Refund window is 30 days",
            text="Refunds are allowed within 30 days of purchase.",
            status=Status.HEALTHY,
            stale=False,
            key="refund_window_days",
            value="30",
            quote="Refunds are allowed within 30 days.",
            owner_team="Support Ops",
            asserted_by=AssertedBy(id="agent_root", name="Policy owner", tier=Tier.ROOT),
            source_ref="src_policy_v3",
            valid_from=now_ms - 86400000,
        )
        edge_policy = GraphEdge(
            id="e_src_policy",
            source="src_policy_v3",
            target="fact_30d",
            type=EdgeType.FROM_SOURCE,
        )

        store.add_node(src_policy, request_id=request_id)
        store.add_node(fact_policy, request_id=request_id)
        store.add_edge(edge_policy, request_id=request_id)

        event_bus.publish(tenant, "node_added", {"node": src_policy.model_dump()}, request_id=request_id)
        event_bus.publish(tenant, "node_added", {"node": fact_policy.model_dump()}, request_id=request_id)
        event_bus.publish(tenant, "edge_added", {"edge": edge_policy.model_dump()}, request_id=request_id)
        event_bus.publish(tenant, "epoch_changed", {"epoch": store.epoch, "reason": "reset"}, request_id=request_id)
        event_bus.publish(tenant, "invariant_status", {"ok": True, "violations": 0}, request_id=request_id)

        return {"status": "reset", "tenant": tenant, "epoch": store.epoch}

    def inject_poison(self, tenant: str, request_id: Optional[str] = None) -> Dict[str, Any]:
        store = get_graph_store(tenant)
        now_ms = int(time.time() * 1000)

        src_webhook = GraphNode(
            id="src_webhook_partner",
            kind=NodeKind.SOURCE,
            label="Partner webhook feed",
            status=Status.HEALTHY,
            stale=False,
            source=SourceInfo(
                kind=SourceKind.WEBHOOK,
                title="Partner feed",
                url="https://partner.example/webhook",
                version="1",
            ),
            valid_from=now_ms,
        )
        fact_90d = GraphNode(
            id="fact_90d",
            kind=NodeKind.FACT,
            label="Refund window is 90 days",
            text="Partner policy states 90 day coverage.",
            status=Status.HEALTHY,
            stale=False,
            key="refund_window_days",
            value="90",
            quote="Partner claims refunds are permitted up to 90 days.",
            origin="webhook:partner-feed",
            owner_team="Billing Tier 1",
            asserted_by=AssertedBy(id="agent_scout", name="Scout Agent", tier=Tier.NEW),
            source_ref="src_webhook_partner",
            valid_from=now_ms,
        )
        edge_webhook = GraphEdge(
            id="e_src_webhook",
            source="src_webhook_partner",
            target="fact_90d",
            type=EdgeType.FROM_SOURCE,
        )

        with store._lock:
            store.nodes.pop(src_webhook.id, None)
            store.nodes.pop(fact_90d.id, None)
            store.edges = [e for e in store.edges if e.id != edge_webhook.id]
        store.add_node(src_webhook, request_id=request_id)
        store.add_node(fact_90d, request_id=request_id)
        store.add_edge(edge_webhook, request_id=request_id)

        event_bus.publish(tenant, "node_added", {"node": src_webhook.model_dump()}, request_id=request_id)
        event_bus.publish(tenant, "node_added", {"node": fact_90d.model_dump()}, request_id=request_id)
        event_bus.publish(tenant, "edge_added", {"edge": edge_webhook.model_dump()}, request_id=request_id)

        return {"status": "injected", "fact_id": "fact_90d"}

    def run_agents(self, tenant: str, request_id: Optional[str] = None) -> Dict[str, Any]:
        store = get_graph_store(tenant)
        now_ms = int(time.time() * 1000)

        if "fact_90d" not in store.nodes:
            self.inject_poison(tenant, request_id)

        # 1. Analyst Agent: derives conclusion from poisoned fact
        conc = GraphNode(
            id="conc_eligible_1042",
            kind=NodeKind.CONCLUSION,
            label="Order 1042 eligible for refund (45d)",
            text="Order 1042 placed 45 days ago is eligible under 90-day policy rule.",
            status=Status.HEALTHY,
            stale=False,
            is_decision=True,
            owner_team="Billing Tier 1",
            valid_from=now_ms,
        )
        e_conc = GraphEdge(
            id="e_conc_1042",
            source="fact_90d",
            target="conc_eligible_1042",
            type=EdgeType.LEADS_TO,
        )
        with store._lock:
            store.nodes.pop(conc.id, None)
            store.edges = [e for e in store.edges if e.id != e_conc.id]
        store.add_node(conc, request_id=request_id)
        store.add_edge(e_conc, request_id=request_id)
        event_bus.publish(tenant, "node_added", {"node": conc.model_dump()}, request_id=request_id)
        event_bus.publish(tenant, "edge_added", {"edge": e_conc.model_dump()}, request_id=request_id)

        # 2. Refunder Agent: stages 3 actions and executes 2
        refund_actions = [
            ("act_refund_1", "Process store credit for order 1042 ($50)", "refund_credit", "idemp_ref_1042_a", True),
            ("act_refund_2", "Refund card payment for order 1042 ($180)", "refund", "idemp_ref_1042_b", True),
            ("act_refund_3", "Waive restocking fee for order 1042 ($25)", "waive_fee", "idemp_ref_1042_c", False),
        ]

        executed_actions = []
        for act_id, label, act_type, idemp_key, should_execute in refund_actions:
            act_node = GraphNode(
                id=act_id,
                kind=NodeKind.ACTION,
                label=label,
                status=Status.HEALTHY,
                stale=False,
                action=ActionInfo(
                    type=act_type,
                    reversibility=Reversibility.COMPENSABLE,
                    executed=False,
                    confirmed=False,
                    gate=GateStatus.PASSED,
                    procedure_id="proc_refund_standard",
                    idempotency_key=f"{idemp_key}:{store.workspace.get('demo_run_id', 'legacy')}",
                ),
                valid_from=now_ms,
            )
            e_act = GraphEdge(
                id=f"e_{act_id}",
                source="conc_eligible_1042",
                target=act_id,
                type=EdgeType.LEADS_TO,
            )
            with store._lock:
                store.nodes.pop(act_node.id, None)
                store.edges = [e for e in store.edges if e.id != e_act.id]
            store.add_node(act_node, request_id=request_id)
            store.add_edge(e_act, request_id=request_id)
            event_bus.publish(tenant, "node_added", {"node": act_node.model_dump()}, request_id=request_id)
            event_bus.publish(tenant, "edge_added", {"edge": e_act.model_dump()}, request_id=request_id)

            if should_execute:
                try:
                    action_executor.execute(store, act_id, tenant=tenant, request_id=request_id)
                    executed_actions.append(act_id)
                except Exception:
                    pass

        # 3. Comms Agent: executed irreversible customer email before audit
        email_node = GraphNode(
            id="act_email_cust",
            kind=NodeKind.ACTION,
            label="Send approval email to customer",
            text="Your refund of $150 has been approved under our new 90-day policy.",
            status=Status.HEALTHY,
            stale=False,
            action=ActionInfo(
                type="SEND_CUSTOMER_EMAIL",
                reversibility=Reversibility.IRREVERSIBLE,
                executed=True,
                confirmed=True,
                gate=GateStatus.PASSED,
                procedure_id="proc_notify_customer",
                idempotency_key="idemp_email_1042",
            ),
            valid_from=now_ms,
        )
        e_email = GraphEdge(
            id="e_act_email",
            source="conc_eligible_1042",
            target="act_email_cust",
            type=EdgeType.LEADS_TO,
        )
        with store._lock:
            store.nodes.pop(email_node.id, None)
            store.edges = [e for e in store.edges if e.id != e_email.id]
        store.add_node(email_node, request_id=request_id)
        store.add_edge(e_email, request_id=request_id)
        event_bus.publish(tenant, "node_added", {"node": email_node.model_dump()}, request_id=request_id)
        event_bus.publish(tenant, "edge_added", {"edge": e_email.model_dump()}, request_id=request_id)
        executed_actions.append("act_email_cust")

        return {
            "status": "agents_completed",
            "derived_decision": "conc_eligible_1042",
            "executed_actions": executed_actions,
            "staged_unexecuted": ["act_refund_3"],
            "executed_irreversible_action": "act_email_cust",
            "gate_status": GateStatus.PASSED.value,
        }

    def audit(self, tenant: str, request_id: Optional[str] = None) -> Dict[str, Any]:
        store = get_graph_store(tenant)

        # 1. Auditor detects contradiction between fact_30d and fact_90d
        fact_90d = store.nodes.get("fact_90d")
        if not fact_90d:
            self.inject_poison(tenant, request_id)
            fact_90d = store.nodes.get("fact_90d")

        # Trust rule overrules uncorroborated new fact -> Cascade quarantine
        affected = auditor_service.quarantine(
            store,
            "fact_90d",
            reason="Contradicts root-tier policy fact_30d",
            tenant=tenant,
            request_id=request_id,
        )

        # 2. Outbox saga worker claims and compensates executed refunds
        compensated = saga_worker.process_pending(store, tenant=tenant)
        compensated_ids = [c.id for c in compensated]

        # 3. Refunder tries to execute act_refund_3 -> refused by execute guard!
        refused = False
        try:
            action_executor.execute(store, "act_refund_3", tenant=tenant, request_id=request_id)
        except Exception:
            refused = True

        # 4. Adaptive learning: procedure rank adjusts
        procedure_registry.record_outcome("proc_refund_standard", success=False)

        # Invariant check
        invariants = store.check_invariants()
        event_bus.publish(tenant, "invariant_status", {"ok": invariants["ok"], "violations": 0}, request_id=request_id)

        return {
            "status": "audited",
            "quarantined_count": len(affected),
            "compensated_actions": compensated_ids,
            "execute_guard_refused_staged": refused,
            "invariants_ok": invariants["ok"],
            "top_procedure": getattr(procedure_registry.get_best_procedure("refund"), "id", None),
        }

    def supersede(self, tenant: str, request_id: Optional[str] = None) -> Dict[str, Any]:
        store = get_graph_store(tenant)
        now_ms = int(time.time() * 1000)

        # Verified policy update v4 (window changed to 45 days)
        src_v4 = GraphNode(
            id="src_policy_v4",
            kind=NodeKind.SOURCE,
            label="Refund policy v4",
            status=Status.HEALTHY,
            stale=False,
            source=SourceInfo(
                kind=SourceKind.DOC,
                title="Refund policy v4",
                url="https://acme.example/policy/v4",
                version="4",
            ),
            valid_from=now_ms,
        )
        fact_45d = GraphNode(
            id="fact_45d_v4",
            kind=NodeKind.FACT,
            label="Refund window is 45 days",
            text="Refund window updated to 45 days under Q4 policy adjustment.",
            status=Status.HEALTHY,
            stale=False,
            key="refund_window_days",
            value="45",
            quote="Refunds are allowed within 45 days of purchase.",
            owner_team="Support Ops",
            asserted_by=AssertedBy(id="agent_root", name="Policy owner", tier=Tier.ROOT),
            source_ref="src_policy_v4",
            valid_from=now_ms,
        )
        e_src = GraphEdge(
            id="e_src_v4",
            source="src_policy_v4",
            target="fact_45d_v4",
            type=EdgeType.FROM_SOURCE,
        )
        e_supersedes = GraphEdge(
            id="e_supersede_30d",
            source="fact_45d_v4",
            target="fact_30d",
            type=EdgeType.SUPERSEDES,
        )

        with store._lock:
            store.nodes.pop(src_v4.id, None)
            store.nodes.pop(fact_45d.id, None)
            store.edges = [e for e in store.edges if e.id not in (e_src.id, e_supersedes.id)]
        store.add_node(src_v4, request_id=request_id)
        store.add_node(fact_45d, request_id=request_id)
        store.add_edge(e_src, request_id=request_id)
        store.add_edge(e_supersedes, request_id=request_id)

        # Mark old fact as SUPERSEDED and flag dependent decisions as stale
        old_fact = store.nodes.get("fact_30d")
        if old_fact:
            old_fact.status = Status.SUPERSEDED
            old_fact.valid_to = now_ms
            event_bus.publish(tenant, "status_changed", {"id": "fact_30d", "status": Status.SUPERSEDED.value}, request_id=request_id)

        for n in store.nodes.values():
            if n.kind == NodeKind.CONCLUSION:
                n.stale = True

        store.bump_epoch(request_id)
        event_bus.publish(tenant, "node_added", {"node": src_v4.model_dump()}, request_id=request_id)
        event_bus.publish(tenant, "node_added", {"node": fact_45d.model_dump()}, request_id=request_id)
        event_bus.publish(tenant, "edge_added", {"edge": e_src.model_dump()}, request_id=request_id)
        event_bus.publish(tenant, "edge_added", {"edge": e_supersedes.model_dump()}, request_id=request_id)
        event_bus.publish(tenant, "epoch_changed", {"epoch": store.epoch, "reason": "supersede"}, request_id=request_id)

        return {
            "status": "superseded",
            "old_fact": "fact_30d",
            "new_fact": "fact_45d_v4",
            "epoch": store.epoch,
        }

    def restore(self, tenant: str, request_id: Optional[str] = None) -> Dict[str, Any]:
        store = get_graph_store(tenant)
        # Attempt restore of fact_30d or fact_90d
        restored = auditor_service.restore(store, "fact_30d", tenant=tenant, request_id=request_id)
        return {"status": "restored", "restored_nodes": restored, "epoch": store.epoch}


scenario_orchestrator = ScenarioOrchestrator()
