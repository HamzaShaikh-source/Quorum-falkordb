"""
The chat layer: everything a person needs to get started and run a team from inside their AI chat, whichever AI it is.

    quorum_help         what commands exist and what each one does
    quorum_start        the short survey that sets the workspace up (team size, name, focus, capture, teammates)
    quorum_status       what is remembered, what will be remembered, what is shared, who is connected
    quorum_manage       teams, members, permission levels and sharing links, without opening the console
    quorum_discover     other Quorum hubs on the same network
    quorum_connect      ask to join one of them (their admin is notified and must approve)
    quorum_inbox        requests waiting for you
    quorum_respond      approve or decline a request
    quorum_connections  the hubs you are connected to, and the MCP settings for them
    peer_retrieve       ask a connected hub a question, in its scope for you

Each tool returns a short `text` the AI can show as is, plus the data behind it. Mutating tools need an Admin token.
"""
import json
import time
from typing import Any, Dict, List, Optional

from app.config import settings
from app.graph.queries import get_graph_store
from app.models.common import NodeKind, Status
from app.services import federation as fed
from app.services import roles
from app.services import workspace as wsvc
from app.services.context_pack import estimate_tokens
from app.services.mcp_guard import AuthError, _get_auth, effective_token, guarded  # noqa: F401

# name -> (who can call it, one line for people). quorum_help prints this table; a test keeps it equal to the registered tools.
COMMANDS: Dict[str, tuple] = {
    "quorum_help": ("anyone", "This list: every command and what it does"),
    "quorum_start": ("anyone / admin applies", "Guided setup: a few questions (team size, name, focus, capture, teammates), then it configures the workspace"),
    "quorum_status": ("anyone", "What is remembered, what will be remembered, what is shared with whom, who is connected, tokens saved"),
    "retrieve_context": ("anyone", "Look up what the team knows about a question (compact mode costs the fewest tokens)"),
    "remember_fact": ("contributor+", "Save a fact with its source; scope=team keeps it private to your team"),
    "observe": ("contributor+", "Save the durable facts already in some text, without one call per fact"),
    "derive": ("contributor+", "Record a conclusion built on parent facts, so a bad parent can take it back"),
    "stage_action": ("contributor+", "Propose an action; the safety gate decides, risky ones go to a person"),
    "execute_action": ("contributor+", "Run a staged action under the guard (refused if any fact behind it is quarantined)"),
    "quarantine": ("reviewer+", "Pull a wrong fact and everything built on it out of circulation"),
    "restore": ("reviewer+", "Bring it back, only where every parent is healthy again"),
    "explain_blast_radius": ("anyone", "Dry run: what a quarantine would touch"),
    "review_handoff": ("admin", "Approve or reject a task the gate sent to a person"),
    "claim_handoff": ("contributor+", "Take a task with an exclusive lease"),
    "get_map": ("anyone", "A few hundred tokens that orient you in the shared memory"),
    "get_node": ("anyone", "One node with its parents and children"),
    "find_path": ("anyone", "How two facts are connected (FalkorDB shortest path)"),
    "delta": ("anyone", "What stopped being true since you last looked"),
    "announce_work": ("contributor+", "Say what you are about to work on (a file, an order, a ticket); get a warning if someone else is on it or near it"),
    "recall_as_of": ("anyone", "What the team believed at an earlier time, and what happened to those facts since"),
    "quorum_manage": ("admin", "Teams, people, permission levels, tokens and sharing links, all from chat (action=overview lists what you can do)"),
    "quorum_discover": ("anyone", "Other Quorum hubs visible on this network"),
    "quorum_connect": ("admin", "Ask to join a hub you discovered; its admin is notified and approves"),
    "quorum_inbox": ("admin", "Connection requests waiting for you"),
    "quorum_respond": ("admin", "Approve or decline a connection request, choosing the permission level"),
    "quorum_connections": ("admin", "Hubs you are connected to, requests you sent, and the MCP settings to attach them"),
    "peer_retrieve": ("admin", "Ask a connected hub a question; you see only what its admin shared with you"),
}

SURVEY: List[Dict[str, Any]] = [
    {"key": "team_size", "ask": "How many people will share this memory (including you)?", "type": "number"},
    {"key": "team_name", "ask": "What should your team be called?", "type": "text"},
    {"key": "focus", "ask": "What will you mostly use it for?", "type": "choice", "options": ["customer support", "engineering", "operations", "research", "something else"]},
    {"key": "auto_remember", "ask": "Should durable facts from your chats be saved automatically (you can review and quarantine them any time)?", "type": "yes/no"},
    {"key": "look_for_teammates", "ask": "Look for teammates already running Quorum on this network?", "type": "yes/no"},
]

CLIENTS = ("claude-code", "claude-desktop", "cursor", "vscode", "windsurf", "antigravity", "chatgpt", "custom")


def _who(auth: Dict[str, Any]) -> str:
    return auth.get("agent_id") or "this token"


def _need_admin(auth: Dict[str, Any], action: str) -> Optional[Dict[str, Any]]:
    if auth["role"] != "human":
        return {"error": roles.refusal(_who(auth), auth["role"], action, "human"), "success": False}
    return None


def _fail(e: Exception) -> Dict[str, Any]:
    msg = getattr(e, "message", None) or str(e)
    return {"error": msg, "success": False}


def _yes(v: Any) -> Optional[bool]:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        t = v.strip().lower()
        if t in ("y", "yes", "true", "1", "on"):
            return True
        if t in ("n", "no", "false", "0", "off"):
            return False
    return None


def client_config(url: str, token: str) -> Dict[str, Any]:
    """Paste-ready MCP settings for a token. Shown to the person who owns it."""
    mcp = (url.rstrip("/") + "/mcp") if url else "http://localhost:8000/mcp"
    return {
        "mcp_url": mcp,
        "claude_code": f'claude mcp add --transport http quorum {mcp} --header "Authorization: Bearer {token}"',
        "json": {"mcpServers": {"quorum": {"url": mcp, "headers": {"Authorization": f"Bearer {token}"}}}},
    }


def quorum_help(token: str = "") -> Dict[str, Any]:
    """List every Quorum command and what it does. Call this first, or when you are unsure what is possible."""
    auth = _get_auth(token)
    lines = [f"{name}: {desc}  [{who}]" for name, (who, desc) in COMMANDS.items()]
    text = "Quorum commands (you are " + roles.level_name(auth["role"]) + "):\n" + "\n".join(lines) + "\nStart with quorum_start if this is a new workspace, or quorum_status to see where things stand."
    return {"text": text, "commands": [{"name": n, "who": w, "does": d} for n, (w, d) in COMMANDS.items()], "your_level": roles.level_name(auth["role"]), "tokens": estimate_tokens(text)}


def quorum_start(
    team_size: int = 0,
    team_name: str = "",
    focus: str = "",
    auto_remember: Optional[bool] = None,
    look_for_teammates: Optional[bool] = None,
    token: str = "",
) -> Dict[str, Any]:
    """
    Guided setup. Call with no arguments to get the questions: ask the person each one in plain words, then call again
    with their answers (team_size, team_name, focus, auto_remember, look_for_teammates). Safe to run again.
    """
    auth = _get_auth(token)
    store = get_graph_store(auth["tenant"])
    with store._lock:
        survey = fed._fed(store)["survey"]
    if not team_size and not team_name:
        text = (
            "Welcome to Quorum: shared memory your AIs and teammates can trust, with a one-step undo for wrong facts.\n"
            "Ask these questions one at a time, then call quorum_start again with the answers:\n"
            + "\n".join(f"{i + 1}. {q['ask']}" + (f" ({'/'.join(q['options'])})" if q.get("options") else "") for i, q in enumerate(SURVEY))
            + "\nCommands: call quorum_help."
        )
        done = survey.get("done_at")
        return {"text": text, "survey": SURVEY, "already_set_up": bool(done), "previous_answers": {k: v for k, v in survey.items() if k != "done_at"}, "tokens": estimate_tokens(text)}
    denied = _need_admin(auth, "set up the workspace")
    if denied:
        return denied
    try:
        size = int(team_size or 5)
        if not 1 <= size <= 200:
            raise wsvc.WorkspaceError("VALIDATION", "Team size must be between 1 and 200.")
        name = (team_name or "").strip() or "My team"
        with store._lock:
            ws = wsvc._ws(store)
            if ws["teams"]:
                t = ws["teams"][0]
                t["name"] = wsvc._clean_name(name, "team")
                used = sum(1 for m in ws["members"] if m["team_id"] == t["id"])
                t["seats"] = max(size, used, 1)
            else:
                wsvc.add_team(store, name, size)
            survey.update({
                "team_size": size, "team_name": name, "focus": (focus or "").strip()[:40] or "something else",
                "auto_remember": _yes(auto_remember) if auto_remember is not None else True,
                "look_for_teammates": _yes(look_for_teammates) if look_for_teammates is not None else True, "done_at": int(time.time() * 1000),
            })
            looking = survey["look_for_teammates"]
            capture = survey["auto_remember"]
        peers = fed.list_peers(store) if looking else []
    except (wsvc.WorkspaceError, fed.FedError) as e:
        return _fail(e)
    lines = [f"Set up: team '{name}' with room for {size}.", f"Focus: {survey['focus']}."]
    lines.append(
        "Automatic capture is ON: install the capture hook (scripts/quorum_observer.py) or call observe on what you write; everything captured can be reviewed and quarantined."
        if capture else "Automatic capture is OFF: facts are saved only when you or an agent call remember_fact or observe."
    )
    if looking:
        lines.append("Teammates on this network: " + (", ".join(p["name"] for p in peers) if peers else "none seen yet (they need Quorum running with the beacon; ask again with quorum_discover)."))
    lines.append("Next: add people with quorum_manage(action='member_add'), or connect to a visible hub with quorum_connect. quorum_status shows the state any time.")
    text = "\n".join(lines)
    return {"text": text, "success": True, "peers": peers, "tokens": estimate_tokens(text)}


def quorum_status(token: str = "") -> Dict[str, Any]:
    """Where things stand, in plain words: what is remembered, what will be, what is shared and with whom, who is connected."""
    auth = _get_auth(token)
    store = get_graph_store(auth["tenant"])
    viewer = wsvc.viewer_for(store, auth.get("member_id"))
    with store._lock:
        visible = [n for n in store.nodes.values() if viewer.can_see(n)]
        facts = [n for n in visible if n.kind == NodeKind.FACT]
        derived = [n for n in visible if n.kind not in (NodeKind.FACT, NodeKind.ACTION)]
        actions = [n for n in visible if n.kind == NodeKind.ACTION]
        bad = [n for n in visible if n.status == Status.QUARANTINED]
        stale = [n for n in visible if getattr(n, "stale", False) and n.status == Status.HEALTHY]
        pending_handoffs = sum(1 for h in store.handoffs if getattr(h.status, "value", h.status) == "PENDING")
        survey = dict(fed._fed(store)["survey"])
        epoch = store.epoch
        saved = sum(c["baseline"] - c["tokens"] for c in store.calls if c.get("baseline", 0) > 0 and c.get("member_id") == auth.get("member_id"))
        wsv = wsvc.public_view(store)
        me = next((m for m in wsv["members"] if m["id"] == auth.get("member_id")), None)
        team = next((t["name"] for t in wsv["teams"] if t["id"] == (me or {}).get("team_id")), None)
        links = [
            lk for lk in wsv["links"]
            if not auth.get("member_id") or auth["member_id"] in (lk["a"], lk["b"])
        ]
    names = {m["id"]: m["name"] for m in wsv["members"]}
    shared = []
    for lk in links:
        peer_id = lk["b"] if lk["a"] == auth.get("member_id") else lk["a"]
        shared.append(f"{names.get(peer_id, peer_id)} ({lk['scope']}{', paused' if lk['paused'] else ''})")
    inbox_n = fed.pending_count(store)
    conns = fed.connections(store)
    capture = survey.get("auto_remember")
    lines = [
        f"You: {_who(auth)}, {roles.level_name(auth['role'])}" + (f", team {team}" if team else "") + f". Workspace: {wsv['org']}.",
        f"Remembered: {len(facts)} facts, {len(derived)} conclusions, {len(actions)} actions. Quarantined: {len(bad)}. Stale: {len(stale)}. Memory version {epoch}.",
        "Will be remembered: " + (
            "facts you or your agents save (remember_fact, observe)" + (", plus durable facts captured automatically from chats (capture is on)." if capture else ". Automatic capture is off.")
        ),
        "Shared with: " + (", ".join(shared) if shared else (f"your team{' ' + team if team else ''} only (scope=team facts); facts saved without a team scope are visible to everyone in the workspace.")),
        f"Waiting on a person: {pending_handoffs} review/task item(s)." if pending_handoffs else "Nothing waiting on a person.",
        f"Tokens saved for you so far: {max(saved, 0):,} versus a typical top-8 retriever.",
    ]
    if auth["role"] == "human":
        lines.append(f"People: {len(wsv['members'])} in {len(wsv['teams'])} team(s). Connection requests waiting: {inbox_n}. Connected hubs: {', '.join(c['peer_name'] for c in conns) or 'none'}.")
    text = "\n".join(lines)
    return {
        "text": text, "epoch": epoch, "tokens": estimate_tokens(text),
        "counts": {"facts": len(facts), "conclusions": len(derived), "actions": len(actions), "quarantined": len(bad), "stale": len(stale)},
        "shared_with": shared, "capture": capture, "connections": [c["peer_name"] for c in conns], "requests_waiting": inbox_n,
    }


# ---------- quorum_manage ----------
def _team(store, ref: str) -> Dict[str, Any]:
    r = (ref or "").strip().lower()
    teams = wsvc._ws(store)["teams"]
    hit = next((t for t in teams if t["id"] == ref or t["name"].lower() == r), None) or (teams[0] if (not r and len(teams) == 1) else None)
    if not hit:
        raise wsvc.WorkspaceError("NOT_FOUND", f"No team called '{ref}'. Teams: {', '.join(t['name'] for t in teams) or 'none yet (action=team_create)'}.", 404)
    return hit


def _member(store, ref: str) -> Dict[str, Any]:
    r = (ref or "").strip().lower()
    ms = wsvc._ws(store)["members"]
    hit = next((m for m in ms if m["id"] == ref or m["name"].lower() == r), None)
    if not hit:
        raise wsvc.WorkspaceError("NOT_FOUND", f"No member called '{ref}'. Members: {', '.join(m['name'] for m in ms) or 'none yet'}.", 404)
    return hit


def _role(value: str) -> str:
    v = (value or "").strip().lower()
    alias = {"contributor": "agent", "reviewer": "auditor", "admin": "human"}
    v = alias.get(v, v)
    if v not in wsvc.ROLES:
        raise wsvc.WorkspaceError("VALIDATION", "Level must be contributor, reviewer or admin.")
    return v


ACTIONS = (
    "overview", "team_create", "team_seats", "team_remove", "member_add", "member_set_level", "member_move", "member_new_token", "member_remove", "leaver_report",
    "link", "pause_link", "resume_link", "unlink",
)


def quorum_manage(
    action: str = "overview",
    name: str = "",
    team: str = "",
    member: str = "",
    level: str = "",
    seats: int = 0,
    client: str = "custom",
    other: str = "",
    scope: str = "team",
    token: str = "",
) -> Dict[str, Any]:
    """
    Run the workspace from chat (Admin only except overview). action is one of: overview, team_create(name, seats),
    team_seats(team, seats), team_remove(team), member_add(name, team, level, client), member_set_level(member, level),
    member_move(member, team), member_new_token(member), leaver_report(member) (what leaves with them), member_remove(member), link(member, other, scope team|commons),
    pause_link(member, other), resume_link(member, other), unlink(member, other). level is contributor, reviewer or admin.
    A new token is shown once.
    """
    auth = _get_auth(token)
    store = get_graph_store(auth["tenant"])
    act = (action or "overview").strip().lower()
    if act not in ACTIONS:
        return {"error": f"Unknown action '{action}'. Use one of: {', '.join(ACTIONS)}.", "success": False}
    if act != "overview":
        denied = _need_admin(auth, "manage people and teams")
        if denied:
            return denied
    elif auth["role"] == "agent":
        return {"error": roles.refusal(_who(auth), auth["role"], "see the whole team", "auditor", "human"), "success": False}
    out: Dict[str, Any] = {"success": True}
    msg = ""
    try:
        if act == "overview":
            v = wsvc.public_view(store)
            nm = {t["id"]: t["name"] for t in v["teams"]}
            lines = [f"{v['org']} ({v['limits']['label']} plan)"]
            for t in v["teams"]:
                ppl = [f"{m['name']} [{roles.level_name(m['role'])}, {m['status']}]" for m in v["members"] if m["team_id"] == t["id"]]
                lines.append(f"- {t['name']} ({len(ppl)}/{t['seats']}): " + (", ".join(ppl) or "no one yet"))
            mn = {m["id"]: m["name"] for m in v["members"]}
            for lk in v["links"]:
                lines.append(f"- link {mn.get(lk['a'], '?')} <-> {mn.get(lk['b'], '?')} ({lk['scope']}{', paused' if lk['paused'] else ''})")
            lines.append("Actions: " + ", ".join(ACTIONS[1:]))
            msg = "\n".join(lines)
            out["teams"] = [{"name": t["name"], "seats": t["seats"]} for t in v["teams"]]
            out["members"] = [{"name": m["name"], "level": roles.level_name(m["role"]), "team": nm.get(m["team_id"], ""), "status": m["status"]} for m in v["members"]]
        elif act == "team_create":
            t = wsvc.add_team(store, name, seats or 5)
            msg = f"Team '{t['name']}' created with {t['seats']} seats."
        elif act == "team_seats":
            t = wsvc.set_seats(store, _team(store, team or name)["id"], seats)
            msg = f"{t['name']} now has {t['seats']} seats."
        elif act == "team_remove":
            t = _team(store, team or name)
            wsvc.remove_team(store, t["id"])
            msg = f"Team '{t['name']}' and its members were removed."
        elif act == "member_add":
            c = client if client in wsvc.CLIENTS else "custom"
            m, tok = wsvc.add_member(store, name, _role(level or "contributor"), _team(store, team)["id"], c, "")
            cfg = client_config(fed.public_url(), tok)
            msg = (
                f"Added {m['name']} as {roles.level_name(m['role'])}. Their token (shown once, treat as a password): {tok}\n"
                f"Their AI setup: {cfg['claude_code']}\nOther clients: paste {json.dumps(cfg['json'])}"
            )
            out["member"] = m["name"]
            out["token"] = tok
            out["config"] = cfg
        elif act == "member_set_level":
            m, before = wsvc.update_member(store, _member(store, member or name)["id"], role=_role(level))
            msg = f"{m['name']} changed from {roles.level_name(before['role'])} to {roles.level_name(m['role'])}. It applies on their next call."
        elif act == "member_move":
            m, _ = wsvc.update_member(store, _member(store, member or name)["id"], team_id=_team(store, team)["id"])
            msg = f"{m['name']} moved to {_team(store, m['team_id'])['name']}."
        elif act == "member_new_token":
            m, tok = wsvc.rotate_token(store, _member(store, member or name)["id"])
            cfg = client_config(fed.public_url(), tok)
            msg = f"New token for {m['name']} (the old one stopped working; shown once): {tok}\n{cfg['claude_code']}"
            out["token"] = tok
            out["config"] = cfg
        elif act == "leaver_report":
            from app.services.mcp_coord import leaver_report

            rep = leaver_report(store, _member(store, member or name))
            msg = rep["text"]
            out.update({k: rep[k] for k in ("owned", "sole_source", "facts", "tasks_held", "successors")})
        elif act == "member_remove":
            from app.services.mcp_coord import leaver_report

            m = _member(store, member or name)
            rep = leaver_report(store, m)
            wsvc.remove_member(store, m["id"])
            msg = f"{m['name']} was removed and their token revoked."
            if rep["sole_source"] or rep["tasks_held"]:
                msg += f" Heads up: {rep['sole_source']} fact(s) had no other source and {len(rep['tasks_held'])} task(s) were held by them; they stay in memory with provenance. Next time run leaver_report first."
        else:
            a, b = _member(store, member or name), _member(store, other)
            if act == "link":
                wsvc.link(store, a["id"], b["id"], scope)
                msg = f"{a['name']} and {b['name']} now share {scope} context."
            else:
                lk = next((x for x in wsvc._ws(store)["links"] if {x["a"], x["b"]} == {a["id"], b["id"]}), None)
                if not lk:
                    raise wsvc.WorkspaceError("NOT_FOUND", "Those two are not linked.", 404)
                if act == "unlink":
                    wsvc.unlink(store, lk["id"])
                    msg = f"Link between {a['name']} and {b['name']} removed."
                else:
                    wsvc.set_link_paused(store, lk["id"], act == "pause_link")
                    msg = f"Link between {a['name']} and {b['name']} {'paused' if act == 'pause_link' else 'resumed'}."
    except (wsvc.WorkspaceError, fed.FedError) as e:
        return _fail(e)
    if act != "overview":
        from app.services.event_bus import event_bus

        event_bus.publish(auth["tenant"], "workspace_changed", {})
    out["text"] = msg
    out["tokens"] = estimate_tokens(msg)
    return out


# ---------- connecting hubs ----------
def quorum_discover(token: str = "") -> Dict[str, Any]:
    """Other Quorum hubs visible on this network. Use the name with quorum_connect."""
    auth = _get_auth(token)
    store = get_graph_store(auth["tenant"])
    fed.refresh_outbox(store)
    peers = fed.list_peers(store)
    if not peers:
        text = "No other Quorum hub is visible on this network yet. They need Quorum running with the beacon (scripts/setup does this) on the same Wi-Fi or LAN."
    else:
        text = "Visible on this network:\n" + "\n".join(
            f"- {p['name']} ({p['url']})" + (" connected" if p["connected"] else " request sent, waiting for approval" if p["requested"] else "") for p in peers
        ) + "\nTo join one: quorum_connect(peer='<name>'). Their admin is notified and decides."
    return {"text": text, "peers": [{"name": p["name"], "connected": p["connected"], "requested": p["requested"]} for p in peers], "tokens": estimate_tokens(text)}


def quorum_connect(peer: str, message: str = "", token: str = "") -> Dict[str, Any]:
    """Ask to join another hub, by the name quorum_discover shows or by its address (http://192.168.1.20:8000) on networks that block discovery. Its admin is notified and approves or declines; check with quorum_connections."""
    auth = _get_auth(token)
    denied = _need_admin(auth, "connect to another hub")
    if denied:
        return denied
    store = get_graph_store(auth["tenant"])
    try:
        r = fed.request_connect(store, peer, _who(auth), message)
    except fed.FedError as e:
        return _fail(e)
    text = f"Request sent to {r['peer']}. Their admin has been notified. Call quorum_connections to see when they approve; nothing else is needed from you."
    return {"text": text, "success": True, "peer": r["peer"], "status": "pending", "tokens": estimate_tokens(text)}


def quorum_inbox(token: str = "") -> Dict[str, Any]:
    """Connection requests waiting for you to approve or decline."""
    auth = _get_auth(token)
    denied = _need_admin(auth, "review connection requests")
    if denied:
        return denied
    rows = fed.inbox(get_graph_store(auth["tenant"]))
    if not rows:
        text = "No connection requests waiting."
    else:
        text = "Waiting for you:\n" + "\n".join(f"- {r['id']}: {r['person']} from {r['from_hub'] or 'another hub'}" + (f' says "{r["message"]}"' if r["message"] else "") for r in rows) + "\nAnswer with quorum_respond(request_id, decision='approve'|'decline', level='contributor'|'reviewer'|'admin')."
    return {"text": text, "requests": rows, "tokens": estimate_tokens(text)}


def quorum_respond(request_id: str, decision: str, level: str = "contributor", team: str = "", token: str = "") -> Dict[str, Any]:
    """Approve or decline a connection request. Approving adds them as a member at the level you choose (default Contributor)."""
    auth = _get_auth(token)
    denied = _need_admin(auth, "answer connection requests")
    if denied:
        return denied
    store = get_graph_store(auth["tenant"])
    d = (decision or "").strip().lower()
    if d not in ("approve", "decline"):
        return {"error": "decision must be 'approve' or 'decline'.", "success": False}
    try:
        tid = _team(store, team)["id"] if team else None
        r = fed.respond(store, request_id, d == "approve", _role(level), tid)
    except (wsvc.WorkspaceError, fed.FedError) as e:
        return _fail(e)
    text = (
        f"{r['person']} approved as {roles.level_name(_role(level))}. They are notified and receive their access automatically."
        if d == "approve" else f"Request from {r['person']} declined."
    )
    return {"text": text, "success": True, "status": r["status"], "tokens": estimate_tokens(text)}


def quorum_connections(reveal: bool = False, token: str = "") -> Dict[str, Any]:
    """Hubs you are connected to and requests you sent. reveal=True also prints the MCP settings (with the token) to attach a hub to an AI client."""
    auth = _get_auth(token)
    denied = _need_admin(auth, "see connections")
    if denied:
        return denied
    store = get_graph_store(auth["tenant"])
    changes = fed.refresh_outbox(store)
    conns = fed.connections(store)
    out_rows = [o for o in fed.outbox(store) if o["status"] != "approved"]
    lines = []
    for c in conns:
        lines.append(f"- Connected to {c['peer_name']} as {c['as_member'] or 'you'} ({roles.level_name(c['role'])}{', team ' + c['team'] if c['team'] else ''}). Ask it with peer_retrieve(peer='{c['peer_name']}', query=...).")
    for o in out_rows:
        lines.append(f"- Request to {o['peer_name']}: {o['status']}")
    text = "\n".join(lines) or "No connections yet. quorum_discover shows hubs you can join."
    out: Dict[str, Any] = {"text": text, "connections": conns, "requests": [{"peer": o["peer_name"], "status": o["status"]} for o in out_rows], "changed": changes}
    if reveal:
        out["config"] = {c["peer_name"]: client_config(fed.connection_config(store, c["peer_name"])["url"], fed.connection_config(store, c["peer_name"])["token"]) for c in conns}
    out["tokens"] = estimate_tokens(text)
    return out


def peer_retrieve(peer: str, query: str, k: int = 3, token: str = "") -> Dict[str, Any]:
    """Ask a connected hub a question. You get only what its admin allows your member to see."""
    auth = _get_auth(token)
    denied = _need_admin(auth, "query a connected hub")
    if denied:
        return denied
    store = get_graph_store(auth["tenant"])
    try:
        r = fed.peer_post(store, peer, "/api/context/retrieve", {"query": query, "k": max(1, min(int(k or 3), 10)), "format": "compact"})
    except fed.FedError as e:
        return _fail(e)
    packed = r.get("packed") or ""
    text = f"From {peer} ({r.get('count', 0)} match(es)):\n{packed}" if packed else f"{peer} has nothing relevant for that question."
    return {"text": text, "from": peer, "count": r.get("count", 0), "tokens": estimate_tokens(text), "tokens_baseline": r.get("tokens_baseline", 0)}


def notice_for(auth: Optional[Dict[str, Any]]) -> Optional[str]:
    """One line added to an admin's tool results while connection requests wait, so they find out in the chat they are already in."""
    if not auth or auth.get("role") != "human" or not settings.DISCOVERY:
        return None
    try:
        n = fed.pending_count(get_graph_store(auth["tenant"]))
    except Exception:  # noqa: BLE001 - a notice must never break a tool call
        return None
    return f"{n} connection request(s) waiting for you: call quorum_inbox." if n else None


__all__ = [
    "COMMANDS", "SURVEY", "quorum_help", "quorum_start", "quorum_status", "quorum_manage", "quorum_discover", "quorum_connect", "quorum_inbox",
    "quorum_respond", "quorum_connections", "peer_retrieve", "notice_for", "effective_token", "guarded", "AuthError",
]
