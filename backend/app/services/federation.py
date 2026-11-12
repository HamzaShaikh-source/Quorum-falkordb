"""
Hub-to-hub connection: find other Quorum hubs on the network, ask to join one, let a person approve, hand over a token.

Nothing here is automatic trust. Discovery only lists names. A request creates a pending item that an admin of the other
hub sees (in the chat, in the console, and as a notice on their next tool call) and must approve. The token is minted at the
moment the requester collects it, so no plaintext token is stored on the approving hub.
"""
import hashlib
import ipaddress
import secrets
import socket
import threading
import time
import uuid
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from app.config import settings
from app.services import workspace as wsvc
from app.services.event_bus import event_bus

PEER_TTL_S = 45
MAX_PENDING = 25
REQUEST_TTL_S = 24 * 3600
_peers: Dict[str, Dict[str, Any]] = {}
_peers_lock = threading.Lock()


class FedError(Exception):
    def __init__(self, message: str, status: int = 422, code: str = "VALIDATION"):
        super().__init__(message)
        self.message, self.status, self.code = message, status, code


def _fed(store) -> Dict[str, Any]:
    ws = wsvc._ws(store)
    f = ws.setdefault("fed", {})
    f.setdefault("hub_id", f"hub_{uuid.uuid4().hex[:10]}")
    f.setdefault("inbox", [])
    f.setdefault("outbox", [])
    f.setdefault("connections", [])
    f.setdefault("survey", {})
    return f


def _h(s: str) -> str:
    return hashlib.sha256(s.encode("utf8")).hexdigest()


def _clean(s: str, n: int, what: str) -> str:
    s = " ".join((s or "").split())
    if not s:
        raise FedError(f"{what} is required.")
    if any(ord(c) < 32 for c in s):
        raise FedError(f"{what} has invalid characters.")
    return s[:n]


def hub_name(store) -> str:
    return settings.HUB_NAME.strip() or wsvc._ws(store).get("org") or store.tenant


def public_url() -> str:
    return (settings.PUBLIC_URL or "").rstrip("/")


def hello(store) -> Dict[str, Any]:
    with store._lock:
        f = _fed(store)
        return {"quorum": 1, "hub_id": f["hub_id"], "name": hub_name(store), "accepting": bool(settings.DISCOVERY), "url": public_url()}


# ---------- URL safety: peers are other machines on the same network ----------
def check_peer_url(url: str) -> str:
    u = urlparse((url or "").strip())
    if u.scheme not in ("http", "https") or not u.hostname or u.username or u.password:
        raise FedError("A peer address must look like http://192.168.1.20:8000.")
    base = f"{u.scheme}://{u.hostname}{':' + str(u.port) if u.port else ''}"
    if settings.PEERS_ALLOW_PUBLIC:
        return base
    try:
        infos = socket.getaddrinfo(u.hostname, u.port or 80, proto=socket.IPPROTO_TCP)
    except OSError:
        raise FedError("That address does not resolve.")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not (ip.is_private or ip.is_loopback or ip.is_link_local):
            raise FedError("Peers must be on your local network (private addresses only).")
    return base


# ---------- discovery ----------
def announce(store, peers: List[Dict[str, Any]]) -> int:
    """The beacon on this machine reports hubs it heard on the network. Own hub and unsafe addresses are ignored."""
    with store._lock:
        mine = _fed(store)["hub_id"]
    n = 0
    now = time.time()
    with _peers_lock:
        for p in peers[:200]:
            try:
                hid = _clean(str(p.get("hub_id", "")), 40, "hub_id")
                if hid == mine:
                    continue
                url = check_peer_url(str(p.get("url", "")))
                name = _clean(str(p.get("name", "")), 60, "name")
            except FedError:
                continue
            _peers[hid] = {"hub_id": hid, "name": name, "url": url, "seen": now}
            n += 1
        for hid in [k for k, v in _peers.items() if now - v["seen"] > PEER_TTL_S * 4]:
            _peers.pop(hid, None)
    return n


def list_peers(store) -> List[Dict[str, Any]]:
    with store._lock:
        f = _fed(store)
        connected = {c["peer_id"] for c in f["connections"]}
        pending = {o["peer_id"] for o in f["outbox"] if o["status"] == "pending"}
    now = time.time()
    with _peers_lock:
        live = [dict(v) for v in _peers.values() if now - v["seen"] <= PEER_TTL_S]
    return [
        {"name": v["name"], "hub_id": v["hub_id"], "url": v["url"], "connected": v["hub_id"] in connected, "requested": v["hub_id"] in pending}
        for v in sorted(live, key=lambda x: x["name"].lower())
    ]


def _peer_from_address(store, url: str) -> Dict[str, Any]:
    """Networks that block broadcast: the person types the other hub's address and we ask it who it is."""
    base = check_peer_url(url)
    try:
        with _http() as c:
            r = c.get(base + "/api/connect/hello")
        info = r.json() if r.status_code == 200 else {}
    except Exception:  # noqa: BLE001
        info = {}
    if info.get("quorum") != 1 or not info.get("hub_id"):
        raise FedError(f"{base} does not answer like a Quorum hub.", 502, "UPSTREAM")
    with store._lock:
        f = _fed(store)
        connected = {c["peer_id"] for c in f["connections"]}
        pending = {o["peer_id"] for o in f["outbox"] if o["status"] == "pending"}
        if info["hub_id"] == f["hub_id"]:
            raise FedError("That is your own hub.", 409, "CONFLICT")
    name = " ".join(str(info.get("name") or base).split())[:60]
    return {"name": name, "hub_id": str(info["hub_id"])[:40], "url": base, "connected": info["hub_id"] in connected, "requested": info["hub_id"] in pending}


def find_peer(store, ref: str) -> Dict[str, Any]:
    ref = (ref or "").strip()
    if ref.lower().startswith(("http://", "https://")):
        return _peer_from_address(store, ref)
    ref = ref.lower()
    if not ref:
        raise FedError("Say which hub to connect to (use quorum_discover to see names, or give its address like http://192.168.1.20:8000).")
    peers = list_peers(store)
    exact = [p for p in peers if p["name"].lower() == ref or p["hub_id"] == ref]
    if len(exact) == 1:
        return exact[0]
    near = [p for p in peers if ref in p["name"].lower()]
    if len(near) == 1:
        return near[0]
    if not near and not exact:
        raise FedError(f"No hub called '{ref}' is visible on this network. Ask them to run Quorum on the same network, then call quorum_discover again.", 404, "NOT_FOUND")
    raise FedError("More than one hub matches: " + ", ".join(p["name"] for p in (exact or near)) + ". Use the full name.", 409, "CONFLICT")


# ---------- receiving side: someone asks to join ----------
def create_request(store, person: str, from_hub: str, message: str) -> Dict[str, str]:
    if not settings.DISCOVERY:
        raise FedError("This hub is not accepting connection requests.", 403, "FORBIDDEN")
    person = _clean(person, 60, "name")
    from_hub = " ".join((from_hub or "").split())[:60]
    message = " ".join((message or "").split())[:200]
    with store._lock:
        f = _fed(store)
        now = time.time()
        f["inbox"] = [r for r in f["inbox"] if r["status"] != "pending" or now - r["created_at"] / 1000 < REQUEST_TTL_S]
        pending = [r for r in f["inbox"] if r["status"] == "pending"]
        if len(pending) >= MAX_PENDING:
            raise FedError("Too many open requests. Ask the admin to review the inbox.", 429, "RATE_LIMITED")
        if any(r["person"].lower() == person.lower() and r["from_hub"] == from_hub for r in pending):
            raise FedError("You already have a request waiting for approval.", 409, "CONFLICT")
        secret = secrets.token_urlsafe(24)
        req = {
            "id": f"cr_{uuid.uuid4().hex[:8]}", "person": person, "from_hub": from_hub, "message": message, "status": "pending",
            "created_at": int(time.time() * 1000), "secret_hash": _h(secret), "member_id": None, "delivered": False,
        }
        f["inbox"].append(req)
    event_bus.publish(store.tenant, "workspace_changed", {"reason": "connect_request", "id": req["id"], "person": person})
    return {"request_id": str(req["id"]), "secret": secret}


def inbox(store, include_done: bool = False) -> List[Dict[str, Any]]:
    with store._lock:
        rows = _fed(store)["inbox"]
        return [{k: v for k, v in r.items() if k != "secret_hash"} for r in rows if include_done or r["status"] == "pending"]


def pending_count(store) -> int:
    with store._lock:
        return sum(1 for r in _fed(store)["inbox"] if r["status"] == "pending")


def respond(store, request_id: str, approve: bool, role: str = "agent", team_id: Optional[str] = None) -> Dict[str, Any]:
    with store._lock:
        f = _fed(store)
        r = next((x for x in f["inbox"] if x["id"] == request_id), None)
        if not r:
            raise FedError("No such request.", 404, "NOT_FOUND")
        if r["status"] != "pending":
            raise FedError(f"That request is already {r['status']}.", 409, "CONFLICT")
        if not approve:
            r["status"] = "declined"
        else:
            ws = wsvc._ws(store)
            if not ws["teams"]:
                wsvc.add_team(store, "Team", 10)
            team = team_id or ws["teams"][0]["id"]
            name = r["person"]
            taken = {m["name"].lower() for m in ws["members"]}
            n, i = name, 2
            while n.lower() in taken:
                n, i = f"{name} {i}", i + 1
            try:
                member, _token = wsvc.add_member(store, n, role, team, "custom", f"{r['from_hub'] or 'remote'} (joined by request)")
            except wsvc.WorkspaceError as e:
                raise FedError(e.message, e.status, e.code)
            r["status"] = "approved"
            r["member_id"] = member["id"]
        out = {k: v for k, v in r.items() if k != "secret_hash"}
    event_bus.publish(store.tenant, "workspace_changed", {"reason": "connect_response", "id": request_id})
    return out


def collect(store, request_id: str, secret: str) -> Dict[str, Any]:
    """The requester asks what happened. Approved: a fresh token, once."""
    with store._lock:
        r = next((x for x in _fed(store)["inbox"] if x["id"] == request_id), None)
        if not r or not secrets.compare_digest(r["secret_hash"], _h(secret or "")):
            raise FedError("Unknown request.", 404, "NOT_FOUND")
        if r["status"] != "approved":
            return {"status": r["status"]}
        if r["delivered"]:
            return {"status": "approved", "delivered": True}
        member, token = wsvc.rotate_token(store, r["member_id"])
        r["delivered"] = True
        ws = wsvc._ws(store)
        team = next((t["name"] for t in ws["teams"] if t["id"] == member["team_id"]), "")
        return {
            "status": "approved", "token": token, "role": member["role"], "member": member["name"], "team": team,
            "hub_name": hub_name(store), "hub_id": _fed(store)["hub_id"], "mcp_url": public_url() + "/mcp", "api_url": public_url(),
        }


# ---------- asking side ----------
def _http():
    import httpx

    return httpx.Client(timeout=6.0, follow_redirects=False)


def _upstream_error(r, who: str) -> str:
    try:
        msg = r.json()["error"]["message"]
    except Exception:  # noqa: BLE001
        msg = f"HTTP {r.status_code}"
    return f"{who} said: {msg}"


def request_connect(store, peer_ref: str, person: str, message: str = "") -> Dict[str, Any]:
    peer = find_peer(store, peer_ref)
    if peer["connected"]:
        raise FedError(f"You are already connected to {peer['name']}.", 409, "CONFLICT")
    if peer["requested"]:
        raise FedError(f"A request to {peer['name']} is already waiting for their approval.", 409, "CONFLICT")
    url = check_peer_url(peer["url"])
    with store._lock:
        mine = hub_name(store)
    try:
        with _http() as c:
            h = c.get(url + "/api/connect/hello")
            if h.status_code != 200 or h.json().get("quorum") != 1:
                raise FedError(f"{peer['name']} does not answer like a Quorum hub.", 502, "UPSTREAM")
            r = c.post(url + "/api/connect/request", json={"person": person, "hub": mine, "message": message})
    except FedError:
        raise
    except Exception as exc:  # noqa: BLE001 - network errors become one clear message
        raise FedError(f"Could not reach {peer['name']} at {url}: {type(exc).__name__}.", 502, "UPSTREAM")
    if r.status_code != 200:
        raise FedError(_upstream_error(r, peer["name"]), 502, "UPSTREAM")
    body = r.json()
    with store._lock:
        _fed(store)["outbox"].append({
            "id": f"co_{uuid.uuid4().hex[:8]}", "peer_id": peer["hub_id"], "peer_name": peer["name"], "peer_url": url,
            "remote_id": body["request_id"], "secret": body["secret"], "status": "pending", "created_at": int(time.time() * 1000),
        })
    return {"peer": peer["name"], "status": "pending"}


def refresh_outbox(store) -> List[Dict[str, Any]]:
    """Ask each hub we wrote to what it decided. Approved ones become connections."""
    with store._lock:
        todo = [dict(o) for o in _fed(store)["outbox"] if o["status"] == "pending"]
    changes = []
    for o in todo:
        try:
            with _http() as c:
                r = c.get(f"{o['peer_url']}/api/connect/requests/{o['remote_id']}/status", headers={"X-Connect-Secret": o["secret"]})
            if r.status_code == 404:
                new: Dict[str, Any] = {"status": "expired"}
            elif r.status_code != 200:
                continue
            else:
                new = r.json()
        except Exception:  # noqa: BLE001 - try again on the next call
            continue
        if new.get("status") == "pending":
            continue
        with store._lock:
            f = _fed(store)
            rec = next((x for x in f["outbox"] if x["id"] == o["id"]), None)
            if not rec:
                continue
            rec["status"] = new["status"]
            if new["status"] == "approved" and new.get("token"):
                rec["secret"] = ""
                f["connections"] = [c for c in f["connections"] if c["peer_id"] != o["peer_id"]]
                f["connections"].append({
                    "peer_id": o["peer_id"], "peer_name": o["peer_name"],
                    "url": (new.get("api_url") or o["peer_url"]).rstrip("/") or o["peer_url"],
                    "mcp_url": new.get("mcp_url") or o["peer_url"] + "/mcp", "token": new["token"], "role": new.get("role", "agent"),
                    "as_member": new.get("member", ""), "team": new.get("team", ""), "connected_at": int(time.time() * 1000),
                })
        changes.append({"peer": o["peer_name"], "status": new["status"]})
    return changes


def outbox(store) -> List[Dict[str, Any]]:
    with store._lock:
        return [{k: v for k, v in o.items() if k != "secret"} for o in _fed(store)["outbox"]]


def connections(store) -> List[Dict[str, Any]]:
    """Connected hubs, without their tokens."""
    with store._lock:
        return [{k: v for k, v in c.items() if k != "token"} for c in _fed(store)["connections"]]


def connection_config(store, peer_ref: str) -> Dict[str, Any]:
    ref = (peer_ref or "").strip().lower()
    with store._lock:
        cs = _fed(store)["connections"]
        hit = next((c for c in cs if c["peer_name"].lower() == ref), None) or next((c for c in cs if ref and ref in c["peer_name"].lower()), None)
        if not hit:
            raise FedError("You are not connected to that hub yet.", 404, "NOT_FOUND")
        return dict(hit)


def peer_post(store, peer_ref: str, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
    c = connection_config(store, peer_ref)
    url = check_peer_url(c["url"])
    try:
        with _http() as h:
            r = h.post(url + path, json=body, headers={"Authorization": f"Bearer {c['token']}"})
    except Exception as exc:  # noqa: BLE001
        raise FedError(f"Could not reach {c['peer_name']}: {type(exc).__name__}.", 502, "UPSTREAM")
    if r.status_code != 200:
        raise FedError(_upstream_error(r, c["peer_name"]), 502, "UPSTREAM")
    return r.json()


def reset_for_tests() -> None:
    with _peers_lock:
        _peers.clear()
