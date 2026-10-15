from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query

from app.auth import AuthContext, get_auth_context
from app.graph.queries import get_graph_store

from app.services import context_pack as cp
from app.services import workspace as ws
from app.services.access import call_visible

router = APIRouter()


def visible_calls(store, auth: AuthContext) -> List[Dict[str, Any]]:
    """Unscoped callers (demo and service tokens) see every call. A member sees calls by their own team and linked teams."""
    viewer = ws.viewer_for(store, auth.member_id)
    with store._lock:
        calls = list(store.calls)
    if not viewer.scoped:
        return calls
    return [c for c in calls if call_visible(store, viewer, c)]


def stats_for(calls: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Savings are counted only on calls that report a baseline (the read tools). Per call it can be negative; it is not clipped."""
    measured = [c for c in calls if c.get("baseline", 0) > 0 and c.get("ok", True)]
    tokens = sum(c["tokens"] for c in measured)
    baseline = sum(c["baseline"] for c in measured)

    def agg(key: str) -> List[Dict[str, Any]]:
        d: Dict[str, Dict[str, Any]] = {}
        for c in measured:
            k = c.get(key) or "unknown"
            r = d.setdefault(k, {"name": k, "calls": 0, "tokens": 0, "baseline": 0})
            r["calls"] += 1
            r["tokens"] += c["tokens"]
            r["baseline"] += c["baseline"]
        return sorted(({**r, "saved": r["baseline"] - r["tokens"]} for r in d.values()), key=lambda r: -r["saved"])

    return {
        "calls": len(calls),
        "measured_calls": len(measured),
        "tokens": tokens,
        "baseline": baseline,
        "saved": baseline - tokens,
        "saved_pct": round((baseline - tokens) / baseline * 100, 1) if baseline else 0.0,
        "by_tool": agg("tool"),
        "by_agent": agg("agent_id"),
        "method": "tokens are estimates (about 4.5 characters per token); baseline is a typical top-8 retriever's full-JSON answer to the same call",
    }


@router.get("/mcp/activity")
def mcp_activity(
    limit: int = Query(100, ge=1, le=500),
    member_id: Optional[str] = Query(None),
    auth: AuthContext = Depends(get_auth_context),
):
    """Every MCP tool call, newest first: who, what, tokens used and the baseline. The Connect page's feed and chats."""
    store = get_graph_store(auth.tenant)
    calls = visible_calls(store, auth)
    if member_id:
        calls = [c for c in calls if c.get("member_id") == member_id]
    return list(reversed(calls[-limit:]))


@router.get("/token-stats")
def token_stats(auth: AuthContext = Depends(get_auth_context)):
    store = get_graph_store(auth.tenant)
    return stats_for(visible_calls(store, auth))


@router.get("/context/preview")
def context_preview(
    query: str = Query("refund window", max_length=200),
    budget: int = Query(400, ge=60, le=4000),
    auth: AuthContext = Depends(get_auth_context),
):
    """What an agent with this token would be handed for `query`, and what it costs against the usual alternatives."""
    store = get_graph_store(auth.tenant)
    return cp.compare(store, query, budget, ws.viewer_for(store, auth.member_id))

