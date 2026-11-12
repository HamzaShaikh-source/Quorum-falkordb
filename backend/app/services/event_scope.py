"""Which live events a scoped viewer may receive. Unscoped callers (demo and service tokens) get everything."""
from typing import Any, Dict

from app.services.context_pack import Viewer


def _visible_node_id(store, viewer: Viewer, node_id: str) -> bool:
    n = store.nodes.get(node_id)
    return n is not None and viewer.can_see(n)


def event_visible(store, viewer: Viewer, ev) -> bool:
    if not viewer.scoped:
        return True
    t, d = ev.type, (ev.data or {})
    if t in ("node_added", "node_updated"):
        node: Dict[str, Any] = d.get("node") or {}
        scopes = set(node.get("scope_teams") or []) | ({node["scope_team"]} if node.get("scope_team") else set())
        return scopes <= ({viewer.team_id} | viewer.linked_teams)
    if t == "edge_added":
        e = d.get("edge") or {}
        return _visible_node_id(store, viewer, e.get("source", "")) and _visible_node_id(store, viewer, e.get("target", ""))
    if t == "status_changed":
        return _visible_node_id(store, viewer, d.get("id", ""))
    if t in ("handoff_updated", "handoff_claimed"):
        from app.services.access import handoff_visible
        hid = (d.get("handoff") or {}).get("id") or d.get("handoff_id")
        handoff = next((h for h in store.handoffs if h.id == hid), None)
        return handoff is not None and handoff_visible(store, viewer, handoff)
    if d.get("action_id"):
        return _visible_node_id(store, viewer, d["action_id"])
    if t == "mcp_call":
        from app.services.access import call_visible
        return call_visible(store, viewer, d.get("call") or {})
    return t in ("epoch_changed", "workspace_changed", "scenario_progress", "heartbeat")
