from typing import Any, Dict


from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.auth import AuthContext, get_auth_context
from app.graph.queries import get_graph_store
from app.services import context_pack as cp, embeddings, mcp_tools
from app.services import workspace as ws
from app.services.mcp_guard import record_event

router = APIRouter()


class ObserveIn(BaseModel):
    text: str = Field(..., min_length=1, max_length=60000)
    source: str = Field("transcript", max_length=40)
    dry_run: bool = False
    scope: str = "commons"


@router.post("/observe")
def observe_route(body: ObserveIn, auth: AuthContext = Depends(get_auth_context)):
    if auth.role == "auditor":
        raise HTTPException(status_code=403, detail={"error": {"code": "FORBIDDEN_ROLE", "message": "Auditors do not write facts."}})
    info = {"tenant": auth.tenant, "role": auth.role, "agent_id": auth.agent_id, "member_id": auth.member_id, "team_id": auth.team_id}
    out = mcp_tools.observe_text(info, body.text, body.source, body.dry_run, body.scope)
    if not out.get("success") and "scope" in out.get("error", "").lower():
        raise HTTPException(status_code=400, detail={"error": {"code": "BAD_REQUEST", "message": out.get("error")}})
    if out.get("success") and not body.dry_run:
        first = (out.get("facts") or [{}])[0].get("text") or f"text from {body.source}"
        record_event(info, "observe", first)
    return out


@router.get("/context/semantic-check")
def semantic_check(query: str = Query("how long can shoppers get their money back", max_length=200), k: int = Query(5, ge=1, le=20), auth: AuthContext = Depends(get_auth_context)):
    """Ask FalkorDB's own vector index for the nearest nodes and compare with the API process's ranking."""
    from app import db

    store = get_graph_store(auth.tenant)
    viewer = ws.viewer_for(store, auth.member_id)
    with store._lock:
        nodes = [n for n in store.nodes.values() if viewer.can_see(n)]
    cand = [n for n in nodes if n.kind.value in ("fact", "conclusion") and n.status.value == "HEALTHY" and not n.stale]
    ranked = cp.rank(cand, query, cp.idf_weights(cand))[:k]
    out: Dict[str, Any] = {"query": query, "provider": embeddings.provider_name(), "api_top": [{"id": n.id, "score": round(s, 3), "cosine": round(c, 3)} for n, s, c in ranked if s > 0], "falkordb_top": None, "agrees": None}
    if store.persister is not None and embeddings.provider_name() == "local":
        qv = embeddings.as_list(embeddings.query_vector(query))
        try:
            res = db.graph_for(auth.tenant).query(
                "CALL db.idx.vector.queryNodes('QNode', 'embedding', $k, vecf32($q)) YIELD node, score RETURN node.id, score ORDER BY score",
                {"k": max(k * 4, 20), "q": qv},
            ).result_set
            allowed = {n.id for n in cand}
            fal = [{"id": r[0], "distance": round(float(r[1]), 3)} for r in res if r[0] in allowed][:k]
            out["falkordb_top"] = fal
            out["agrees"] = bool(fal) and bool(out["api_top"]) and fal[0]["id"] in [a["id"] for a in out["api_top"][:3]]
        except Exception as e:
            out["falkordb_error"] = f"{type(e).__name__}: {e}"[:200]
    return out

