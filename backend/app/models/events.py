from typing import Optional, List, Dict, Any, Literal
from pydantic import BaseModel, Field
from app.models.common import Status, CompensationStatus
from app.models.graph import GraphNode, GraphEdge
from app.models.handoffs import Handoff


class NodeAddedData(BaseModel):
    node: GraphNode


class EdgeAddedData(BaseModel):
    edge: GraphEdge


class StatusChangedData(BaseModel):
    id: str
    status: Status
    stale: Optional[bool] = None
    depth: Optional[int] = Field(default=None, ge=0)
    root_id: Optional[str] = None


class EpochChangedData(BaseModel):
    epoch: int = Field(..., ge=0)
    reason: Literal["quarantine", "restore", "supersede"]


class GateBlockedData(BaseModel):
    action_id: str
    handoff_id: str
    reason: str


class ActionExecutedData(BaseModel):
    action_id: str
    confirmed: bool


class CompensationUpdatedData(BaseModel):
    action_id: str
    status: CompensationStatus
    attempts: int = Field(default=0, ge=0)


class HandoffClaimedData(BaseModel):
    handoff_id: str
    winner: str
    losers: List[str]
    lease_until: int


class HandoffUpdatedData(BaseModel):
    handoff: Handoff


class InvariantStatusData(BaseModel):
    ok: bool
    violations: int = Field(default=0, ge=0)


class ScenarioProgressData(BaseModel):
    run_id: str
    step: str
    state: Literal["started", "finished", "failed"]
    detail: Optional[str] = None


class SSEEventEnvelope(BaseModel):
    seq: int = Field(..., ge=0)
    ts: int
    epoch: int = Field(..., ge=0)
    type: str
    request_id: Optional[str] = None
    data: Dict[str, Any]
