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


# Word overlap (Jaccard) at which two facts count as one statement. Chosen after bench/corroboration_eval.py showed 0.8 missed a copy with
# only its prefix changed; it fails closed (a borderline pair goes to a person), and the genuine scenarios there stay trusted.
NEAR_DUPLICATE = 0.65


class QuorumGateService:
    """
    Evaluates the Quorum Gate at stage_action.
    - Reversible/compensable actions pass automatically.
    - Irreversible actions require:
      (a) No ancestor is stale.
      (b) EVERY leaf fact is either root-backed OR corroborated:
          Another healthy fact with the same key and value that is INDEPENDENT (different asserter, different source,
          not a verbatim copy; see independent()), with at least one asserted by an established-or-higher agent.
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
                handoff = self._create_review_handoff(store, action, reason, tenant, request_id=request_id)
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

    @staticmethod
    def _origin_label(fact: GraphNode) -> str:
        """The claimed source with the writer's own prefix removed: agent:scout:partner-feed and agent:comms:partner-feed are the same source."""
        o = (fact.origin or "").strip().lower()
        o = re.sub(r"^(agent|observer):[^:]*:", "", o) if o.startswith("agent:") else re.sub(r"^observer:", "", o)
        return o

    @staticmethod
    def _wording(fact: GraphNode) -> str:
        return re.sub(r"[^a-z0-9]+", " ", (fact.quote or fact.text or "").lower()).strip()

    @staticmethod
    def _lineage(store, fact: GraphNode) -> set:
        """The fact and everything it declares it was copied from, transitively."""
        seen: set = set()
        cur: Optional[GraphNode] = fact
        while cur is not None and cur.id not in seen:
            seen.add(cur.id)
            cur = store.nodes.get(cur.copied_from) if (store is not None and cur.copied_from) else None
        return seen

    @staticmethod
    def _host(store, fact: GraphNode) -> str:
        from urllib.parse import urlparse

        node = store.nodes.get(fact.source_ref) if (store is not None and fact.source_ref) else None
        url = (getattr(getattr(node, "source", None), "url", None) or getattr(getattr(fact, "source", None), "url", None) or "")
        return (urlparse(url).hostname or "").lower()

    @classmethod
    def independent(cls, a: GraphNode, b: GraphNode, store=None) -> bool:
        """
        Two facts corroborate each other only if they cannot be one claim counted twice. They are NOT independent when:
          - the same agent or member wrote both (an agent cannot corroborate itself, whatever label it puts on each write);
          - one declares it was copied from the other, or both descend from one declared ancestor (`copied_from`, followed transitively);
          - they cite the same source node, or source nodes on the same host;
          - their claimed source is the same once the writer's own prefix is stripped;
          - their wording is identical or almost identical (a verbatim or lightly edited copy is one claim, however many places repeat it).
        What this cannot see: a reworded copy with no declared lineage, no shared source, a different owner and different wording.
        Provenance Quorum is shown is all it can check; that boundary is stated in docs/HACKATHON.md and measured in bench/corroboration_eval.py.
        """
        if a.asserted_by and b.asserted_by and a.asserted_by.id == b.asserted_by.id:
            return False
        if cls._lineage(store, a) & cls._lineage(store, b):
            return False
        if a.source_ref and b.source_ref and a.source_ref == b.source_ref:
            return False
        ha, hb = cls._host(store, a), cls._host(store, b)
        if ha and hb and ha == hb:
            return False
        la, lb = cls._origin_label(a), cls._origin_label(b)
        if la and lb and la == lb:
            return False
        wa, wb = cls._wording(a), cls._wording(b)
        if wa and wb:
            if wa == wb:
                return False
            ta, tb = set(wa.split()), set(wb.split())
            if len(ta | tb) >= 4 and len(ta & tb) / len(ta | tb) >= NEAR_DUPLICATE:
                return False
        return True

    def _find_corroborator(self, store: GraphStore, target_fact: GraphNode, require_independence: bool = True) -> Optional[GraphNode]:
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

        with store._lock:
            all_nodes = list(store.nodes.values())

        for node in all_nodes:
            if node.id == target_fact.id or node.kind != NodeKind.FACT or node.status != Status.HEALTHY or getattr(node, "stale", False):
                continue

            if not set(inherited_scopes([node])) <= set(inherited_scopes([target_fact])):
                continue

            node_val_norm = re.sub(r"\s+", " ", str(node.value).strip().lower()) if node.value else ""
            if node.key == target_fact.key and node_val_norm == target_val_norm:
                node_ref = store.nodes.get(node.source_ref) if node.source_ref else None
                node_origin = node.origin or getattr(node_ref, "origin", None) or node.source_ref
                # Corroboration must be independent: a different origin label is not enough (see independent())
                if node_origin and target_origin and node_origin != target_origin and (not require_independence or self.independent(target_fact, node, store)):
                    candidate_tier = node.asserted_by.tier if node.asserted_by else Tier.NEW
                    # Check at least one is established or root
                    if target_tier in [Tier.ROOT, Tier.ESTABLISHED, "root", "established"] or candidate_tier in [Tier.ROOT, Tier.ESTABLISHED, "root", "established"]:
                        return node

        return None

    def _create_review_handoff(self, store: GraphStore, action: GraphNode, reason: str, tenant: str, request_id: Optional[str] = None) -> Handoff:
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
            request_id=request_id,
        )
        return handoff


quorum_gate = QuorumGateService()
