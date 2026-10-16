from typing import List, Optional
from pydantic import BaseModel, Field


class WorkspacePatch(BaseModel):
    org: Optional[str] = None
    plan: Optional[str] = None
    hub: Optional[str] = None


class TeamIn(BaseModel):
    name: str
    seats: int = 5


class SeatsIn(BaseModel):
    seats: int


class MemberIn(BaseModel):
    name: str
    role: str = "agent"
    team_id: str
    client: str = "custom"
    device: str = ""
    expires_in_days: Optional[int] = Field(None, ge=0, le=3650)  # None: the hub default; 0: never


class MemberPatch(BaseModel):
    role: Optional[str] = None
    team_id: Optional[str] = None
    client: Optional[str] = None


class LinkIn(BaseModel):
    a: str
    b: str
    scope: str = "team"


class PausedIn(BaseModel):
    paused: bool


class TeamItem(BaseModel):
    id: str
    name: str
    seats: int


class MemberItem(BaseModel):
    id: str
    name: str
    role: str
    team_id: str
    client: str
    device: str
    token_prefix: str
    created_at: int
    ttl_days: int
    expires_at: Optional[int] = None
    status: str
    last_seen: Optional[float] = None
    calls: int
    tokens_saved: int


class LinkItem(BaseModel):
    id: str
    a: str
    b: str
    scope: str
    paused: bool
    created_at: int


class WorkspaceLimits(BaseModel):
    seats: int
    teams: int
    label: str
    note: str


class WorkspaceResponse(BaseModel):
    org: str
    plan: str
    hub: str
    limits: WorkspaceLimits
    teams: List[TeamItem]
    members: List[MemberItem]
    links: List[LinkItem]
