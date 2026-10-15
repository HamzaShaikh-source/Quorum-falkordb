from enum import Enum
from typing import Optional, Dict, Any
from pydantic import BaseModel


class Status(str, Enum):
    HEALTHY = "HEALTHY"
    SUPERSEDED = "SUPERSEDED"
    QUARANTINED = "QUARANTINED"


class NodeKind(str, Enum):
    SOURCE = "source"
    FACT = "fact"
    CONCLUSION = "conclusion"
    ACTION = "action"
    ALERT = "alert"


class EdgeType(str, Enum):
    LEADS_TO = "LEADS_TO"
    SUPPORTS = "SUPPORTS"
    CONTRADICTS = "CONTRADICTS"
    SUPERSEDES = "SUPERSEDES"
    FROM_SOURCE = "FROM_SOURCE"


class Tier(str, Enum):
    ROOT = "root"
    ESTABLISHED = "established"
    NEW = "new"


class CompensationStatus(str, Enum):
    PENDING_COMPENSATION = "PENDING_COMPENSATION"
    DISPATCHING = "DISPATCHING"
    COMPENSATED = "COMPENSATED"
    FAILED = "FAILED"


class Reversibility(str, Enum):
    REVERSIBLE = "REVERSIBLE"
    COMPENSABLE = "COMPENSABLE"
    IRREVERSIBLE = "IRREVERSIBLE"


class GateStatus(str, Enum):
    PASSED = "PASSED"
    BLOCKED = "BLOCKED"


class HandoffKind(str, Enum):
    TASK = "TASK"
    REVIEW = "REVIEW"
    ESCALATION = "ESCALATION"


class HandoffStatus(str, Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    TIMED_OUT = "TIMED_OUT"


class SourceKind(str, Enum):
    DOC = "DOC"
    TICKET = "TICKET"
    CHAT = "CHAT"
    WEBHOOK = "WEBHOOK"


class ErrorCode(str, Enum):
    UNAUTHENTICATED = "UNAUTHENTICATED"
    FORBIDDEN_TENANT = "FORBIDDEN_TENANT"
    FORBIDDEN_ROLE = "FORBIDDEN_ROLE"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    GUARD_REFUSED = "GUARD_REFUSED"
    VALIDATION = "VALIDATION"
    FALKORDB_UNAVAILABLE = "FALKORDB_UNAVAILABLE"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    INTERNAL = "INTERNAL"


class ErrorDetail(BaseModel):
    code: ErrorCode
    message: str
    details: Optional[Dict[str, Any]] = None


class ErrorResponse(BaseModel):
    error: ErrorDetail
