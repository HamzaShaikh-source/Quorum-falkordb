from typing import Optional, List
from pydantic import BaseModel


class Citation(BaseModel):
    source_title: str
    url: str
    version: str
    quote: str
    owner_team: str
    valid_from: int
    stale: bool


class BrainAskRequest(BaseModel):
    question: str


class BrainAskResponse(BaseModel):
    refused: bool
    reason: Optional[str] = None
    answer: Optional[str] = None
    citations: List[Citation] = []


class WhoKnowsItem(BaseModel):
    person: str
    team: str
    evidence_count: int
    source_ids: List[str]


class DecisionTraceItem(BaseModel):
    claim: str
    quote: str
    source_title: str
    url: str
    version: str
    owner: str
