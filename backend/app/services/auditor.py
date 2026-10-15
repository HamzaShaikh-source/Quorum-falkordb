import time
import uuid
from typing import List, Dict, Any, Optional
from app.graph.queries import GraphStore
from app.models.common import (
    NodeKind,
    Tier,
    Status,
    Reversibility,
    CompensationStatus,
    HandoffKind,
    HandoffStatus,
    EdgeType,
)
from app.models.graph import GraphNode, GraphEdge, CompensationInfo
from app.models.handoffs import Handoff
from app.services.event_bus import event_bus
from app.services.access import inherited_scopes


class AuditorService:
    """
    Deterministic Auditor Service (NOT an LLM):
    1. Contradiction detection & trust rule evaluation (root > corroborated > tier).
    2. Supersede authority verification.
    3. Triggers cascade quarantine, sets depths, bumps Meta.epoch, and enqueues compensations.
    4. Triggers restore, checks all parents healthy, bumps Meta.epoch.
    """
    def check_contradiction(
        self,
        store: GraphStore,
        new_fact: GraphNode,
        tenant: str = "acme",
        request_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if new_fact.key is None or new_fact.value is None:
            return None

        # Find existing healthy facts with same key
        with store._lock:
            conflicting_facts = [
                f for f in list(store.nodes.values())
                if f.kind == NodeKind.FACT and f.status == Status.HEALTHY and set(inherited_scopes([f])) <= set(inherited_scopes([new_fact])) and f.id != new_fact.id and f.key == new_fact.key and f.value is not None and new_fact.value is not None and f.value != new_fact.value
            ]

        if not conflicting_facts:
            return None

        old_fact = conflicting_facts[0]

        # Trust evaluation
        old_tier = old_fact.asserted_by.tier if old_fact.asserted_by else Tier.NEW
        new_tier = new_fact.asserted_by.tier if new_fact.asserted_by else Tier.NEW

        # Rule 1: Contradicting a root fact auto-quarantines the CHALLENGER (unless both are ROOT)
        if old_tier == Tier.ROOT and new_tier != Tier.ROOT:
            self.quarantine(store, new_fact.id, reason="Contradicts root-tier policy fact", tenant=tenant, request_id=request_id)
            return {"verdict": "CHALLENGER_QUARANTINED", "quarantined_id": new_fact.id}

        if new_tier == Tier.ROOT and any((f.asserted_by.tier if f.asserted_by else Tier.NEW) != Tier.ROOT for f in conflicting_facts):
            quarantined = []
            for cf in conflicting_facts:
                cf_tier = cf.asserted_by.tier if cf.asserted_by else Tier.NEW
                if cf_tier != Tier.ROOT:
                    self.quarantine(store, cf.id, reason="Contradicted by root-tier assertion", tenant=tenant, request_id=request_id)
                    quarantined.append(cf.id)
            return {"verdict": "OLD_FACT_QUARANTINED", "quarantined_ids": quarantined, "quarantined_id": quarantined[0] if quarantined else old_fact.id}

        # Rule 2: Compare tier weights
        tier_weights = {Tier.ROOT: 10.0, Tier.ESTABLISHED: 3.0, Tier.NEW: 0.5}
        old_weight = tier_weights.get(old_tier, 0.5)
        new_weight = tier_weights.get(new_tier, 0.5)

        if new_weight >= 3 * old_weight:
            self.quarantine(store, old_fact.id, reason="Weaker assertion superseded by higher tier", tenant=tenant, request_id=request_id)
            return {"verdict": "OLD_FACT_QUARANTINED", "quarantined_id": old_fact.id}
        elif old_weight >= 3 * new_weight:
            self.quarantine(store, new_fact.id, reason="Weaker assertion rejected by established fact", tenant=tenant, request_id=request_id)
            return {"verdict": "CHALLENGER_QUARANTINED", "quarantined_id": new_fact.id}
        else:
            # Closer call -> REVIEW handoff for human
            handoff = Handoff(
                id=f"hand_review_{uuid.uuid4().hex[:6]}",
                kind=HandoffKind.REVIEW,
                task=f"Auditor: Contradiction between '{new_fact.id}' and '{old_fact.id}' for key '{new_fact.key}' requires human tie-break.",
                status=HandoffStatus.PENDING,
                context_root_id=new_fact.id,
            )
            store.add_handoff(handoff)
            event_bus.publish(
                tenant,
                "handoff_updated",
                {"handoff": handoff.model_dump()},
                request_id=request_id,
            )
            return {"verdict": "REVIEW_HANDOFF", "handoff_id": handoff.id}

    def verify_supersede_authority(self, old_fact: GraphNode, new_fact: GraphNode) -> bool:
        """
        Supersede authority: a Fact may supersede another only if asserted by a root-tier agent,
        or it is a newer version from the same Source.origin as the old fact.
        """
        if new_fact.asserted_by and new_fact.asserted_by.tier == Tier.ROOT:
            return True

        if old_fact.origin and new_fact.origin and old_fact.origin == new_fact.origin:
            return True

        return False

    def quarantine(self, store: GraphStore, root_id: str, reason: str = "Invalidated", tenant: str = "acme", request_id: Optional[str] = None) -> List[Dict[str, Any]]:
        # The one persisted diff includes quarantine, epoch, notices and compensation intent.
        with store._lock:
            affected = self._quarantine(store, root_id, reason, tenant, request_id)
            store.commit(request_id)
            return affected

    def _quarantine(
        self,
        store: GraphStore,
        root_id: str,
        reason: str = "Invalidated",
        tenant: str = "acme",
        request_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        # 1. Run cascade
        affected = store.cascade_quarantine(root_id, request_id=request_id)

        # 2. Emit status_changed events in order of depth so UI can animate wave
        for node_info in affected:
            event_bus.publish(
                tenant,
                "status_changed",
                {
                    "id": node_info["id"],
                    "status": Status.QUARANTINED.value,
                    "depth": node_info["depth"],
                    "root_id": root_id,
                },
                request_id=request_id,
            )

        # 3. Emit epoch_changed
        event_bus.publish(
            tenant,
            "epoch_changed",
            {"epoch": store.epoch, "reason": "quarantine"},
            request_id=request_id,
        )

        # 4. Enqueue compensations for executed actions
        for node_info in affected:
            node = store.nodes.get(node_info["id"])
            if node and node.kind == NodeKind.ACTION and node.action and node.action.executed:
                if node.action.reversibility == Reversibility.IRREVERSIBLE:
                    if node.action.correction_notice is not None:
                        continue
                    # Automated damage control: generate structured CorrectionNotice
                    from app.services.saga import generate_correction_notice
                    notice = generate_correction_notice(node, store, tenant=tenant)
                    node.action.correction_notice = notice
                    node.correction_notice = notice

                    # Store on an :Alert:MemoryNode in GraphStore
                    alert_id = f"alert_retraction_{node.id}"
                    alert_node = GraphNode(
                        id=alert_id,
                        kind=NodeKind.ALERT,
                        label=f"⚠️ Damage Control: Retraction for {node.id}",
                        text=notice["drafted_body"],
                        status=Status.HEALTHY,
                        stale=False,
                        owner_team=node.owner_team,
                        scope_team=node.scope_team,
                        scope_teams=list(node.scope_teams),
                        valid_from=int(time.time() * 1000),
                    )
                    if alert_id not in store.nodes:
                        store.add_node(alert_node, request_id=request_id)
                    store.add_edge(
                        GraphEdge(
                            id=f"e_{alert_id}",
                            source=alert_id,
                            target=node.id,
                            type=EdgeType.LEADS_TO,
                        ),
                        request_id=request_id,
                    )

                    # Escalation handoff for human with CorrectionNotice payload
                    handoff = Handoff(
                        id=f"hand_escl_{uuid.uuid4().hex[:6]}",
                        kind=HandoffKind.ESCALATION,
                        task=f"Irreversible action '{node.id}' quarantined! Automated damage control retraction notice drafted.",
                        status=HandoffStatus.PENDING,
                        context_root_id=node.id,
                        correction_notice=notice,
                    )
                    store.add_handoff(handoff)

                    event_bus.publish(tenant, "node_added", {"node": alert_node.model_dump()}, request_id=request_id)
                    event_bus.publish(tenant, "handoff_updated", {"handoff": handoff.model_dump()}, request_id=request_id)
                    event_bus.publish(tenant, "correction_notice_created", {"action_id": node.id, "notice": notice}, request_id=request_id)
                else:
                    # Enqueue compensation
                    if node.action.compensation is not None:
                        continue
                    node.action.compensation = CompensationInfo(
                        status=CompensationStatus.PENDING_COMPENSATION,
                        attempts=0,
                    )
                    event_bus.publish(
                        tenant,
                        "compensation_updated",
                        {"action_id": node.id, "status": CompensationStatus.PENDING_COMPENSATION.value, "attempts": 0},
                        request_id=request_id,
                    )

        return affected

    def restore(
        self,
        store: GraphStore,
        root_id: str,
        tenant: str = "acme",
        request_id: Optional[str] = None,
    ) -> List[str]:
        with store._lock:
            restored_ids = store.restore_node(root_id, request_id=request_id)

            for rid in restored_ids:
                event_bus.publish(
                    tenant,
                    "status_changed",
                    {"id": rid, "status": Status.HEALTHY.value},
                    request_id=request_id,
                )

            if restored_ids:
                event_bus.publish(
                    tenant,
                    "epoch_changed",
                    {"epoch": store.epoch, "reason": "restore"},
                    request_id=request_id,
                )
                store.commit(request_id)

            return restored_ids


auditor_service = AuditorService()
