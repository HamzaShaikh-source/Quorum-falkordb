from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from app.auth import AuthContext, get_auth_context, require_role
from app.graph.queries import get_graph_store
from app.models.workspace import (
    WorkspacePatch,
    TeamIn,
    SeatsIn,
    MemberIn,
    MemberPatch,
    LinkIn,
    PausedIn,
)
from app.services import roles
from app.services import workspace as ws
from app.services.mcp_guard import record_event
from app.services.event_bus import event_bus


router = APIRouter()


def _fail(e: ws.WorkspaceError) -> HTTPException:
    return HTTPException(status_code=e.status, detail={"error": {"code": e.code, "message": e.message}})


def _done(auth: AuthContext, store, extra: Optional[dict] = None) -> dict:
    out = ws.public_view(store)
    event_bus.publish(auth.tenant, "workspace_changed", {})
    if extra:
        out.update(extra)
    return out


@router.get("/workspace")
def get_workspace(auth: AuthContext = Depends(get_auth_context)):
    return ws.public_view(get_graph_store(auth.tenant))


@router.put("/workspace")
def patch_workspace(body: WorkspacePatch, auth: AuthContext = Depends(require_role(["human"]))):
    store = get_graph_store(auth.tenant)
    try:
        if body.org is not None:
            ws.rename(store, body.org)
        if body.plan is not None:
            ws.set_plan(store, body.plan)
        if body.hub is not None:
            ws.set_hub(store, body.hub)
    except ws.WorkspaceError as e:
        raise _fail(e)
    return _done(auth, store)


@router.post("/workspace/teams")
def add_team(body: TeamIn, auth: AuthContext = Depends(require_role(["human"]))):
    store = get_graph_store(auth.tenant)
    try:
        ws.add_team(store, body.name, body.seats)
    except ws.WorkspaceError as e:
        raise _fail(e)
    return _done(auth, store)


@router.patch("/workspace/teams/{team_id}")
def set_seats(team_id: str, body: SeatsIn, auth: AuthContext = Depends(require_role(["human"]))):
    store = get_graph_store(auth.tenant)
    try:
        ws.set_seats(store, team_id, body.seats)
    except ws.WorkspaceError as e:
        raise _fail(e)
    return _done(auth, store)


@router.delete("/workspace/teams/{team_id}")
def remove_team(team_id: str, auth: AuthContext = Depends(require_role(["human"]))):
    store = get_graph_store(auth.tenant)
    try:
        ws.remove_team(store, team_id)
    except ws.WorkspaceError as e:
        raise _fail(e)
    return _done(auth, store)


@router.post("/workspace/members")
def add_member(body: MemberIn, auth: AuthContext = Depends(require_role(["human"]))):
    """Creates a member and returns their token. It is shown once and only its hash is kept."""
    store = get_graph_store(auth.tenant)
    try:
        member, token = ws.add_member(store, body.name, body.role, body.team_id, body.client, body.device, body.expires_in_days)
    except ws.WorkspaceError as e:
        raise _fail(e)
    return _done(auth, store, {"issued": {"member": member, "token": token}})


@router.patch("/workspace/members/{member_id}")
def update_member(member_id: str, body: MemberPatch, auth: AuthContext = Depends(require_role(["human"]))):
    """Change a member's permission level (agent = Contributor, auditor = Reviewer, human = Admin), team or AI client."""
    store = get_graph_store(auth.tenant)
    try:
        member, before = ws.update_member(store, member_id, body.role, body.team_id, body.client)
    except ws.WorkspaceError as e:
        raise _fail(e)
    if body.role is not None and body.role != before["role"]:
        info = {"tenant": auth.tenant, "role": auth.role, "agent_id": auth.agent_id, "member_id": auth.member_id, "team_id": auth.team_id}
        record_event(info, "console.workspace", f"changed {member['name']} from {roles.level_name(before['role'])} to {roles.level_name(member['role'])}")
    return _done(auth, store)


@router.post("/workspace/members/{member_id}/rotate")
def rotate(member_id: str, auth: AuthContext = Depends(require_role(["human"]))):
    store = get_graph_store(auth.tenant)
    try:
        member, token = ws.rotate_token(store, member_id)
    except ws.WorkspaceError as e:
        raise _fail(e)
    return _done(auth, store, {"issued": {"member": member, "token": token}})


@router.delete("/workspace/members/{member_id}")
def remove_member(member_id: str, auth: AuthContext = Depends(require_role(["human"]))):
    store = get_graph_store(auth.tenant)
    try:
        ws.remove_member(store, member_id)
    except ws.WorkspaceError as e:
        raise _fail(e)
    return _done(auth, store)


@router.post("/workspace/links")
def add_link(body: LinkIn, auth: AuthContext = Depends(require_role(["human"]))):
    store = get_graph_store(auth.tenant)
    try:
        ws.link(store, body.a, body.b, body.scope)
    except ws.WorkspaceError as e:
        raise _fail(e)
    return _done(auth, store)


@router.patch("/workspace/links/{link_id}")
def pause_link(link_id: str, body: PausedIn, auth: AuthContext = Depends(require_role(["human"]))):
    store = get_graph_store(auth.tenant)
    try:
        ws.set_link_paused(store, link_id, body.paused)
    except ws.WorkspaceError as e:
        raise _fail(e)
    return _done(auth, store)


@router.delete("/workspace/links/{link_id}")
def remove_link(link_id: str, auth: AuthContext = Depends(require_role(["human"]))):
    store = get_graph_store(auth.tenant)
    try:
        ws.unlink(store, link_id)
    except ws.WorkspaceError as e:
        raise _fail(e)
    return _done(auth, store)

@router.get("/workspace/members/{member_id}/leaver-report")
def leaver_report(member_id: str, auth: AuthContext = Depends(require_role(["human"]))):
    """What leaves with this person: facts only they recorded, what is built on them, tasks they hold, likely successors."""
    from app.services.mcp_coord import leaver_report as report

    store = get_graph_store(auth.tenant)
    with store._lock:
        m = next((x for x in ws._ws(store)["members"] if x["id"] == member_id), None)
    if m is None:
        raise HTTPException(status_code=404, detail={"error": {"code": "NOT_FOUND", "message": "That member does not exist."}})
    return report(store, m)
