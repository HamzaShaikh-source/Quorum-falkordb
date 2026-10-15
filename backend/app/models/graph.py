from typing import Optional, List, Dict, Any, Callable
from pydantic import BaseModel, Field, PrivateAttr
from app.models.common import (
    Status,
    NodeKind,
    EdgeType,
    Tier,
    SourceKind,
    Reversibility,
    GateStatus,
    CompensationStatus,
)


class AssertedBy(BaseModel):
    id: str
    name: str
    tier: Tier


class SourceInfo(BaseModel):
    kind: SourceKind
    title: str
    url: str
    version: str


class CompensationInfo(BaseModel):
    status: CompensationStatus
    attempts: int = Field(default=0, ge=0)
    lease_owner: Optional[str] = None
    lease_until: Optional[int] = None
    next_attempt_at: int = 0
    last_error: Optional[str] = None


class ActionInfo(BaseModel):
    type: str
    reversibility: Reversibility
    executed: bool = False
    confirmed: bool = False
    executed_at: Optional[int] = None
    gate: GateStatus
    procedure_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    external_key: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    delivery_error: Optional[str] = None
    execution_attempts: int = 0
    execution_lease_until: Optional[int] = None
    compensation: Optional[CompensationInfo] = None
    correction_notice: Optional[Dict[str, Any]] = None


class GraphNode(BaseModel):
    _on_change: Optional[Callable] = PrivateAttr(default=None)

    def __setattr__(self, name, value):
        super().__setattr__(name, value)
        if name in {"status", "stale", "kind", "label", "text", "key", "value", "scope_team", "scope_teams"} and self._on_change:
            self._on_change(self)

    id: str
    kind: NodeKind
    is_decision: Optional[bool] = None
    label: str = Field(..., max_length=120)
    text: Optional[str] = None
    key: Optional[str] = None
    value: Optional[str] = None
    status: Status
    stale: bool = False
    valid_from: Optional[int] = None
    valid_to: Optional[int] = None
    quarantined_at: Optional[int] = None
    released_at: Optional[int] = None
    quarantine_root: Optional[str] = None
    depth: Optional[int] = Field(default=None, ge=0)
    source_ref: Optional[str] = None
    quote: Optional[str] = None
    origin: Optional[str] = None
    asserted_by: Optional[AssertedBy] = None
    source: Optional[SourceInfo] = None
    owner_team: Optional[str] = None
    # Private to one team (and to teams linked to it). None means the shared commons everyone in the tenant can read.
    scope_team: Optional[str] = None
    # Every private parent scope must be visible. Older records retain scope_team compatibility.
    scope_teams: List[str] = Field(default_factory=list)
    action: Optional[ActionInfo] = None
    correction_notice: Optional[Dict[str, Any]] = None


class GraphEdge(BaseModel):
    id: str
    source: str
    target: str
    type: EdgeType


class GraphResponse(BaseModel):
    contract_version: str
    tenant: str
    epoch: int
    seq: int
    as_of: Optional[int] = None
    generated_at: int
    truncated: bool = False
    nodes: List[GraphNode]
    edges: List[GraphEdge]


class StatusHistoryEntry(BaseModel):
    ts: int
    status: Status
    reason: str


class NodeDetailResponse(BaseModel):
    node: GraphNode
    path: List[GraphNode]
    descendant_count: int
    status_history: List[StatusHistoryEntry]
