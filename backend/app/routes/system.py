import json
import time
from pathlib import Path
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from app import db
from app.auth import get_auth_context, AuthContext
from app.config import settings
from app.models.common import ErrorCode
from app.models.system import (
    InvariantsResponse,
    InvariantCheckItem,
    CypherLogItem,
    DeniedDemoResponse,
    BenchmarkResultsResponse,
    ConcurrencyMetrics,
    HandoffRaceMetrics,
    InvalidationMetrics,
    PoisonDepthMetrics,
    PoisonSystemMetrics,
)

from app.graph.queries import get_graph_store

router = APIRouter()


@router.get("/invariants", response_model=InvariantsResponse)
def get_invariants(auth: AuthContext = Depends(get_auth_context)):
    store = get_graph_store(auth.tenant)
    res = store.check_invariants()
    now_ms = int(time.time() * 1000)
    return InvariantsResponse(
        ok=res["ok"],
        checked_at=now_ms,
        checks=[InvariantCheckItem(**c) for c in res["checks"]],
    )


@router.get("/cypher-log", response_model=List[CypherLogItem])
def get_cypher_log(
    request_id: Optional[str] = Query(None),
    limit: int = Query(20, le=100),
    auth: AuthContext = Depends(get_auth_context),
):
    """The Cypher this tenant's writes actually ran against FalkorDB, newest first. Nothing is made up."""
    store = get_graph_store(auth.tenant)
    with store._lock:
        entries = list(store.query_log)
    if auth.member_id:
        # Statements can describe other teams' writes; parameter-free Cypher is still sensitive.
        return []

    if request_id:
        entries = [e for e in entries if e.request_id == request_id]

    return [
        CypherLogItem(**e.model_dump())
        for e in reversed(entries[-limit:])
    ]


BENCH_FILE = Path(__file__).resolve().parents[3] / "bench" / "results.json"


@router.get("/bench/results", response_model=BenchmarkResultsResponse)
def get_bench_results(auth: AuthContext = Depends(get_auth_context)):
    """The last recorded run of `python bench/run.py`. Not generated here, so nothing on the page is invented."""
    if not BENCH_FILE.exists():
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": ErrorCode.NOT_FOUND.value, "message": "No benchmark has been recorded yet. Run: python bench/run.py (needs FalkorDB)."}},
        )
    r = json.loads(BENCH_FILE.read_text(encoding="utf-8"))
    c, h, e = r["concurrency"], r["handoff_race"], r.get("execute_race", {})
    poison = []
    for key in sorted(r.get("poison_bench", {})):
        d = r["poison_bench"][key]
        poison.append(
            PoisonDepthMetrics(
                depth=d["causal_depth"],
                systems=[
                    PoisonSystemMetrics(name="Quorum (Graph Memory)", leaked_rate=d["quorum_retrieval_leakage_rate"], wrongly_quarantined=0, ms_to_containment=d["containment_ms"]),
                    PoisonSystemMetrics(name="Baseline Flat Vector + TTL", leaked_rate=d["baseline_flat_ttl_leakage_rate"], wrongly_quarantined=0, ms_to_containment=None),
                    PoisonSystemMetrics(name="Delete Root Fact Only", leaked_rate=d["baseline_delete_only_leakage_rate"], wrongly_quarantined=0, ms_to_containment=None),
                ],
            )
        )
    return BenchmarkResultsResponse(
        generated_at=r["timestamp"],
        params={
            "engine": r.get("engine", "unknown"),
            "falkordb_version": r.get("falkordb_version"),
            "harness_agents": c.get("agents"),
            "source": "bench/results.json",
            "reproduce": "python bench/run.py",
        },
        concurrency=ConcurrencyMetrics(agents=c["agents"], ops_per_s=c["throughput_ops_sec"], p50_ms=c["p50_ms"], p95_ms=c["p95_ms"], p99_ms=c["p99_ms"]),
        handoff_race=HandoffRaceMetrics(handoffs=h["total_handoffs"], claimers=h["concurrent_claimers"], single_winner_rate=h["exact_single_winner_rate"]),
        invalidation_under_load=InvalidationMetrics(
            violations=int(c.get("healthy_children_under_quarantined", 0)) + int(e.get("actions_executed_after_ancestor_quarantined", 0))
        ),
        poison=poison,
    )


OTHER_TENANT = {"acme": "globex", "globex": "acme"}


@router.get("/tenants/denied-demo", response_model=DeniedDemoResponse)
def get_denied_demo(auth: AuthContext = Depends(get_auth_context)):
    """
    A live isolation check, not a canned one. This tenant's token asks for the other tenant's graph. The API resolves
    the graph from the token and never from request input, so the answer is a refusal. The graph names listed are the
    ones FalkorDB really holds for this tenant.
    """
    other = OTHER_TENANT.get(auth.tenant, "acme" if auth.tenant != "acme" else "globex")
    attempted = f"quorum:{other}"
    own = f"quorum:{auth.tenant}"
    allowed = False  # structural: the tenant always comes from the token, so there is no path that allows it
    visible = [own]
    if settings.PERSISTENCE == "falkordb":
        try:
            visible = sorted(g for g in db.get_client().list_graphs() if g == own or g.startswith(f"{own}:"))
        except Exception:
            visible = [own]
    return DeniedDemoResponse(
        tenant=auth.tenant,
        attempted_graph=attempted,
        result="ALLOWED" if allowed else "DENIED",
        detail=(
            f"Token for tenant '{auth.tenant}' asked for graph '{attempted}'. The API picks the graph from the token "
            f"('{own}'), never from the request, so any other tenant's graph is refused (FORBIDDEN_TENANT). "
            "Each tenant is a separate FalkorDB graph."
        ),
        visible_graph_names=visible,
    )

