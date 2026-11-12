from typing import Optional, List, Dict, Any, Literal
from pydantic import BaseModel


class InvariantCheckItem(BaseModel):
    name: str
    ok: bool
    value: float


class InvariantsResponse(BaseModel):
    ok: bool
    checked_at: int
    checks: List[InvariantCheckItem]


class CypherLogItem(BaseModel):
    ts: int
    request_id: Optional[str] = None
    name: str
    cypher: str
    ms: float
    rows: int
    origin: str = "illustrative"
    outcome: str = "success"
    timing_boundary: str = "client_round_trip"


class DeniedDemoResponse(BaseModel):
    tenant: str
    attempted_graph: str
    result: Literal["DENIED", "ALLOWED"]
    detail: str
    visible_graph_names: List[str]


class ConcurrencyMetrics(BaseModel):
    agents: int
    ops_per_s: float
    p50_ms: float
    p95_ms: float
    p99_ms: float


class HandoffRaceMetrics(BaseModel):
    handoffs: int
    claimers: int
    single_winner_rate: float


class InvalidationMetrics(BaseModel):
    violations: int


class PoisonSystemMetrics(BaseModel):
    name: str
    leaked_rate: float
    wrongly_quarantined: int
    ms_to_containment: Optional[float] = None


class PoisonDepthMetrics(BaseModel):
    depth: int
    systems: List[PoisonSystemMetrics]


class BenchmarkResultsResponse(BaseModel):
    generated_at: int
    params: Dict[str, Any]
    concurrency: ConcurrencyMetrics
    handoff_race: HandoffRaceMetrics
    invalidation_under_load: InvalidationMetrics
    poison: List[PoisonDepthMetrics]
