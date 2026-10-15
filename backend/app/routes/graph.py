from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from app.auth import get_auth_context, AuthContext
from app.models.graph import GraphResponse, NodeDetailResponse, StatusHistoryEntry
from app.models.common import ErrorCode, Status

from app.graph.queries import get_graph_store
from app.config import settings
from app.services import workspace as ws
from app.services.event_bus import event_bus
import time

router = APIRouter()


@router.get("/graph", response_model=GraphResponse)
def get_graph(as_of: Optional[int] = None, auth: AuthContext = Depends(get_auth_context)):
    store = get_graph_store(tenant=auth.tenant)
    now_ms = int(time.time() * 1000)
    if as_of is not None:
        nodes = store.get_as_of(as_of)
    else:
        nodes = list(store.nodes.values())

    viewer = ws.viewer_for(store, auth.member_id)
    nodes = [n for n in nodes if viewer.can_see(n)]
    node_ids = {n.id for n in nodes}
    edges = [e for e in store.edges if e.source in node_ids and e.target in node_ids]

    return GraphResponse(
        contract_version=settings.CONTRACT_VERSION,
        tenant=auth.tenant,
        epoch=store.epoch,
        # Must match the SSE stream's numbering, or a client that opens the stream from this seq replays or misses events.
        seq=event_bus.get_tenant_buffer(auth.tenant).current_seq,
        as_of=as_of,
        generated_at=now_ms,
        truncated=False,
        nodes=nodes,
        edges=edges,
    )


@router.get("/node/{node_id}", response_model=NodeDetailResponse)
def get_node(node_id: str, auth: AuthContext = Depends(get_auth_context)):
    store = get_graph_store(tenant=auth.tenant)
    viewer = ws.viewer_for(store, auth.member_id)
    target = None
    with store._lock:
        cand = store.nodes.get(node_id)
        if cand is not None and viewer.can_see(cand):
            target = cand
            edges = list(store.edges)
            all_nodes = {i: n for i, n in store.nodes.items() if viewer.can_see(n)}
            base_ts = target.valid_from or int(time.time() * 1000)

    if not target:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": ErrorCode.NOT_FOUND.value,
                    "message": f"Node with id '{node_id}' not found.",
                }
            },
        )

    # Compute provenance path to root
    path = [target]
    curr_id = node_id
    visited_ids = {curr_id}
    while True:
        parent_edge = next((e for e in edges if e.target == curr_id and (e.type.value in ["LEADS_TO", "FROM_SOURCE"] if hasattr(e.type, 'value') else str(e.type) in ["LEADS_TO", "FROM_SOURCE"])), None)
        if not parent_edge or parent_edge.source in visited_ids:
            break
        parent_node = all_nodes.get(parent_edge.source)
        if not parent_node:
            break
        path.insert(0, parent_node)
        visited_ids.add(parent_node.id)
        curr_id = parent_node.id

    # Descendants count
    descendant_count = sum(1 for e in edges if e.source == node_id)

    status_history = [
        StatusHistoryEntry(
            ts=target.valid_from or base_ts,
            status=Status.HEALTHY,
            reason="Node created from source",
        )
    ]
    if target.status == Status.QUARANTINED:
        status_history.append(
            StatusHistoryEntry(
                ts=target.quarantined_at or base_ts,
                status=Status.QUARANTINED,
                reason=f"Cascade invalidation from root {target.quarantine_root or 'poison'}",
            )
        )
    elif target.status == Status.SUPERSEDED:
        status_history.append(
            StatusHistoryEntry(
                ts=base_ts,
                status=Status.SUPERSEDED,
                reason="Superseded by verified newer policy",
            )
        )

    return NodeDetailResponse(
        node=target,
        path=path,
        descendant_count=descendant_count,
        status_history=status_history,
    )
