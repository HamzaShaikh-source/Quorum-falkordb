"""Read tools: retrieve_context, get_map, get_node, find_path, delta."""
import json
from typing import Any, Dict, List, Optional

from app.graph.queries import get_graph_store
from app.models.common import Status
from app.services import context_pack as cp
from app.services.context_pack import estimate_tokens
from app.services.mcp_guard import AuthError, _get_auth, effective_token, emit as _emit, guarded, viewer_of as _viewer  # noqa: F401


def _agent_key(auth: Dict[str, Any]) -> str:
    return auth.get("member_id") or auth.get("agent_id") or "agent"


def _remember_delivery(store, auth: Dict[str, Any], ids: List[str], query: Optional[str] = None) -> None:
    with store._lock:
        st = store.agent_state.setdefault(_agent_key(auth), {"ids": [], "query": "", "epoch": store.epoch})
        existing = list(st.get("ids", []))
        incoming_set = set(ids)
        merged = [i for i in existing if i not in incoming_set] + list(ids)
        st["ids"] = merged[-500:]
        st["epoch"] = store.epoch
        if query is not None:
            st["query"] = query


def retrieve_context(
    query: str,
    token: str = "",
    k: int = 5,
    format: str = "full",
    budget_tokens: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Healthy, unsuperseded, in-scope context for a question, best match first.
    format="full" returns the matching facts as JSON (the default, for existing callers).
    format="compact" returns one line per node with short ids and the ancestors that prove each one, within
    budget_tokens (default 400). It costs a fraction of the tokens: prefer it, and cite the ids it gives you.
    Every response carries `tokens` (its size) and `tokens_baseline` (what a typical top-8 retriever would have sent).
    """
    auth = _get_auth(token)
    store = get_graph_store(auth["tenant"])
    viewer = _viewer(auth, store)

    if format == "compact":
        p = cp.pack(store, query, budget=max(60, int(budget_tokens or 400)), viewer=viewer)
        _remember_delivery(store, auth, p["ids"], query)
        out = {
            "epoch": store.epoch,
            "format": "compact",
            "count": len(p["ids"]),
            "matched": p["matched"],
            "truncated": p["truncated"],
            "packed": p["text"],
            "ids": p["short_ids"],
        }
        out["tokens"] = estimate_tokens(json.dumps(out))
        out["tokens_baseline"] = p["tokens_baseline"]
        return out

    with store._lock:
        candidates = store.nodes.candidates(viewer)
        cache_key = (store.nodes.revision, query, viewer.scoped, viewer.team_id, tuple(sorted(viewer.linked_teams)))
        baseline = store.baseline_cache.get(cache_key)
        if baseline is None:
            nodes = [n for n in store.nodes.values() if viewer.can_see(n)]
            baseline = cp.baseline_tokens(nodes, query)
            if len(store.baseline_cache) >= 32:
                store.baseline_cache.clear()
            store.baseline_cache[cache_key] = baseline
    idf = cp.idf_weights(candidates)
    ranked = [(n, sc) for n, sc, _ in cp.rank(candidates, query, idf)]
    matched = [
        {
            "id": f.id,
            "key": f.key,
            "value": f.value,
            "text": f.text,
            "quote": f.quote,
            "asserted_by": f.asserted_by.model_dump() if f.asserted_by else None,
        }
        for f, score in ranked
        if score > 0
    ]
    k_val = max(1, int(k or 5)) if not isinstance(k, int) else max(1, k)
    out = {"epoch": store.epoch, "count": len(matched), "context": matched[:k_val]}
    _remember_delivery(store, auth, [m["id"] for m in out["context"]], query)
    out["tokens"] = estimate_tokens(json.dumps(out))
    out["tokens_baseline"] = baseline
    return out


def get_map(token: str = "") -> Dict[str, Any]:
    """A small index of the shared memory: areas, hubs and how to ask. Read it once per session instead of loading the graph."""
    auth = _get_auth(token)
    store = get_graph_store(auth["tenant"])
    m = cp.build_map(store, _viewer(auth, store))
    return {"epoch": m["epoch"], "map": m["text"], "tokens": m["tokens"], "tokens_baseline": m["tokens_baseline"]}


def get_node(id: str, token: str = "") -> Dict[str, Any]:
    """One node, its parents and its children, by short id (F3, C2) or full id. A few lines instead of a document."""
    auth = _get_auth(token)
    store = get_graph_store(auth["tenant"])
    v = cp.node_view(store, id, _viewer(auth, store))
    if v.get("success"):
        _remember_delivery(store, auth, [v["id"]])
    return v


def find_path(from_id: str, to_id: str, token: str = "") -> Dict[str, Any]:
    """The shortest causal chain between two nodes: provenance without the whole subgraph."""
    auth = _get_auth(token)
    store = get_graph_store(auth["tenant"])
    return cp.find_path(store, from_id, to_id, _viewer(auth, store))


def delta(token: str = "") -> Dict[str, Any]:
    """
    What changed since you last read: facts you were given that are no longer valid (drop them), and new matches for
    your last question. Call it when the epoch in any response changes, instead of re-reading everything.
    """
    auth = _get_auth(token)
    store = get_graph_store(auth["tenant"])
    viewer = _viewer(auth, store)
    with store._lock:
        st = store.agent_state.get(_agent_key(auth))
        delivered = set(st["ids"]) if st else set()
        last_query = st["query"] if st else ""
    if not st:
        text = "nothing read yet: call retrieve_context first"
        return {"epoch": store.epoch, "text": text, "invalidated": [], "tokens": estimate_tokens(text), "tokens_baseline": 0}
    with store._lock:
        nodes = {n.id: n for n in store.nodes.values()}
    ids = cp.short_ids([n for n in nodes.values() if viewer.can_see(n)])
    gone: List[str] = []
    for i in sorted(delivered):
        n = nodes.get(i)
        if n is None or not viewer.can_see(n) or n.status != Status.HEALTHY or n.stale:
            why = "removed" if n is None else (n.status.value.lower() if n.status != Status.HEALTHY else "stale")
            gone.append(f"✗ {ids.get(i, i)} no longer valid ({why})")
    still = {i for i in delivered if i in nodes and nodes[i].status == Status.HEALTHY and not nodes[i].stale and viewer.can_see(nodes[i])}
    p = cp.pack(store, last_query, budget=300, viewer=viewer, have_ids=still)
    body = [f"# delta · epoch {store.epoch}"] + gone + (p["text"].split("\n")[1:] if p["ids"] else [])
    if len(body) == 1:
        body.append("no changes")
    text = "\n".join(body)
    _remember_delivery(store, auth, p["ids"])
    with store._lock:
        st_entry = store.agent_state.get(_agent_key(auth))
        existing = list(st_entry.get("ids", [])) if st_entry else []
        still_set = set(still)
        incoming_set = set(p["ids"])
        merged = [i for i in existing if i in still_set and i not in incoming_set] + list(p["ids"])
        store.agent_state[_agent_key(auth)]["ids"] = merged[-500:]
    return {
        "epoch": store.epoch,
        "text": text,
        "invalidated": [g.split(" ")[1] for g in gone],
        "new": p["short_ids"],
        "tokens": estimate_tokens(text),
        "tokens_baseline": p["tokens_baseline"],
    }
