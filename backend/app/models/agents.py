from typing import Optional, List
from pydantic import BaseModel
from app.models.common import Tier, Status


class AgentItem(BaseModel):
    id: str
    name: str
    role: str
    tier: Tier
    last_active: Optional[int] = None


class AgentMemoryItem(BaseModel):
    fact_id: str
    text: str
    source_ref: Optional[str] = None
    quote: Optional[str] = None


class AgentResumeResponse(BaseModel):
    session_id: str
    summary: str
    memories: List[AgentMemoryItem]


class ProcedureItem(BaseModel):
    id: str
    task_type: str
    steps: List[str]
    uses: int
    successes: int
    failures: int
    rank_score: float
    seeded: bool = True


class PromptDiffNode(BaseModel):
    node_id: str
    text: str
    status: Status


class PromptDiffResponse(BaseModel):
    epoch_before: int
    epoch_after: int
    before: List[PromptDiffNode]
    after: List[PromptDiffNode]
    removed_ids: List[str]
