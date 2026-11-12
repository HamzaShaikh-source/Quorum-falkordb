"""Connecting hubs: the public handshake another machine uses, and the admin side (inbox, outgoing requests, peers)."""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app.auth import AuthContext, get_auth_context, require_role
from app.config import settings
from app.graph.queries import get_graph_store
from app.ratelimit import RateLimiter
from app.services import federation as fed

router = APIRouter()

# A stranger can call the public handshake, so it is limited more tightly than the rest of the API.
_public_limiter = RateLimiter(12, burst=6)


def _fail(e: fed.FedError) -> HTTPException:
    return HTTPException(status_code=e.status, detail={"error": {"code": e.code, "message": e.message}})


def _hub_store():
    return get_graph_store(settings.DEFAULT_TENANT)


def _ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


class ConnectRequestIn(BaseModel):
    person: str = Field(..., min_length=1, max_length=60)
    hub: str = Field("", max_length=60)
    message: str = Field("", max_length=200)


class RespondIn(BaseModel):
    approve: bool
    role: str = "agent"
    team_id: Optional[str] = None


class OutgoingIn(BaseModel):
    peer: str = Field(..., min_length=1, max_length=80)
    person: str = Field("", max_length=60)
    message: str = Field("", max_length=200)


class PeerIn(BaseModel):
    hub_id: str = Field(..., max_length=40)
    name: str = Field(..., max_length=60)
    url: str = Field(..., max_length=200)


class AnnounceIn(BaseModel):
    peers: List[PeerIn] = Field(default_factory=list, max_length=200)


class RetrieveIn(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    k: int = Field(5, ge=1, le=20)
    format: str = Field("compact", pattern="^(compact|full)$")


# ---------- public handshake (no token: the other person is not a member yet) ----------
@router.get("/connect/hello")
def hello() -> Dict[str, Any]:
    return fed.hello(_hub_store())


@router.post("/connect/request")
def create_request(body: ConnectRequestIn, request: Request) -> Dict[str, Any]:
    ok, wait = _public_limiter.allow(_ip(request))
    if not ok:
        raise HTTPException(status_code=429, detail={"error": {"code": "RATE_LIMITED", "message": f"Slow down. Try again in {wait:.0f}s."}})
    try:
        return fed.create_request(_hub_store(), body.person, body.hub, body.message)
    except fed.FedError as e:
        raise _fail(e)


@router.get("/connect/requests/{request_id}/status")
def request_status(request_id: str, request: Request, x_connect_secret: str = Header("", max_length=80)) -> Dict[str, Any]:
    ok, wait = _public_limiter.allow("status:" + _ip(request))
    if not ok:
        raise HTTPException(status_code=429, detail={"error": {"code": "RATE_LIMITED", "message": f"Slow down. Try again in {wait:.0f}s."}})
    try:
        return fed.collect(_hub_store(), request_id, x_connect_secret)
    except fed.FedError as e:
        raise _fail(e)


# ---------- admin side ----------
@router.get("/connect/inbox")
def get_inbox(auth: AuthContext = Depends(require_role(["human"]))) -> Dict[str, Any]:
    return {"requests": fed.inbox(get_graph_store(auth.tenant), include_done=True)}


@router.post("/connect/inbox/{request_id}/respond")
def respond(request_id: str, body: RespondIn, auth: AuthContext = Depends(require_role(["human"]))) -> Dict[str, Any]:
    try:
        return fed.respond(get_graph_store(auth.tenant), request_id, body.approve, body.role, body.team_id)
    except fed.FedError as e:
        raise _fail(e)


@router.get("/peers")
def peers(auth: AuthContext = Depends(get_auth_context)) -> Dict[str, Any]:
    store = get_graph_store(auth.tenant)
    fed.refresh_outbox(store)
    return {"hub": fed.hello(store), "peers": fed.list_peers(store), "outgoing": fed.outbox(store), "connections": fed.connections(store)}


@router.post("/peers/announce")
def announce(body: AnnounceIn, auth: AuthContext = Depends(require_role(["human"]))) -> Dict[str, Any]:
    return {"accepted": fed.announce(get_graph_store(auth.tenant), [p.model_dump() for p in body.peers])}


@router.post("/connect/outgoing")
def outgoing(body: OutgoingIn, auth: AuthContext = Depends(require_role(["human"]))) -> Dict[str, Any]:
    store = get_graph_store(auth.tenant)
    try:
        return fed.request_connect(store, body.peer, body.person or (auth.agent_id or "A teammate"), body.message)
    except fed.FedError as e:
        raise _fail(e)


# ---------- what a connected peer may ask this hub (their member token, their scope) ----------
@router.post("/context/retrieve")
def retrieve(body: RetrieveIn, auth: AuthContext = Depends(get_auth_context)) -> Dict[str, Any]:
    from app.services import mcp_read

    return mcp_read.retrieve_context(body.query, token=auth.token, k=body.k, format=body.format)
