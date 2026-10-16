import time
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from app.auth import get_auth_context, AuthContext, require_role
from app.models.handoffs import Handoff, ReviewRequest, ReviewDecision
from app.models.common import ErrorCode, HandoffStatus, HandoffKind, Status
from app.graph.queries import get_graph_store
from app.services.event_bus import event_bus

from app.services.access import handoff_visible
from app.services.workspace import viewer_for

router = APIRouter()


@router.get("/handoffs", response_model=List[Handoff])
def list_handoffs(auth: AuthContext = Depends(get_auth_context)):
    store = get_graph_store(auth.tenant)
    with store.handoff_lock:
        return [h for h in store.handoffs if handoff_visible(store, viewer_for(store, auth.member_id), h)]


@router.post("/handoffs/{handoff_id}/claim", response_model=Handoff)
def claim_handoff(
    handoff_id: str,
    auth: AuthContext = Depends(get_auth_context),
):
    store = get_graph_store(auth.tenant)
    with store.handoff_lock:
        target = next((h for h in store.handoffs if h.id == handoff_id), None)
        if not target or not handoff_visible(store, viewer_for(store, auth.member_id), target):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": ErrorCode.NOT_FOUND.value, "message": f"Handoff '{handoff_id}' not found."}},
            )

        # Role enforcement
        if auth.role == "agent" and target.kind != HandoffKind.TASK:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": {
                        "code": ErrorCode.FORBIDDEN_ROLE.value,
                        "message": f"Agents may only claim TASK handoffs. Handoff '{handoff_id}' is kind '{target.kind.value}'.",
                    }
                },
            )

        now_ms = int(time.time() * 1000)

        # Atomic lease check (Brief Section 5.8)
        # Allowed if PENDING or if IN_PROGRESS with expired lease
        anchor_ok = True
        if target.kind == HandoffKind.TASK and target.context_root_id:
            anchor = store.nodes.get(target.context_root_id)
            if anchor and anchor.status != Status.HEALTHY:
                anchor_ok = False

        is_claimable = anchor_ok and (
            target.status == HandoffStatus.PENDING
            or target.status == HandoffStatus.TIMED_OUT
            or (target.status == HandoffStatus.IN_PROGRESS and target.lease_until is not None and target.lease_until < now_ms)
        )

        if not is_claimable:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": {
                        "code": ErrorCode.CONFLICT.value,
                        "message": f"Handoff '{handoff_id}' already claimed by '{target.claimed_by}' with active lease until {target.lease_until}." if not anchor_ok else f"Anchor node for handoff '{handoff_id}' is not HEALTHY.",
                    }
                },
            )

        claimer = auth.agent_id or auth.role
        target.status = HandoffStatus.IN_PROGRESS
        target.claimed_by = claimer
        target.claimed_at = now_ms
        target.lease_until = now_ms + 60000

        event_bus.publish(
            auth.tenant,
            "handoff_claimed",
            {
                "handoff_id": target.id,
                "winner": claimer,
                "losers": ["agent_refunder"] if claimer != "agent_refunder" else ["agent_analyst"],
                "lease_until": target.lease_until,
            },
        )
        event_bus.publish(
            auth.tenant,
            "handoff_updated",
            {"handoff": target.model_dump()},
        )

        return target


@router.post("/handoffs/{handoff_id}/review", response_model=Handoff)
def review_handoff(
    handoff_id: str,
    body: ReviewRequest,
    auth: AuthContext = Depends(require_role(["human"])),
):
    store = get_graph_store(auth.tenant)
    with store._lock:
        with store.handoff_lock:
            target = next((h for h in store.handoffs if h.id == handoff_id), None)
            if not target or not handoff_visible(store, viewer_for(store, auth.member_id), target):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"error": {"code": ErrorCode.NOT_FOUND.value, "message": f"Handoff '{handoff_id}' not found."}},
                )

            target.status = HandoffStatus.COMPLETED
            note_val = body.note or body.notes
            if note_val:
                target.note = note_val

            if body.decision in [ReviewDecision.APPROVE, "approve", "APPROVE"] and target.context_root_id:
                from app.models.common import GateStatus
                action_node = store.nodes.get(target.context_root_id)
                if action_node and action_node.action:
                    action_node.action.gate = GateStatus.PASSED
                    notice = action_node.action.correction_notice
                    if notice:
                        notice["status"] = "APPROVED_PENDING_DELIVERY"
                        action_node.correction_notice = notice
                    event_bus.publish(auth.tenant, "node_updated", {"node": action_node.model_dump()})
                if target.correction_notice:
                    target.correction_notice["status"] = "APPROVED_PENDING_DELIVERY"

                alert_node = store.nodes.get(f"alert_retraction_{target.context_root_id}")
                if alert_node:
                    alert_node.label = f"Retraction approved, delivery pending: {target.context_root_id}"
                    event_bus.publish(auth.tenant, "node_updated", {"node": alert_node.model_dump()})

        event_bus.publish(
            auth.tenant,
            "handoff_updated",
            {"handoff": target.model_dump()},
        )

        return target
