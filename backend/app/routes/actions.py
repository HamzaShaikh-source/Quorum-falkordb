import uuid
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from app.auth import get_auth_context, AuthContext, require_role
from app.graph.queries import get_graph_store
from app.models.graph import GraphNode, ActionInfo, GraphEdge
from app.models.common import (
    NodeKind,
    Status,
    Reversibility,
    GateStatus,
    EdgeType,
    ErrorCode,
    HandoffStatus,
    HandoffKind,
)
from app.services.gate import quorum_gate
from app.services.executor import action_executor
from app.services.access import inherited_scopes, node_for, creation_matches
from app.services.workspace import viewer_for

router = APIRouter()


class StageActionRequest(BaseModel):
    id: Optional[str] = None
    label: str
    action_type: str
    reversibility: Reversibility
    parent_ids: List[str]
    procedure_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)


@router.get("/actions", response_model=List[GraphNode])
def list_actions(auth: AuthContext = Depends(get_auth_context)):
    store = get_graph_store(tenant=auth.tenant)
    viewer = viewer_for(store, auth.member_id)
    actions = [n for n in store.nodes.values() if n.kind == NodeKind.ACTION and viewer.can_see(n)]
    return actions


@router.post("/actions/stage", response_model=GraphNode)
def stage_action(
    req: StageActionRequest,
    auth: AuthContext = Depends(get_auth_context),
):
    store = get_graph_store(tenant=auth.tenant)

    with store._lock:
        # 1. Atomic derivation check: all parents must exist and be HEALTHY
        parents = [node_for(store, auth, pid) for pid in req.parent_ids]
        if not parents or any(p.status != Status.HEALTHY or p.stale for p in parents):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": {
                        "code": ErrorCode.CONFLICT.value,
                        "message": "Atomic derivation guard failed: one or more parents do not exist or are not HEALTHY.",
                    }
                },
            )

        action_id = req.id or f"act_{uuid.uuid4().hex[:6]}"
        action_node = GraphNode(
            id=action_id,
            kind=NodeKind.ACTION,
            label=req.label[:60],
            status=Status.HEALTHY,
            stale=False,
            scope_team=next((p.scope_team for p in parents if p.scope_team), None),
            scope_teams=inherited_scopes(parents),
            action=ActionInfo(
                type=req.action_type,
                reversibility=req.reversibility,
                executed=False,
                confirmed=False,
                gate=GateStatus.BLOCKED,  # Evaluated by gate below
                procedure_id=req.procedure_id,
                payload=req.payload,
            ),
        )
        if req.idempotency_key and action_node.action is not None:
            action_node.action.idempotency_key = req.idempotency_key

        # Add node and causal edges
        existing = store.nodes.get(action_id)
        if existing and viewer_for(store, auth.member_id).can_see(existing) and creation_matches(store, existing, action_node, parents):
            return existing
        store.add_node(action_node)
        for p in parents:
            store.add_edge(GraphEdge(
                id=f"e_{uuid.uuid4().hex[:6]}",
                source=p.id,
                target=action_node.id,
                type=EdgeType.LEADS_TO,
            ))

    # 2. Evaluate Quorum Gate
    gate_status, reason, handoff = quorum_gate.evaluate(store, action_node, tenant=auth.tenant)

    return action_node


@router.post("/actions/{action_id}/execute", response_model=GraphNode)
def execute_action_endpoint(
    action_id: str,
    auth: AuthContext = Depends(get_auth_context),
):
    store = get_graph_store(tenant=auth.tenant)
    node_for(store, auth, action_id)
    return action_executor.execute(store, action_id, tenant=auth.tenant)


@router.post("/actions/{action_id}/approve-correction")
def approve_correction(
    action_id: str,
    auth: AuthContext = Depends(require_role(["human"])),
):
    store = get_graph_store(tenant=auth.tenant)
    node_for(store, auth, action_id)
    from app.services.event_bus import event_bus

    with store._lock:
        action_node = store.nodes.get(action_id)
        if not action_node or not action_node.action:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": ErrorCode.NOT_FOUND.value, "message": f"Action '{action_id}' not found."}},
            )

        notice = getattr(action_node.action, "correction_notice", None)
        if not notice:
            from app.services.saga import generate_correction_notice
            notice = generate_correction_notice(action_node, store, tenant=auth.tenant)

        notice["status"] = "APPROVED_PENDING_DELIVERY"
        action_node.action.correction_notice = notice
        action_node.correction_notice = notice

        # Update alert memory node if present
        alert_node = store.nodes.get(f"alert_retraction_{action_id}")
        if alert_node:
            alert_node.label = f"Retraction approved, delivery pending: {action_id}"[:120]
            event_bus.publish(auth.tenant, "node_updated", {"node": alert_node.model_dump()})

    with store.handoff_lock:
        for h in store.handoffs:
            if (h.context_root_id == action_id or (h.correction_notice and h.correction_notice.get("target_action_id") == action_id)) and h.kind == HandoffKind.ESCALATION:
                h.status = HandoffStatus.COMPLETED
                if h.correction_notice:
                    h.correction_notice["status"] = "APPROVED_PENDING_DELIVERY"
                event_bus.publish(auth.tenant, "handoff_updated", {"handoff": h.model_dump()})

    event_bus.publish(
        auth.tenant,
        "correction_approved",
        {"action_id": action_id, "status": "APPROVED_PENDING_DELIVERY", "notice": notice},
    )

    return {
        "status": "APPROVED_PENDING_DELIVERY",
        "action_id": action_id,
        "notice": notice,
    }

