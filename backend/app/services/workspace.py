"""
The workspace: plan, teams, members and links for one tenant. Server-side, persisted in FalkorDB with the tenant's graph.

Members get their own token, issued here and shown once. It is a real credential: `resolve_token` accepts it, tied to the
member's role and team. Links decide whose team-private facts a member may read, which is how context is shared between
people on different laptops with different AIs: they all talk to the same server, and the server decides what each sees.
"""
import hashlib
import re
import secrets
import time
import uuid
from typing import Any, Dict, Optional, Tuple

from app.services.context_pack import Viewer

PLANS: Dict[str, Dict[str, Any]] = {
    "team": {"label": "Team", "seats": 10, "teams": 2, "note": "One shared commons, small group"},
    "business": {"label": "Business", "seats": 50, "teams": 8, "note": "Several teams, per-team scopes"},
    "enterprise": {"label": "Enterprise", "seats": 500, "teams": 40, "note": "Many teams, audit trail, linked workspaces"},
}
ROLES = ("agent", "auditor", "human")
CLIENTS = ("claude-code", "claude-desktop", "cursor", "vscode", "windsurf", "antigravity", "chatgpt", "custom")
ONLINE_S = 120
IDLE_S = 900


class WorkspaceError(Exception):
    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def default_workspace(tenant: str) -> Dict[str, Any]:
    return {"org": tenant.replace("_", " ").title(), "plan": "business", "hub": "", "teams": [], "members": [], "links": []}


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:6]}"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf8")).hexdigest()


def _ws(store) -> Dict[str, Any]:
    ws = store.workspace
    ws.setdefault("teams", [])
    ws.setdefault("members", [])
    ws.setdefault("links", [])
    ws.setdefault("plan", "business")
    ws.setdefault("org", store.tenant.replace("_", " ").title())
    ws.setdefault("hub", "")
    return ws


def _team(store, team_id: str) -> Dict[str, Any]:
    t = next((x for x in _ws(store)["teams"] if x["id"] == team_id), None)
    if not t:
        raise WorkspaceError("NOT_FOUND", "That team does not exist.", 404)
    return t


def _member(store, member_id: str) -> Dict[str, Any]:
    m = next((x for x in _ws(store)["members"] if x["id"] == member_id), None)
    if not m:
        raise WorkspaceError("NOT_FOUND", "That member does not exist.", 404)
    return m


def _clean_name(name: str, what: str) -> str:
    n = re.sub(r"\s+", " ", (name or "")).strip()
    if not n:
        raise WorkspaceError("VALIDATION", f"Give the {what} a name.")
    if len(n) > 60:
        raise WorkspaceError("VALIDATION", f"The {what} name is too long (60 characters at most).")
    return n


# ---------- reads ----------
def public_view(store) -> Dict[str, Any]:
    """The workspace as the console sees it: no token hashes, with presence and token savings per member."""
    with store._lock:
        ws = _ws(store)
        now = time.time() * 1000
        last: Dict[str, int] = {}
        calls: Dict[str, int] = {}
        saved: Dict[str, int] = {}
        for c in store.calls:
            mid = c.get("member_id")
            if not mid:
                continue
            last[mid] = max(last.get(mid, 0), c["ts"])
            calls[mid] = calls.get(mid, 0) + 1
            if c.get("baseline", 0) > 0:
                saved[mid] = saved.get(mid, 0) + (c["baseline"] - c["tokens"])
        members = []
        for m in ws["members"]:
            ts = last.get(m["id"])
            age = (now - ts) / 1000 if ts else None
            status = "offline" if age is None or age > IDLE_S else ("online" if age <= ONLINE_S else "idle")
            members.append(
                {
                    **{k: v for k, v in m.items() if k != "token_hash"},
                    "status": status,
                    "last_seen": ts,
                    "calls": calls.get(m["id"], 0),
                    "tokens_saved": saved.get(m["id"], 0),
                }
            )
        plan = PLANS[ws["plan"]]
        return {
            "org": ws["org"],
            "plan": ws["plan"],
            "hub": ws["hub"],
            "limits": {"seats": plan["seats"], "teams": plan["teams"], "label": plan["label"], "note": plan["note"]},
            "teams": [dict(t) for t in ws["teams"]],
            "members": members,
            "links": [dict(l) for l in ws["links"]],
        }


def find_member_by_token(store, token: str) -> Optional[Dict[str, Any]]:
    h = hash_token(token)
    with store._lock:
        for m in _ws(store)["members"]:
            if secrets.compare_digest(m["token_hash"], h):
                exp = m.get("expires_at")
                if exp and exp < int(time.time() * 1000):
                    return None  # an expired token is as good as a wrong one: the caller gets 401 and rotates it
                return dict(m)
    return None


def viewer_for(store, member_id: Optional[str]) -> Viewer:
    """Unscoped for service callers; for a member, their team plus the teams of everyone actively linked to them."""
    if not member_id:
        return Viewer(scoped=False)
    with store._lock:
        ws = _ws(store)
        m = next((x for x in ws["members"] if x["id"] == member_id), None)
        if not m:
            return Viewer(scoped=True)
        linked_ids = set()
        for l in ws["links"]:
            if l["paused"]:
                continue
            if l["a"] == member_id:
                linked_ids.add(l["b"])
            elif l["b"] == member_id:
                linked_ids.add(l["a"])
        teams = {x["team_id"] for x in ws["members"] if x["id"] in linked_ids}
        teams.discard(m["team_id"])
        return Viewer(agent_id=m["name"], team_id=m["team_id"], linked_teams=teams, scoped=True)


# ---------- writes ----------
def rename(store, org: str) -> None:
    with store._lock:
        _ws(store)["org"] = _clean_name(org, "company")


def set_plan(store, plan: str) -> None:
    if plan not in PLANS:
        raise WorkspaceError("VALIDATION", f"Unknown plan '{plan}'.")
    with store._lock:
        ws = _ws(store)
        p = PLANS[plan]
        if len(ws["members"]) > p["seats"]:
            raise WorkspaceError("CONFLICT", f"{len(ws['members'])} members do not fit the {p['label']} plan ({p['seats']} seats). Remove some first.", 409)
        if len(ws["teams"]) > p["teams"]:
            raise WorkspaceError("CONFLICT", f"{len(ws['teams'])} teams do not fit the {p['label']} plan ({p['teams']} teams). Remove some first.", 409)
        ws["plan"] = plan


def set_hub(store, hub: str) -> None:
    hub = (hub or "").strip()
    if hub and not re.match(r"^https?://[^\s]+$", hub):
        raise WorkspaceError("VALIDATION", "The hub must be an http(s) URL.")
    with store._lock:
        _ws(store)["hub"] = hub


def add_team(store, name: str, seats: int) -> Dict[str, Any]:
    n = _clean_name(name, "team")
    if not 1 <= int(seats) <= 200:
        raise WorkspaceError("VALIDATION", "Seats must be between 1 and 200.")
    with store._lock:
        ws = _ws(store)
        if any(t["name"].lower() == n.lower() for t in ws["teams"]):
            raise WorkspaceError("CONFLICT", "A team with that name already exists.", 409)
        p = PLANS[ws["plan"]]
        if len(ws["teams"]) >= p["teams"]:
            raise WorkspaceError("CONFLICT", f"The {p['label']} plan allows {p['teams']} teams.", 409)
        t = {"id": _uid("team"), "name": n, "seats": int(seats)}
        ws["teams"].append(t)
        return dict(t)


def set_seats(store, team_id: str, seats: int) -> Dict[str, Any]:
    if not 1 <= int(seats) <= 200:
        raise WorkspaceError("VALIDATION", "Seats must be between 1 and 200.")
    with store._lock:
        t = _team(store, team_id)
        used = sum(1 for m in _ws(store)["members"] if m["team_id"] == team_id)
        if int(seats) < used:
            raise WorkspaceError("CONFLICT", f"{t['name']} has {used} members, so it needs at least {used} seats.", 409)
        t["seats"] = int(seats)
        return dict(t)


def remove_team(store, team_id: str) -> None:
    with store._lock:
        ws = _ws(store)
        _team(store, team_id)
        gone = {m["id"] for m in ws["members"] if m["team_id"] == team_id}
        ws["teams"] = [t for t in ws["teams"] if t["id"] != team_id]
        ws["members"] = [m for m in ws["members"] if m["team_id"] != team_id]
        ws["links"] = [l for l in ws["links"] if l["a"] not in gone and l["b"] not in gone]
        for mid in gone:
            store.agent_state.pop(mid, None)
        # Facts private to a removed team become unreadable by scoped members but stay in the graph for audit.


def settings_ttl(days: Optional[int]) -> int:
    from app.config import settings

    return settings.MEMBER_TOKEN_TTL_DAYS if days is None else days


def _expiry(days: Optional[int]) -> Optional[int]:
    from app.config import settings

    d = settings.MEMBER_TOKEN_TTL_DAYS if days is None else days
    return int(time.time() * 1000) + d * 86_400_000 if d and d > 0 else None


def add_member(store, name: str, role: str, team_id: str, client: str, device: str, expires_in_days: Optional[int] = None) -> Tuple[Dict[str, Any], str]:
    n = _clean_name(name, "member")
    if role not in ROLES:
        raise WorkspaceError("VALIDATION", f"Role must be one of {', '.join(ROLES)}.")
    if client not in CLIENTS:
        raise WorkspaceError("VALIDATION", "Unknown AI client.")
    with store._lock:
        ws = _ws(store)
        t = _team(store, team_id)
        p = PLANS[ws["plan"]]
        if len(ws["members"]) >= p["seats"]:
            raise WorkspaceError("CONFLICT", f"All {p['seats']} seats on the {p['label']} plan are used. Upgrade the plan or remove a member.", 409)
        if sum(1 for m in ws["members"] if m["team_id"] == team_id) >= t["seats"]:
            raise WorkspaceError("CONFLICT", f"{t['name']} has no free seats. Raise its seat count first.", 409)
        if any(m["name"].lower() == n.lower() for m in ws["members"]):
            raise WorkspaceError("CONFLICT", "A member with that name already exists.", 409)
        token = f"qrm_{store.tenant}_{secrets.token_hex(16)}"
        m = {
            "id": _uid("m"),
            "name": n,
            "role": role,
            "team_id": team_id,
            "client": client,
            "device": (device or f"{n}'s laptop").strip()[:80],
            "token_hash": hash_token(token),
            "token_prefix": token[: len(f"qrm_{store.tenant}_") + 4],
            "created_at": int(time.time() * 1000),
            "ttl_days": settings_ttl(expires_in_days),
            "expires_at": _expiry(expires_in_days),
        }
        ws["members"].append(m)
        return {k: v for k, v in m.items() if k != "token_hash"}, token


def rotate_token(store, member_id: str) -> Tuple[Dict[str, Any], str]:
    with store._lock:
        m = _member(store, member_id)
        token = f"qrm_{store.tenant}_{secrets.token_hex(16)}"
        m["token_hash"] = hash_token(token)
        m["token_prefix"] = token[: len(f"qrm_{store.tenant}_") + 4]
        m["expires_at"] = _expiry(m.get("ttl_days", 0))  # a rotated token gets a fresh lifetime of the same length
        return {k: v for k, v in m.items() if k != "token_hash"}, token


def update_member(store, member_id: str, role: Optional[str] = None, team_id: Optional[str] = None, client: Optional[str] = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Change a member's permission level, team or AI client. Takes effect on their very next call: tokens are resolved from this record every time."""
    with store._lock:
        m = _member(store, member_id)
        before = {"role": m["role"], "team_id": m["team_id"], "client": m["client"]}
        if role is not None:
            if role not in ROLES:
                raise WorkspaceError("VALIDATION", f"Level must be one of {', '.join(ROLES)}.")
            m["role"] = role
        if team_id is not None:
            _team(store, team_id)
            ws = _ws(store)
            if team_id != m["team_id"] and sum(1 for x in ws["members"] if x["team_id"] == team_id) >= _team(store, team_id)["seats"]:
                raise WorkspaceError("CONFLICT", "That team has no free seats. Raise its seat count first.", 409)
            m["team_id"] = team_id
        if client is not None:
            if client not in CLIENTS:
                raise WorkspaceError("VALIDATION", "Unknown AI client.")
            m["client"] = client
        return {k: v for k, v in m.items() if k != "token_hash"}, before


def remove_member(store, member_id: str) -> None:
    with store._lock:
        ws = _ws(store)
        _member(store, member_id)
        ws["members"] = [m for m in ws["members"] if m["id"] != member_id]
        ws["links"] = [l for l in ws["links"] if l["a"] != member_id and l["b"] != member_id]
        store.agent_state.pop(member_id, None)


def link(store, a: str, b: str, scope: str) -> Dict[str, Any]:
    if scope not in ("team", "commons"):
        raise WorkspaceError("VALIDATION", "Scope must be team or commons.")
    if a == b:
        raise WorkspaceError("VALIDATION", "Pick two different members.")
    with store._lock:
        ws = _ws(store)
        _member(store, a)
        _member(store, b)
        if any((l["a"] == a and l["b"] == b) or (l["a"] == b and l["b"] == a) for l in ws["links"]):
            raise WorkspaceError("CONFLICT", "Those two are already linked.", 409)
        l = {"id": _uid("l"), "a": a, "b": b, "scope": scope, "paused": False, "created_at": int(time.time() * 1000)}
        ws["links"].append(l)
        return dict(l)


def unlink(store, link_id: str) -> None:
    with store._lock:
        ws = _ws(store)
        if not any(l["id"] == link_id for l in ws["links"]):
            raise WorkspaceError("NOT_FOUND", "That link does not exist.", 404)
        ws["links"] = [l for l in ws["links"] if l["id"] != link_id]


def set_link_paused(store, link_id: str, paused: bool) -> Dict[str, Any]:
    with store._lock:
        l = next((x for x in _ws(store)["links"] if x["id"] == link_id), None)
        if not l:
            raise WorkspaceError("NOT_FOUND", "That link does not exist.", 404)
        l["paused"] = bool(paused)
        return dict(l)
