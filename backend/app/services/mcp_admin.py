"""Auditor tools: quarantine, restore, explain_blast_radius."""
from typing import Any, Dict

from app.graph.queries import get_graph_store
from app.services.access import node_for
from fastapi import HTTPException
from app.services import roles
from app.services.auditor import auditor_service
from app.services.mcp_guard import AuthError, _get_auth, effective_token, emit as _emit, guarded, viewer_of as _viewer  # noqa: F401


def quarantine(root_id: str, token: str = "", reason: str = "Manual auditor quarantine") -> Dict[str, Any]:
    """Auditor-only tool: Quarantines root fact and cascades down causal descendants."""
    auth = _get_auth(token)
    if auth["role"] not in ["auditor", "human"]:
        return {"error": roles.refusal(auth.get("agent_id") or "This token", auth["role"], "quarantine facts", "auditor", "human"), "success": False}

    store = get_graph_store(auth["tenant"])
    try:
        node_for(store, auth, root_id)
    except HTTPException:
        return {"error": "Node not found.", "success": False}
    affected = auditor_service.quarantine(store, root_id, reason=reason, tenant=auth["tenant"])
    affected = [item for item in affected if _viewer(auth, store).can_see(store.nodes[item["id"]])]
    return {
        "epoch": store.epoch,
        "root_id": root_id,
        "affected_count": len(affected),
        "affected_nodes": affected,
        "success": True,
    }


def restore(root_id: str, token: str = "") -> Dict[str, Any]:
    """Auditor-only tool: Restores root fact and valid descendants if all parents are healthy."""
    auth = _get_auth(token)
    if auth["role"] not in ["auditor", "human"]:
        return {"error": roles.refusal(auth.get("agent_id") or "This token", auth["role"], "restore facts", "auditor", "human"), "success": False}

    store = get_graph_store(auth["tenant"])
    try:
        node_for(store, auth, root_id)
    except HTTPException:
        return {"error": "Node not found.", "success": False}
    restored_ids = auditor_service.restore(store, root_id, tenant=auth["tenant"])
    restored_ids = [nid for nid in restored_ids if _viewer(auth, store).can_see(store.nodes[nid])]
    return {
        "epoch": store.epoch,
        "root_id": root_id,
        "restored_count": len(restored_ids),
        "restored_nodes": restored_ids,
        "success": True,
    }


def explain_blast_radius(root_id: str, token: str = "") -> Dict[str, Any]:
    """Auditor tool: Dry-run blast radius calculation."""
    auth = _get_auth(token)
    store = get_graph_store(auth["tenant"])
    try:
        node_for(store, auth, root_id)
    except HTTPException:
        return {"error": "Node not found.", "success": False}
    with store._lock:
        descendants = store.get_descendants(root_id, edge_types=["LEADS_TO", "SUPPORTS"])
        descendants = [n for n in descendants if _viewer(auth, store).can_see(n)]
    return {
        "epoch": store.epoch,
        "root_id": root_id,
        "predicted_impact_count": len(descendants),
        "predicted_impact_nodes": [d.id for d in descendants if _viewer(auth, store).can_see(d)],
    }
