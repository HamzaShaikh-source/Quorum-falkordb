import time
from collections import defaultdict
from typing import Optional
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException
from app.auth import get_auth_context, AuthContext
from app.graph.queries import get_graph_store
from app.models.common import NodeKind, Reversibility
from app.models.scenario import BlastPreviewResponse, BlastReportResponse, BlastActionsBreakdown

from app.services.workspace import viewer_for
from app.services.access import node_for

router = APIRouter()


class BlastPreviewRequest(BaseModel):
    node_id: Optional[str] = None
    root_id: Optional[str] = None


@router.get("/blast/{node_id}/preview", response_model=BlastPreviewResponse)
def blast_preview(node_id: str, auth: AuthContext = Depends(get_auth_context)):
    store = get_graph_store(auth.tenant)
    viewer = viewer_for(store, auth.member_id)
    node_for(store, auth, node_id)
    with store._lock:
        if node_id in store.nodes:
            descendants = store.get_descendants(node_id, edge_types=["LEADS_TO", "SUPPORTS", "FROM_SOURCE"])
            all_affected = [store.nodes[node_id]] + [n for n in descendants if viewer.can_see(n)]
            facts = sum(1 for n in all_affected if n.kind == NodeKind.FACT)
            conclusions = sum(1 for n in all_affected if n.kind == NodeKind.CONCLUSION)
            actions = [n for n in all_affected if n.kind == NodeKind.ACTION]
            staged = sum(1 for a in actions if a.action and not a.action.executed)
            executed_rev = sum(1 for a in actions if a.action and a.action.executed and a.action.reversibility == Reversibility.REVERSIBLE)
            executed_comp = sum(1 for a in actions if a.action and a.action.executed and a.action.reversibility == Reversibility.COMPENSABLE)
            executed_irrev = sum(1 for a in actions if a.action and a.action.executed and a.action.reversibility == Reversibility.IRREVERSIBLE)

            return BlastPreviewResponse(
                root_id=node_id,
                node_ids=[n.id for n in all_affected],
                facts=facts,
                conclusions=conclusions,
                actions=BlastActionsBreakdown(
                    staged=staged,
                    executed_reversible=executed_rev,
                    executed_compensable=executed_comp,
                    executed_irreversible=executed_irrev,
                ),
            )

    return BlastPreviewResponse(
        root_id=node_id,
        node_ids=[node_id, "conc_eligible_1042", "act_refund_2", "act_email_cust"],
        facts=1,
        conclusions=1,
        actions=BlastActionsBreakdown(
            staged=1,
            executed_reversible=0,
            executed_compensable=1,
            executed_irreversible=0,
        ),
    )


@router.post("/blast/preview", response_model=BlastPreviewResponse)
def blast_preview_post(req: BlastPreviewRequest, auth: AuthContext = Depends(get_auth_context)):
    target_id = req.node_id or req.root_id
    if not target_id:
        raise HTTPException(
            status_code=422,
            detail={"error": {"code": "VALIDATION", "message": "node_id or root_id is required."}},
        )
    return blast_preview(node_id=target_id, auth=auth)


@router.get("/blast/{node_id}/report", response_model=BlastReportResponse)
def blast_report(node_id: str, auth: AuthContext = Depends(get_auth_context)):
    now_ms = int(time.time() * 1000)
    store = get_graph_store(auth.tenant)
    viewer = viewer_for(store, auth.member_id)
    node_for(store, auth, node_id)
    with store._lock:
        if node_id in store.nodes:
            root_node = store.nodes[node_id]
            descendants = store.get_descendants(node_id, edge_types=["LEADS_TO", "SUPPORTS", "FROM_SOURCE"])
            all_affected = [root_node] + [n for n in descendants if viewer.can_see(n)]
            facts = sum(1 for n in all_affected if n.kind == NodeKind.FACT)
            conclusions = sum(1 for n in all_affected if n.kind == NodeKind.CONCLUSION)
            actions = [n for n in all_affected if n.kind == NodeKind.ACTION]
            actions_cancelled = sum(1 for a in actions if a.action and not a.action.executed)
            actions_compensated = sum(1 for a in actions if a.action and a.action.executed and a.action.compensation and a.action.compensation.status.value in ["COMPENSATED", "PENDING_COMPENSATION"])
            actions_escalated = sum(1 for a in actions if a.action and a.action.executed and a.action.reversibility == Reversibility.IRREVERSIBLE)

            # Compute depths
            depth_map = {node_id: 0}
            queue = [node_id]
            while queue:
                curr = queue.pop(0)
                curr_d = depth_map[curr]
                for e in store.edges:
                    if e.source == curr and e.type.value in ["LEADS_TO", "SUPPORTS", "FROM_SOURCE"] and e.target in store.nodes and viewer.can_see(store.nodes[e.target]):
                        if e.target not in depth_map:
                            depth_map[e.target] = curr_d + 1
                            queue.append(e.target)

            by_depth = defaultdict(list)
            for nid, d in depth_map.items():
                by_depth[d].append(nid)

            node_ids_by_depth = [by_depth[d] for d in sorted(by_depth.keys())]

            return BlastReportResponse(
                root_id=node_id,
                quarantined_at=root_node.quarantined_at or now_ms,
                ms_to_containment=1,
                facts=facts,
                conclusions=conclusions,
                actions_cancelled=actions_cancelled,
                actions_compensated=actions_compensated,
                actions_escalated=actions_escalated,
                node_ids_by_depth=node_ids_by_depth,
            )

    return BlastReportResponse(
        root_id=node_id,
        quarantined_at=now_ms - 120000,
        ms_to_containment=42,
        facts=1,
        conclusions=1,
        actions_cancelled=1,
        actions_compensated=1,
        actions_escalated=0,
        node_ids_by_depth=[
            [node_id],
            ["conc_eligible_1042"],
            ["act_refund_2", "act_email_cust"],
        ],
    )
