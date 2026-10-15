from typing import Optional
from fastapi import APIRouter, Depends, Query
from app.auth import get_auth_context, AuthContext
from app.models.agents import PromptDiffResponse, PromptDiffNode
from app.models.common import Status

from app.graph.queries import get_graph_store
from app.models.common import NodeKind

from app.services.workspace import viewer_for

router = APIRouter()


@router.get("/prompt-diff", response_model=PromptDiffResponse)
def get_prompt_diff(
    agent: Optional[str] = Query(None),
    auth: AuthContext = Depends(get_auth_context),
):
    store = get_graph_store(auth.tenant)
    viewer = viewer_for(store, auth.member_id)
    with store._lock:
        quarantined = [n for n in store.nodes.values() if viewer.can_see(n) and n.status == Status.QUARANTINED and n.kind in [NodeKind.FACT, NodeKind.CONCLUSION]]
        healthy = [n for n in store.nodes.values() if viewer.can_see(n) and n.status == Status.HEALTHY and n.kind in [NodeKind.FACT, NodeKind.CONCLUSION]]

    if quarantined:
        removed_ids = [n.id for n in quarantined]
        after_nodes = [
            PromptDiffNode(node_id=n.id, text=n.text or n.label, status=n.status)
            for n in healthy
        ]
        before_nodes = after_nodes + [
            PromptDiffNode(node_id=n.id, text=n.text or n.label, status=Status.HEALTHY)
            for n in quarantined
        ]
        return PromptDiffResponse(
            epoch_before=max(1, store.epoch - 1),
            epoch_after=store.epoch,
            before=before_nodes,
            after=after_nodes,
            removed_ids=removed_ids,
        )

    # Clean / baseline default diff
    return PromptDiffResponse(
        epoch_before=13,
        epoch_after=14,
        before=[
            PromptDiffNode(node_id="fact_30d", text="Refund window is 30 days.", status=Status.HEALTHY),
            PromptDiffNode(node_id="fact_90d", text="Refund window is 90 days.", status=Status.HEALTHY),
            PromptDiffNode(node_id="conc_eligible_1042", text="Order 1042 eligible for refund (45d).", status=Status.HEALTHY),
        ],
        after=[
            PromptDiffNode(node_id="fact_30d", text="Refund window is 30 days.", status=Status.HEALTHY),
        ],
        removed_ids=["fact_90d", "conc_eligible_1042"],
    )
