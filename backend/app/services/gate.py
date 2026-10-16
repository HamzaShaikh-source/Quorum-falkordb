import re
import uuid
from typing import Tuple, Optional
from app.graph.queries import GraphStore
from app.models.common import (
    NodeKind,
    Reversibility,
    GateStatus,
    Tier,
    HandoffKind,
    HandoffStatus,
    EdgeType,
    Status,
)
from app.models.graph import GraphNode, GraphEdge
from app.models.handoffs import Handoff
from app.services.event_bus import event_bus
from app.services.access import inherited_scopes


class QuorumGateService:
    """
    Evaluates the Quorum Gate at stage_action.
    - Reversible/compensable actions pass automatically.
    - Irreversible actions require:
      (a) No ancestor is stale.
      (b) EVERY leaf fact is either root-backed OR corroborated:
          Another healthy fact with the same key and value from a Source with a DIFFERENT origin,
          with at least one asserted by an established-or-higher agent.
    - On pass via corroboration: creates (corroborator)-[:SUPPORTS]->(action).
    - On block: creates a REVIEW handoff anchored to the action.
    """
    def evaluate(
        self,
        store: GraphStore,
        action: GraphNode,
        tenant: str = "acme",
        request_id: Optional[str] = None,
    ) -> Tuple[GateStatus, Optional[str], Optional[Handoff]]:
        if not action.action:
            return GateStatus.PASSED, None, None

        # Reversible or compensable actions pass immediately
        if action.action.reversibility in [Reversibility.REVERSIBLE, Reversibility.COMPENSABLE]:
            action.action.gate = GateStatus.PASSED
            return GateStatus.PASSED, None, None

        # 1. Stale or non-healthy ancestor check
        ancestors = store.get_ancestors(action.id, edge_types=["LEADS_TO", "SUPPORTS"])
        unhealthy_ancestors = [a for a in ancestors if a.stale or a.status != Status.HEALTHY]
        if unhealthy_ancestors:
            action.action.gate = GateStatus.BLOCKED
            reason = f"Blocked: Action has non-healthy or stale ancestor '{unhealthy_ancestors[0].id}'."
            handoff = self._create_review_handoff(store, action, reason, tenant)
            return GateStatus.BLOCKED, reason, handoff

        # 2. Leaf fact inspection
        leaf_facts = store.get_leaf_facts(action.id)
        if not leaf_facts:
            # Action has no causal leaf facts; block for safety
            action.action.gate = GateStatus.BLOCKED
            reason = "Blocked: Action has no causal leaf facts to verify."
            handoff = self._create_review_handoff(store, action, reason, tenant)
            return GateStatus.BLOCKED, reason, handoff

        corroborating_edges_to_add = []

        for leaf in leaf_facts:
            # Condition A: Root-backed and healthy
            if leaf.asserted_by and leaf.asserted_by.tier == Tier.ROOT and leaf.status == Status.HEALTHY and not leaf.stale:
                continue

            # Condition B: Corroborated
            corroborator = self._find_corroborator(store, leaf)
            if corroborator:
                corroborating_edges_to_add.append(
                    GraphEdge(
                        id=f"supp_{uuid.uuid4().hex[:6]}",
                        source=corroborator.id,
                        target=action.id,
                        type=EdgeType.SUPPORTS,
                    )
                )
            else:
                # Leaf failed both conditions -> Gate blocked!
                action.action.gate = GateStatus.BLOCKED
                reason = f"Blocked: Irreversible action rests on uncorroborated leaf fact '{leaf.id}' (origin: {leaf.origin or 'unknown'})."
                handoff = self._create_review_handoff(store, action, reason, tenant)
                return GateStatus.BLOCKED, reason, handoff

        # All leaf facts passed! Add SUPPORTS edges and pass gate
        action.action.gate = GateStatus.PASSED
        seen_corroborators = set()
        for edge in corroborating_edges_to_add:
            if edge.source in seen_corroborators:
                continue
            seen_corroborators.add(edge.source)
            if not any(e.source == edge.source and e.target == edge.target and e.type == edge.type for e in store.edges):
                store.add_edge(edge, request_id)

        return GateStatus.PASSED, None, None

    def _find_corroborator(self, store: GraphStore, target_fact: GraphNode) -> Optional[GraphNode]:
        """
        Finds another HEALTHY fact with:
        - Same key and normalized value
        - Source origin DIFFERENT from target_fact.origin
        - At least one asserted by established-or-higher agent
        """
        if not target_fact.key or not target_fact.value:
            return None

        target_tier = target_fact.asserted_by.tier if target_fact.asserted_by else Tier.NEW
        target_val_norm = re.sub(r"\s+", " ", str(target_fact.value).strip().lower())
        ref_node = store.nodes.get(target_fact.source_ref) if target_fact.source_ref else None
        target_origin = target_fact.origin or getattr(ref_node, "origin", None) or target_fact.source_ref

        for node in store.nodes.values():
            if node.id == target_fact.id or node.kind != NodeKind.FACT or node.status != Status.HEALTHY or getattr(node, "stale", False):
                continue

            if not set(inherited_scopes([node])) <= set(inherited_scopes([target_fact])):
                continue

            node_val_norm = re.sub(r"\s+", " ", str(node.value).strip().lower()) if node.value else ""
            if node.key == target_fact.key and node_val_norm == target_val_norm:
                node_ref = store.nodes.get(node.source_ref) if node.source_ref else None
                node_origin = node.origin or getattr(node_ref, "origin", None) or node.source_ref
                # Check different origin
                if node_origin and target_origin and node_origin != target_origin:
                    candidate_tier = node.asserted_by.tier if node.asserted_by else Tier.NEW
                    # Check at least one is established or root
                    if target_tier in [Tier.ROOT, Tier.ESTABLISHED, "root", "established"] or candidate_tier in [Tier.ROOT, Tier.ESTABLISHED, "root", "established"]:
                        return node

        return None

    def _create_review_handoff(self, store: GraphStore, action: GraphNode, reason: str, tenant: str) -> Handoff:
        # Check if an existing pending review handoff already exists for this action to guarantee idempotency
        with store.handoff_lock:
            existing = next(
                (h for h in store.handoffs if h.context_root_id == action.id and h.kind == HandoffKind.REVIEW and h.status == HandoffStatus.PENDING),
                None,
            )
            if existing:
                return existing

        handoff = Handoff(
            id=f"hand_gate_{uuid.uuid4().hex[:6]}",
            kind=HandoffKind.REVIEW,
            task=f"Quorum Gate Review: {reason}",
            status=HandoffStatus.PENDING,
            context_root_id=action.id,
        )
        store.add_handoff(handoff)
        event_bus.publish(
            tenant,
            "gate_blocked",
            {"action_id": action.id, "handoff_id": handoff.id, "reason": reason},
        )
        return handoff


quorum_gate = QuorumGateService()
