import uuid
import time
from typing import List, Dict, Any
from app.graph.queries import GraphStore
from app.models.common import (
    NodeKind,
    Tier,
    Status,
    CompensationStatus,
    HandoffKind,
    HandoffStatus,
)
from app.models.graph import GraphNode
from app.models.handoffs import Handoff
from app.services.helpdesk import helpdesk_service, external_key
from app.services.event_bus import event_bus
from app.services.access import inherited_scopes


def generate_correction_notice(
    action_node: GraphNode,
    store: GraphStore,
    tenant: str = "acme",
) -> Dict[str, Any]:
    """
    Automated Damage Control / Retraction Notice Generator:
    Generates structured CorrectionNotice for an executed IRREVERSIBLE action
    hit by quarantine cascade, linking back to verified truth and policy citations.
    """
    payload = action_node.action.payload if action_node.action else {}
    recipient = payload.get("recipient") or payload.get("customer_id")
    ancestors = store.get_ancestors(action_node.id, ["LEADS_TO", "SUPPORTS", "FROM_SOURCE"])
    affected = {n.id for n in ancestors}
    with store._lock:
        all_nodes = list(store.nodes.values())
    verified = [n for n in all_nodes if n.kind == NodeKind.FACT and n.status == Status.HEALTHY
                and not n.stale and n.asserted_by and n.asserted_by.tier == Tier.ROOT
                and set(inherited_scopes([n])) <= set(inherited_scopes([action_node]))
                and (n.id in affected or any(p.key and p.key == n.key for p in ancestors))]
    citations = [{"id": n.id, "text": n.text or n.label, "source_ref": n.source_ref, "quote": n.quote} for n in verified]
    missing = []
    if not recipient:
        missing.append("recipient")
    if not verified:
        missing.append("verified replacement facts")
    original = action_node.text or action_node.label
    body = f"Correction to action {action_node.id}: {original}.\n"
    body += "\n".join(f"Verified replacement [{n.id}]: {n.text or n.label}" for n in verified)
    if missing:
        body += "\nHuman input required: " + ", ".join(missing)
    return {
        "target_action_id": action_node.id,
        "recipient": recipient,
        "original_subject": payload.get("subject"),
        "original_statement": original,
        "correction_subject": f"Correction: {action_node.label}",
        "drafted_body": body,
        "verified_policy_citation": "; ".join(n.source_ref or n.id for n in verified),
        "provenance": citations,
        "missing_information": missing,
        "status": "NEEDS_HUMAN_INPUT" if missing else "READY_FOR_HUMAN_APPROVAL",
    }


class OutboxSagaWorker:
    """
    Outbox Saga Worker:
    - Polls actions where compensation is PENDING_COMPENSATION.
    - Claims with lease (DISPATCHING).
    - Calls Helpdesk with idempotency keys.
    - Handles in-flight actions (executed=True, confirmed=False older than 60s) safely via Helpdesk tombstones.
    """
    def __init__(self, max_attempts: int = 3):
        self.max_attempts = max_attempts
        self.owner = uuid.uuid4().hex
        self.lease_ms = 30000
        self.retry_ms = 1000

    def process_pending(self, store: GraphStore, tenant: str = "acme") -> List[GraphNode]:
        self.reconcile_executions(store, tenant)
        processed = []

        for node in list(store.nodes.values()):
            if node.kind != NodeKind.ACTION or not node.action or not node.action.compensation:
                continue

            with store._lock:
                comp = node.action.compensation
                now = int(time.time() * 1000)
                if not comp or comp.status not in (CompensationStatus.PENDING_COMPENSATION, CompensationStatus.DISPATCHING):
                    continue
                if comp.status == CompensationStatus.DISPATCHING and comp.lease_until and comp.lease_until > now:
                    continue
                if comp.next_attempt_at > now:
                    continue
                if comp.attempts >= self.max_attempts:
                    comp.status = CompensationStatus.FAILED
                    comp.last_error = comp.last_error or "Retry budget exhausted after interrupted delivery"
                    comp.lease_owner = None
                    comp.lease_until = None
                    handoff = Handoff(id=f"hand_comp_fail_{uuid.uuid4().hex}", kind=HandoffKind.ESCALATION,
                                      task=f"Confirm compensation result for {node.id}; last delivery was interrupted and retry budget is exhausted.",
                                      status=HandoffStatus.PENDING, context_root_id=node.id)
                    store.add_handoff(handoff)
                    store.commit()
                    event_bus.publish(tenant, "handoff_updated", {"handoff": handoff.model_dump()})
                    event_bus.publish(
                        tenant,
                        "compensation_updated",
                        {"action_id": node.id, "status": comp.status.value, "attempts": comp.attempts},
                    )
                    continue
                comp.status = CompensationStatus.DISPATCHING
                claim_owner = f"{self.owner}:{uuid.uuid4().hex}"
                comp.lease_owner = claim_owner
                comp.lease_until = now + self.lease_ms
                comp.attempts += 1
                idempotency_key = external_key(tenant, node)
                store.commit()

            event_bus.publish(
                tenant,
                "compensation_updated",
                {"action_id": node.id, "status": comp.status.value, "attempts": comp.attempts},
            )

            # In-flight check: if unconfirmed and older than 60s, dispatch compensation anyway
            # The helpdesk tombstone guarantees safety
            try:
                resp = helpdesk_service.compensate_action(
                    idempotency_key=idempotency_key,
                    action_id=node.id,
                    action_type=node.action.type,
                )
            except Exception as e:
                resp = {"status": 500, "error": str(e)}

            with store._lock:
                if comp.lease_owner != claim_owner:
                    continue
                comp.lease_owner = None
                comp.lease_until = None
                if resp.get("status") == 200:
                    comp.last_error = None
                    comp.status = CompensationStatus.COMPENSATED
                    processed.append(node)
                else:
                    comp.last_error = str(resp.get("error") or resp.get("status"))
                    comp.next_attempt_at = int(time.time() * 1000) + self.retry_ms * (2 ** (comp.attempts - 1))
                    if comp.attempts >= self.max_attempts:
                        comp.status = CompensationStatus.FAILED
                        # Create ESCALATION handoff for human
                        handoff = Handoff(
                            id=f"hand_comp_fail_{uuid.uuid4().hex[:6]}",
                            kind=HandoffKind.ESCALATION,
                            task=f"Compensation failed for action '{node.id}' after {comp.attempts} attempts. Manual intervention required.",
                            status=HandoffStatus.PENDING,
                            context_root_id=node.id,
                        )
                        store.add_handoff(handoff)
                        event_bus.publish(tenant, "handoff_updated", {"handoff": handoff.model_dump()})
                    else:
                        comp.status = CompensationStatus.PENDING_COMPENSATION

                store.commit()
            event_bus.publish(
                tenant, "compensation_updated",
                {"action_id": node.id, "status": comp.status.value, "attempts": comp.attempts},
            )

        return processed

    def reconcile_executions(self, store: GraphStore, tenant: str) -> None:
        """An uncertain external result is retried with its durable key after the lease expires.

        Provider idempotency is required: an external success followed by a process
        crash then returns the original result. Legacy unkeyed records need human review.
        """
        for node in list(store.nodes.values()):
            with store._lock:
                action = node.action
                now = int(time.time() * 1000)
                if not action or not action.executed or action.confirmed or action.compensation:
                    continue
                if node.status != Status.HEALTHY or node.stale or action.gate.value != "PASSED":
                    continue
                if not action.external_key or action.execution_attempts == 0:
                    if action.delivery_error != "LEGACY_REQUIRES_REVIEW":
                        action.delivery_error = "LEGACY_REQUIRES_REVIEW"
                        handoff = Handoff(id=f"hand_legacy_{uuid.uuid4().hex}", kind=HandoffKind.ESCALATION,
                                          task=f"Confirm historical external delivery for {node.id}; no durable delivery attempt was recorded.",
                                          status=HandoffStatus.PENDING, context_root_id=node.id)
                        store.add_handoff(handoff)
                        store.commit()
                        event_bus.publish(tenant, "handoff_updated", {"handoff": handoff.model_dump()})
                    continue
                if any(n.status != Status.HEALTHY or n.stale for n in store.get_ancestors(node.id)):
                    continue
                if action.execution_lease_until and action.execution_lease_until > now:
                    continue
                if action.execution_attempts >= self.max_attempts:
                    if action.delivery_error != "RECONCILIATION_EXHAUSTED":
                        action.delivery_error = "RECONCILIATION_EXHAUSTED"
                        handoff = Handoff(id=f"hand_execute_{uuid.uuid4().hex}", kind=HandoffKind.ESCALATION,
                                          task=f"Confirm external delivery for {node.id}; retries exhausted.",
                                          status=HandoffStatus.PENDING, context_root_id=node.id)
                        store.add_handoff(handoff)
                        store.commit()
                        event_bus.publish(tenant, "handoff_updated", {"handoff": handoff.model_dump()})
                    continue
                action.execution_attempts += 1
                action.execution_lease_until = now + self.lease_ms
                store.commit()
            try:
                response = helpdesk_service.execute_action(action.external_key, action.type, action.payload)
            except Exception as exc:
                response = {"status": 502, "error": type(exc).__name__}
            with store._lock:
                if response.get("status") == 200:
                    action.confirmed = True
                    action.delivery_error = None
                    action.execution_lease_until = None
                    event_bus.publish(
                        tenant,
                        "action_executed",
                        {"action_id": node.id, "confirmed": True},
                    )
                elif not action.confirmed:
                    action.delivery_error = str(response.get("error") or response.get("status"))
                store.commit()


saga_worker = OutboxSagaWorker()
