from typing import Optional, Dict, Any
from enum import Enum
from pydantic import BaseModel
from app.models.common import HandoffKind, HandoffStatus


class Handoff(BaseModel):
    id: str
    kind: HandoffKind
    task: str
    status: HandoffStatus
    assigned_to: Optional[str] = None
    claimed_by: Optional[str] = None
    claimed_at: Optional[int] = None
    lease_until: Optional[int] = None
    context_root_id: Optional[str] = None
    correction_notice: Optional[Dict[str, Any]] = None
    note: Optional[str] = None


class ReviewDecision(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"

    @classmethod
    def _missing_(cls, value):
        if isinstance(value, str):
            for member in cls:
                if member.value.lower() == value.lower():
                    return member
        return None


class ReviewRequest(BaseModel):
    decision: ReviewDecision
    note: Optional[str] = None
    notes: Optional[str] = None
