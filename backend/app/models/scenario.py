from typing import List
from pydantic import BaseModel


class ScenarioResponse(BaseModel):
    run_id: str
    accepted: bool


class BlastActionsBreakdown(BaseModel):
    staged: int = 0
    executed_reversible: int = 0
    executed_compensable: int = 0
    executed_irreversible: int = 0


class BlastPreviewResponse(BaseModel):
    root_id: str
    node_ids: List[str]
    facts: int
    conclusions: int
    actions: BlastActionsBreakdown


class BlastReportResponse(BaseModel):
    root_id: str
    quarantined_at: int
    ms_to_containment: int
    facts: int
    conclusions: int
    actions_cancelled: int
    actions_compensated: int
    actions_escalated: int
    node_ids_by_depth: List[List[str]]
