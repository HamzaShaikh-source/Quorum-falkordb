"""
Coordination and time: announce_work (warn before two agents collide) and recall_as_of (what the team believed then).

announce_work is the general form of a file-conflict warning: the target can be a file, an order, a ticket, a customer. It is
ephemeral on purpose (an expiring note, not a fact), so it never pollutes memory. recall_as_of reads the same bitemporal data the
console's time-travel slider uses: a fact that was believed at that time is returned even if it has been quarantined or replaced
since, and it says so.
"""
import re
import time
from typing import Any, Dict, List, Optional

from app.graph.queries import get_graph_store
from app.models.common import NodeKind, Status
from app.services import context_pack as cp
from app.services.context_pack import estimate_tokens
from app.services.mcp_guard import _get_auth, viewer_of as _viewer

MAX_NOTES_PER_TENANT = 500


def _norm(target: str) -> str:
    return re.sub(r"\s+", " ", (target or "").strip().lower().replace("\\", "/"))[:200]


def _related(a: str, b: str) -> bool:
    """The same thing, or one is inside the other (src/payments and src/payments/discount.py)."""
    if a == b:
        return True
    return a.startswith(b.rstrip("/") + "/") or b.startswith(a.rstrip("/") + "/")


CAUSAL = ("LEADS_TO", "SUPPORTS", "FROM_SOURCE")
GRAPH_HOPS = 3


def _graph_neighbors(store, tgt: str, hops: int = GRAPH_HOPS):
    """
    When the target is a node in the memory graph, the nodes within `hops` causal steps of it: {lower-case id: (relation, hops)}.
    This is what makes a collision warning understand dependency, not only names: revising a fact is a conflict with someone deriving
    from it, or from something it was derived from, even though no name matches. Runs as a FalkorDB traversal when available.
    """
    ids = {i.lower(): i for i in store.nodes}
    nid = ids.get(tgt)
    if nid is None:
        return {}
    down = store.closure_in_falkordb(nid, CAUSAL, "out")
    if down is None:
        down = store._process_closure(nid, CAUSAL)
    up = store.closure_in_falkordb(nid, CAUSAL, "in")
    if up is None:
        rev: Dict[str, List[str]] = {}
        for e in store.edges:
            if e.type.value in CAUSAL:
                rev.setdefault(e.target, []).append(e.source)
        up, frontier, depth = {}, [nid], 0
        while frontier and depth < hops:
            depth += 1
            nxt = []
            for cur in frontier:
                for p in rev.get(cur, []):
                    if p != nid and p not in up:
                        up[p] = depth
                        nxt.append(p)
            frontier = nxt
    out = {}
    for i, d in down.items():
        if d <= hops:
            out[i.lower()] = ("downstream", d)
    for i, d in up.items():
        if d <= hops:
            out.setdefault(i.lower(), ("upstream", d))
    return out


def announce_work(target: str, intent: str = "", token: str = "", minutes: int = 30, done: bool = False) -> Dict[str, Any]:
    """
    Say what you are about to work on (a file, an order, a ticket, or a fact id). Returns who else announced the same thing, something
    inside or around it, or (when the target is a fact id) work on a fact within 3 causal hops of it, upstream or downstream, in the
    last `minutes` (default 30), so you can pick something else or coordinate. done=true removes your note.
    Notes expire by themselves and are not stored as facts.
    """
    auth = _get_auth(token)
    store = get_graph_store(auth["tenant"])
    tgt = _norm(target)
    if not tgt:
        return {"error": "Say what you are working on (a file, an order, a ticket).", "success": False}
    me = auth.get("member_id") or auth.get("agent_id") or "agent"
    name = auth.get("agent_id") or "agent"
    now = time.time()
    ttl = max(1, min(int(minutes or 30), 24 * 60)) * 60
    with store._lock:
        notes: List[Dict[str, Any]] = store.work
        notes[:] = [n for n in notes if now - n["ts"] <= n["ttl"]]
        if done:
            notes[:] = [n for n in notes if not (n["who"] == me and _related(n["target"], tgt) and n["target"] == tgt)]
            return {"success": True, "released": target, "text": f"Released {target}.", "tokens": 8}
        others = [n for n in notes if n["who"] != me and _related(n["target"], tgt)]
        graph = {}
        rest = [n for n in notes if n["who"] != me and n not in others]
        if rest and any(n["target"] in {i.lower() for i in store.nodes} for n in rest):
            graph = _graph_neighbors(store, tgt)
        for n in rest:
            if n["target"] in graph:
                n["_rel"] = graph[n["target"]]
                others.append(n)
        notes[:] = [n for n in notes if not (n["who"] == me and n["target"] == tgt)]
        notes.append({"who": me, "name": name, "target": tgt, "raw": target.strip()[:200], "intent": (intent or "").strip()[:200], "ts": now, "ttl": ttl})
        del notes[: max(0, len(notes) - MAX_NOTES_PER_TENANT)]
    conflicts = []
    for n in sorted(others, key=lambda x: -x["ts"]):
        c = {"agent": n["name"], "target": n["raw"], "intent": n["intent"], "minutes_ago": round((now - n["ts"]) / 60, 1), "same_target": n["target"] == tgt}
        if "_rel" in n:
            c["relation"], c["hops"] = n["_rel"]
        conflicts.append(c)
    for n in others:
        n.pop("_rel", None)
    if conflicts:
        c = conflicts[0]
        what = "the same work" if c["same_target"] else (f"{c['target']}, {c['hops']} hop(s) {c['relation']} of yours" if "relation" in c else c["target"])
        text = f"Heads up: {c['agent']} announced {what} {c['minutes_ago']} min ago" + (f" ({c['intent']})" if c["intent"] else "") + ". Coordinate or pick something else."
    else:
        text = f"Clear: nobody else is on {target} right now."
    return {"success": True, "clear": not conflicts, "conflicts": conflicts, "text": text, "tokens": estimate_tokens(text)}


def recall_as_of(query: str, token: str = "", as_of: Optional[int] = None, minutes_ago: Optional[float] = None, k: int = 5) -> Dict[str, Any]:
    """
    What the team believed at an earlier time. Give as_of (milliseconds since the epoch) or minutes_ago. Facts that were believed
    then are returned even if they have since been quarantined or replaced, and each says what happened to it afterwards.
    """
    auth = _get_auth(token)
    store = get_graph_store(auth["tenant"])
    viewer = _viewer(auth, store)
    if as_of is None and minutes_ago is None:
        return {"error": "Give as_of (milliseconds) or minutes_ago.", "success": False}
    t = int(as_of) if as_of is not None else int(time.time() * 1000 - float(minutes_ago or 0) * 60_000)
    k = max(1, min(int(k or 5), 20))
    with store._lock:
        then = [n for n in store.get_as_of(t) if n.kind == NodeKind.FACT and viewer.can_see(n)]
        idf = cp.idf_weights(then)
        ranked = [(n, sc) for n, sc, _ in cp.rank(then, query, idf) if sc > 0][:k]
        out: List[Dict[str, Any]] = []
        for n, _sc in ranked:
            if n.status == Status.QUARANTINED:
                since = "quarantined since"
            elif n.status == Status.SUPERSEDED or (n.valid_to is not None and n.valid_to > t):
                since = "replaced since"
            else:
                since = "still believed"
            out.append({"id": n.id, "key": n.key, "value": n.value, "text": n.text, "quote": n.quote, "now": since})
    lines = [f"- {o['id']}: {o['text']} [{o['now']}]" for o in out]
    text = (f"Believed at {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(t / 1000))} ({len(out)} match(es)):\n" + "\n".join(lines)) if out else "Nothing relevant was believed at that time."
    return {"success": True, "as_of": t, "outcome": "ANSWERED" if out else "NOT_IN_MEMORY", "facts": out, "text": text, "tokens": estimate_tokens(text)}


def leaver_report(store, member: Dict[str, Any]) -> Dict[str, Any]:
    """
    What leaves with a person: the facts and conclusions only they recorded, how much is built on them (a graph traversal in
    FalkorDB), tasks they hold, and who on their team already read the same material and could take over.
    """
    name = member["name"]
    with store._lock:
        mine = [n for n in store.nodes.values() if n.asserted_by and n.asserted_by.id == name and n.kind in (NodeKind.FACT, NodeKind.CONCLUSION)]
        corroborated = {
            (n.key, n.value)
            for n in store.nodes.values()
            if n.kind == NodeKind.FACT and n.key and n.status == Status.HEALTHY and (n.asserted_by.id if n.asserted_by else None) != name
        }
        rows: List[Dict[str, Any]] = []
        for n in mine[:300]:
            if n.status != Status.HEALTHY:
                continue
            desc = store.get_descendants(n.id, edge_types=["LEADS_TO", "SUPPORTS"])
            sole = n.kind == NodeKind.FACT and (n.key, n.value) not in corroborated
            rows.append({
                "id": n.id, "text": (n.text or n.label or "")[:90], "sole_source": sole,
                "dependents": len(desc), "actions_depending": sum(1 for d in desc if d.kind == NodeKind.ACTION),
            })
        rows.sort(key=lambda r: (-int(r["sole_source"]), -r["dependents"], r["id"]))
        held = [h.id for h in store.handoffs if h.claimed_by in (name, member["id"]) and getattr(h.status, "value", h.status) not in ("COMPLETED", "REJECTED")]
        mine_ids = {r["id"] for r in rows}
        team = [m for m in store.workspace.get("members", []) if m["team_id"] == member["team_id"] and m["id"] != member["id"]]
        cands = []
        for m in team:
            seen = set((store.agent_state.get(m["id"]) or {}).get("ids", []))
            cands.append({"name": m["name"], "already_read": len(seen & mine_ids)})
        cands.sort(key=lambda c: (-c["already_read"], c["name"]))
    sole_n = sum(1 for r in rows if r["sole_source"])
    lines = [f"If {name} leaves: {len(rows)} healthy facts or conclusions are theirs; {sole_n} have no other source."]
    for r in rows[:5]:
        lines.append(f"- {r['id']}: {r['text']} ({'only source, ' if r['sole_source'] else ''}{r['dependents']} built on it, {r['actions_depending']} action(s))")
    if held:
        lines.append(f"Tasks they hold: {', '.join(held)} (release or reassign).")
    if cands:
        lines.append("Best successors: " + ", ".join(f"{c['name']} (has read {c['already_read']})" for c in cands[:3]) + ".")
    elif rows:
        lines.append("No one else is on their team to hand over to.")
    text = "\n".join(lines)
    return {"success": True, "member": name, "owned": len(rows), "sole_source": sole_n, "facts": rows[:20], "tasks_held": held, "successors": cands[:5], "text": text, "tokens": estimate_tokens(text)}
