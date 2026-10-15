"""
Token-aware context for agents.

What an agent should receive is the smallest slice of the graph that answers its question, in a format that costs few
tokens, with the provenance that proves where each line came from. This module builds that slice:

  pack()        healthy, in-scope nodes ranked by relevance, plus ancestors, one compact line each, within a budget
  build_map()   a small index of where everything lives, read once per session
  node_view()   one node, its parents and children, by id
  find_path()   shortest causal chain between two nodes
  delta()       only what changed since this agent last read

Every function reports `tokens` (what it returned) and `tokens_baseline` (what a typical top-8 retriever returns for the
same question: full JSON of the best-matching nodes, healthy or not). Token counts are estimates, see estimate_tokens.
"""
import json
import re
from collections import deque
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from app.models.common import NodeKind, Status
from app.models.graph import GraphNode

_TOKEN_RE = re.compile(r"[A-Za-z0-9À-ɏ]+|\s+|[^\sA-Za-z0-9À-ɏ]")
_WORD_RE = re.compile(r"^[A-Za-z0-9À-ɏ]+$")
_KIND_ORDER = {"source": 0, "fact": 1, "conclusion": 2, "action": 3}
_KIND_CODE = {"source": "S", "fact": "F", "conclusion": "C", "action": "A"}


def estimate_tokens(text: str) -> int:
    """
    A heuristic, not a tokenizer: a word run costs one token per 4.5 characters, every symbol about one token,
    a newline half a token. Within roughly 15% of BPE counts on English and JSON, which is enough to compare formats.
    The TypeScript twin in frontend/src/context/tokens.ts gives identical numbers (see test_context_pack.py).
    """
    if not text:
        return 0
    total = 0.0
    for m in _TOKEN_RE.finditer(text):
        s = m.group(0)
        if s.isspace():
            if "\n" in s:
                total += 0.5
            continue
        if _WORD_RE.match(s):
            total += max(1, -(-len(s) * 2 // 9))  # ceil(len / 4.5)
        else:
            total += 0.9
    return int(total + 0.5) if total >= 0 else 0


@dataclass
class Viewer:
    """Who is asking. None means an unscoped system caller that sees everything (demo and service tokens)."""

    agent_id: str = "agent"
    team_id: Optional[str] = None
    linked_teams: Set[str] = field(default_factory=set)
    scoped: bool = False

    def can_see(self, n: GraphNode) -> bool:
        if not self.scoped:
            return True
        required = set(n.scope_teams) | ({n.scope_team} if n.scope_team else set())
        return required <= ({self.team_id} | self.linked_teams)


def _clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _words(s: str) -> List[str]:
    return re.findall(r"[a-z0-9]{2,}", (s or "").lower())


_KIND_VALUE = {k: k.value for k in NodeKind}  # Enum.value is slow when it runs tens of thousands of times per ranking pass


def _kind(n: GraphNode) -> str:
    return _KIND_VALUE.get(n.kind) or str(getattr(n.kind, "value", n.kind))


def short_ids(nodes: Iterable[GraphNode]) -> Dict[str, str]:
    """F1, C3, ... Stable: numbered per kind in the order the nodes were created, not in storage order."""
    ordered = sorted(nodes, key=lambda n: (_KIND_ORDER.get(_kind(n), 9), n.valid_from or 0, n.id))
    counters: Dict[str, int] = {}
    out: Dict[str, str] = {}
    for n in ordered:
        c = _KIND_CODE.get(_kind(n), "N")
        counters[c] = counters.get(c, 0) + 1
        out[n.id] = f"{c}{counters[c]}"
    return out


def resolve_ref(nodes: Dict[str, GraphNode], ref: str) -> Optional[GraphNode]:
    if ref in nodes:
        return nodes[ref]
    ids = short_ids(nodes.values())
    for nid, sid in ids.items():
        if sid.lower() == ref.strip().lower():
            return nodes[nid]
    return None


def _in_scope(nodes: Iterable[GraphNode], viewer: Viewer) -> List[GraphNode]:
    return [n for n in nodes if viewer.can_see(n)]


def _healthy(nodes: Iterable[GraphNode]) -> List[GraphNode]:
    return [n for n in nodes if n.status == Status.HEALTHY and not n.stale]


def _adjacency(nodes: Dict[str, GraphNode], edges) -> Tuple[Dict[str, List[str]], Dict[str, List[str]]]:
    inn: Dict[str, List[str]] = {i: [] for i in nodes}
    out: Dict[str, List[str]] = {i: [] for i in nodes}
    for e in edges:
        if e.source in nodes and e.target in nodes and e.type.value in ("LEADS_TO", "SUPPORTS", "FROM_SOURCE"):
            out[e.source].append(e.target)
            inn[e.target].append(e.source)
    return inn, out


def _ancestors(inn: Dict[str, List[str]], start: str, limit: int) -> List[str]:
    seen = {start}
    order: List[str] = []
    q = deque([start])
    while q and len(order) < limit:
        cur = q.popleft()
        for p in inn.get(cur, []):
            if p not in seen:
                seen.add(p)
                order.append(p)
                q.append(p)
    return order


_STOP = set("is on do to of in at by an it be or as if so up we me my am no get got make made take like just very much many more most some such only own same too use used using one two long often can the and for are was were with that this from what which who how when where why does did can its has have not you your our their about into than then them they will would should could been being also any all per via out off over under".split())


@lru_cache(maxsize=100_000)
def _words_of(label: str, text: str, key: str, value: str) -> frozenset:
    return frozenset(_words(f"{label} {text} {key} {value}"))


def _node_words(n: GraphNode) -> frozenset:
    """The node's word set. Cached by the text it is made from, so a ranking pass over thousands of nodes does not re-tokenize them."""
    return _words_of(n.label, n.text or "", n.key or "", n.value or "")


def idf_weights(nodes: Iterable[GraphNode]) -> Dict[str, float]:
    """Rare words count more than common ones, so "region R2" beats "refund window" when a topic has dozens of facts."""
    import math

    docs = [_node_words(n) for n in nodes]
    df: Dict[str, int] = {}
    for d in docs:
        for w in d:
            df[w] = df.get(w, 0) + 1
    n_docs = max(1, len(docs))
    return {w: math.log(1 + n_docs / c) for w, c in df.items()}


def _relevance(n: GraphNode, qwords: Set[str], idf: Optional[Dict[str, float]] = None) -> float:
    """Coverage of the question's meaningful words, weighted by rarity. Plain overlap count when no weights are given."""
    if not qwords:
        return 0.0
    hay = _node_words(n)
    useful = [w for w in qwords if w in hay and w not in _STOP]
    if not useful:
        return 0.0
    score = sum((idf or {}).get(w, 1.0) if idf else 1.0 for w in useful)
    if _kind(n) in ("fact", "conclusion"):
        score += 0.2
    return float(score)




def rank(candidates: List[GraphNode], query: str, idf: Dict[str, float]) -> List[Tuple[GraphNode, float, float]]:
    """
    Hybrid ranking, best first: (node, score, cosine). Keyword evidence (IDF-weighted coverage of the question's words)
    still leads; the vector similarity breaks ties and rescues paraphrases that share no keyword with the question.
    A node with no keyword hit needs a cosine of at least RANK_VECTOR_MIN (0.30 by default). Score 0 means no match.
    """
    from app.services import embeddings

    qw = set(_words(query))
    lex = {n.id: _relevance(n, qw, idf) for n in candidates}
    # A question word the memory has never seen counts against coverage as much as the rarest word it has.
    unseen = max(idf.values()) if idf else 1.0
    lmax = sum(idf.get(w, unseen) for w in qw if w not in _STOP) + 0.2
    from app.config import settings

    cos = embeddings.cosine_scores(query, candidates) if settings.RANKING != "keyword" else {}
    out: List[Tuple[GraphNode, float, float]] = []
    for n in candidates:
        l, c = lex[n.id], cos.get(n.id, 0.0)
        if l <= 0 and c < settings.RANK_VECTOR_MIN:
            out.append((n, 0.0, c))
            continue
        out.append((n, settings.RANK_KEYWORD_WEIGHT * min(1.0, l / lmax) + settings.RANK_VECTOR_WEIGHT * c + (0.02 if _kind(n) in ("fact", "conclusion") else 0.0), c))
    return sorted(out, key=lambda x: -x[1])


def _raw(n: GraphNode) -> Dict[str, Any]:
    return n.model_dump(mode="json")


def baseline_tokens(nodes: List[GraphNode], query: str, k: int = 8) -> int:
    """A typical top-k retriever: best keyword matches, any status, full JSON chunks."""
    qw = set(_words(query))
    scored = [(n, _relevance(n, qw)) for n in nodes]
    top = [n for n, s in sorted(scored, key=lambda x: -x[1]) if s > 0][:k]
    return estimate_tokens(json.dumps([_raw(n) for n in top], indent=2))


def _line(n: GraphNode, ids: Dict[str, str], parents: List[str]) -> str:
    body = f"|{_clip(n.text, 110)}" if n.text and n.text != n.label else ""
    par = f"|<{','.join(ids.get(p, p) for p in parents)}" if parents else ""
    return f"{ids.get(n.id, n.id)}|{_clip(n.label, 60)}{body}{par}"


def pack(
    store,
    query: str,
    budget: int = 400,
    depth: int = 2,
    viewer: Optional[Viewer] = None,
    have_ids: Optional[Set[str]] = None,
    seeds: int = 8,
) -> Dict[str, Any]:
    viewer = viewer or Viewer()
    with store._lock:
        nodes_all = dict(store.nodes)
        edges = list(store.edges)
    scoped_nodes = _in_scope(nodes_all.values(), viewer)
    scoped = {n.id: n for n in scoped_nodes}
    live = {n.id: n for n in _healthy(scoped_nodes)}
    inn, _ = _adjacency(scoped, edges)
    ids = short_ids(scoped.values())

    idf = idf_weights(live.values())
    ranked = [(n, sc) for n, sc, _ in rank(list(live.values()), query, idf)]
    picked: List[str] = []
    seen: Set[str] = set()

    def add(i: str) -> None:
        if i in live and i not in seen:
            seen.add(i)
            picked.append(i)

    for n, s in ranked[:seeds]:
        if s <= 0:
            break
        add(n.id)
        for a in _ancestors(inn, n.id, depth * 3):
            add(a)

    base = baseline_tokens(scoped_nodes, query)
    chosen = picked
    delta_info = None
    if have_ids is not None:
        added = [i for i in picked if i not in have_ids]
        removed = [i for i in have_ids if i not in seen]
        delta_info = {"added": len(added), "removed": len(removed)}
        chosen = added

    lines: List[Tuple[str, str, int]] = []
    bodies: Set[str] = set()
    dropped = 0
    for i in chosen:
        n = live[i]
        text = _line(n, ids, inn.get(i, []))
        body = text.split("|", 1)[1] if "|" in text else text
        if body in bodies:
            dropped += 1
            continue
        bodies.add(body)
        lines.append((i, text, estimate_tokens(text) + 1))
    used = 24
    kept: List[Tuple[str, str, int]] = []
    for item in lines:
        if used + item[2] > budget:
            dropped += 1
            continue
        used += item[2]
        kept.append(item)

    head = f"# delta · {delta_info['added']} new, {delta_info['removed']} gone" if delta_info else f'# context · "{_clip(query, 40)}"'
    text = "\n".join([head] + [t for _, t, _ in kept])
    return {
        "text": text,
        "ids": [i for i, _, _ in kept],
        "short_ids": [ids[i] for i, _, _ in kept],
        "tokens": estimate_tokens(text),
        "tokens_baseline": base,
        "dropped": dropped,
        "truncated": dropped > 0,
        "delta": delta_info,
        "matched": len([1 for _, s in ranked if s > 0]),
    }


def build_map(store, viewer: Optional[Viewer] = None, epoch: Optional[int] = None) -> Dict[str, Any]:
    viewer = viewer or Viewer()
    with store._lock:
        nodes_all = dict(store.nodes)
        edges = list(store.edges)
        ep = store.epoch if epoch is None else epoch
    scoped = {n.id: n for n in _in_scope(nodes_all.values(), viewer)}
    inn, out = _adjacency(scoped, edges)
    ids = short_ids(scoped.values())
    healthy = len(_healthy(scoped.values()))
    bad = sum(1 for n in scoped.values() if n.status == Status.QUARANTINED)

    # connected components over the causal links
    comp_of: Dict[str, int] = {}
    comps: List[List[str]] = []
    for start in scoped:
        if start in comp_of:
            continue
        idx = len(comps)
        members: List[str] = []
        q = deque([start])
        comp_of[start] = idx
        while q:
            cur = q.popleft()
            members.append(cur)
            for nb in inn.get(cur, []) + out.get(cur, []):
                if nb not in comp_of:
                    comp_of[nb] = idx
                    q.append(nb)
        comps.append(members)
    comps.sort(key=len, reverse=True)

    def downstream(i: str) -> int:
        seen = {i}
        q = deque([i])
        while q:
            for nb in out.get(q.popleft(), []):
                if nb not in seen:
                    seen.add(nb)
                    q.append(nb)
        return len(seen) - 1

    lines = [
        f"# quorum map · {store.tenant} · epoch {ep}",
        f"{len(scoped)} nodes ({healthy} healthy, {bad} quarantined) · {len(edges)} links",
        "",
        "## areas",
    ]
    linked = [c for c in comps if len(c) > 1]
    singles = [c[0] for c in comps if len(c) == 1]
    for members in linked[:8]:
        counts = {"S": 0, "F": 0, "C": 0, "A": 0}
        for i in members:
            counts[_KIND_CODE.get(_kind(scoped[i]), "F")] = counts.get(_KIND_CODE.get(_kind(scoped[i]), "F"), 0) + 1
        roots = [scoped[i].label for i in members if not inn.get(i)][:2]
        hub = max(members, key=downstream) if members else None
        badn = sum(1 for i in members if scoped[i].status == Status.QUARANTINED)
        lines.append(
            f"- {_clip(' + '.join(roots) or 'unrooted', 48)}: S{counts['S']} F{counts['F']} C{counts['C']} A{counts['A']}"
            + (f" hub {ids[hub]}" if hub and downstream(hub) > 0 else "")
            + (f" ⚠{badn} bad" if badn else "")
        )
    if len(linked) > 8:
        lines.append(f"- … and {len(linked) - 8} smaller linked areas")
    if singles:
        counts = {"S": 0, "F": 0, "C": 0, "A": 0}
        topics: Dict[str, int] = {}
        for i in singles:
            n = scoped[i]
            counts[_KIND_CODE.get(_kind(n), "F")] = counts.get(_KIND_CODE.get(_kind(n), "F"), 0) + 1
            if n.key:
                t = re.sub(r"[_\d]+$", "", n.key)
                topics[t] = topics.get(t, 0) + 1
        top = ", ".join(f"{k} ×{v}" for k, v in sorted(topics.items(), key=lambda x: -x[1])[:6])
        lines.append(f"- unlinked: S{counts['S']} F{counts['F']} C{counts['C']} A{counts['A']}" + (f" · topics: {top}" if top else ""))
    hubs = sorted(scoped, key=downstream, reverse=True)[:5]
    lines += ["", "## hubs (most downstream)"]
    for i in hubs:
        d = downstream(i)
        if d:
            lines.append(f"- {ids[i]} {_clip(scoped[i].label, 44)} → {d}")
    lines += [
        "",
        "## how to ask",
        '- retrieve_context(query, k, format="compact", budget_tokens) → healthy facts + sources',
        "- get_node(id) → one node + parents (F3, C2 or a full id)",
        "- find_path(from, to) → shortest chain",
        "- delta() → what changed since your last read",
    ]
    text = "\n".join(lines)
    prose = estimate_tokens(
        "\n".join(f"{_kind(n).title()} \"{n.label}\" ({n.id}) is {n.status.value.lower()}" + (f": {n.text}" if n.text else "") for n in scoped.values())
    )
    return {"text": text, "epoch": ep, "tokens": estimate_tokens(text), "tokens_baseline": prose, "nodes": len(scoped)}


def node_view(store, ref: str, viewer: Optional[Viewer] = None) -> Dict[str, Any]:
    viewer = viewer or Viewer()
    with store._lock:
        nodes_all = dict(store.nodes)
        edges = list(store.edges)
    scoped = {n.id: n for n in _in_scope(nodes_all.values(), viewer)}
    n = resolve_ref(scoped, ref)
    if not n:
        return {"error": f"Node '{ref}' not found (or not visible to you).", "success": False}
    ids = short_ids(scoped.values())
    inn, out = _adjacency(scoped, edges)
    parents = [scoped[p] for p in inn.get(n.id, [])]
    children = [scoped[c] for c in out.get(n.id, [])]
    lines = [
        _line(n, ids, inn.get(n.id, [])) + f"|{n.status.value}" + (f"|by {n.asserted_by.name}" if n.asserted_by else ""),
    ]
    lines += [f"↑ {_line(p, ids, [])}" for p in parents[:6]]
    lines += [f"↓ {ids.get(c.id, c.id)}|{_clip(c.label, 50)}" for c in children[:8]]
    text = "\n".join(lines)
    base = estimate_tokens(json.dumps([_raw(n)] + [_raw(p) for p in parents], indent=2))
    return {"text": text, "id": n.id, "short_id": ids[n.id], "status": n.status.value, "tokens": estimate_tokens(text), "tokens_baseline": base, "success": True}


def find_path(store, a: str, b: str, viewer: Optional[Viewer] = None) -> Dict[str, Any]:
    viewer = viewer or Viewer()
    with store._lock:
        nodes_all = dict(store.nodes)
        edges = list(store.edges)
    scoped = {n.id: n for n in _in_scope(nodes_all.values(), viewer)}
    na, nb = resolve_ref(scoped, a), resolve_ref(scoped, b)
    if not na or not nb:
        return {"error": "One of the nodes was not found (or is not visible to you).", "success": False}
    ids = short_ids(scoped.values())

    path: Optional[List[str]] = None

    # FalkorDB native shortestPath algorithm when connected
    if getattr(store, "persister", None) is not None:
        try:
            store.persister.sync()
            cypher = (
                "MATCH (a:QNode {id: $a}), (b:QNode {id: $b}) "
                "MATCH p = shortestPath((a)-[:LEADS_TO|SUPPORTS|FROM_SOURCE*]-(b)) "
                "RETURN [n IN nodes(p) | n.id] AS path"
            )
            res = store.persister._q("shortest_path", cypher, {"a": na.id, "b": nb.id})
            if res.result_set and res.result_set[0] and res.result_set[0][0]:
                raw_path = [str(x) for x in res.result_set[0][0]]
                if all(nid in scoped for nid in raw_path):
                    path = raw_path
        except Exception:
            path = None

    # In-memory BFS fallback
    if path is None:
        inn, out = _adjacency(scoped, edges)
        prev: Dict[str, Optional[str]] = {na.id: None}
        q = deque([na.id])
        while q:
            cur = q.popleft()
            if cur == nb.id:
                break
            for nxt in inn.get(cur, []) + out.get(cur, []):
                if nxt not in prev:
                    prev[nxt] = cur
                    q.append(nxt)
        if nb.id not in prev:
            path = []
        else:
            path = []
            step: Optional[str] = nb.id
            while step is not None:
                path.append(step)
                step = prev[step]
            path.reverse()

    if not path:
        text = f"no chain between {ids[na.id]} and {ids[nb.id]}"
        return {"text": text, "path": [], "hops": 0, "tokens": estimate_tokens(text), "tokens_baseline": estimate_tokens(json.dumps([_raw(na), _raw(nb)], indent=2)), "success": True}

    text = " → ".join(f"{ids[i]} {_clip(scoped[i].label, 36)}" for i in path)
    base = estimate_tokens(json.dumps([_raw(scoped[i]) for i in path], indent=2))
    return {"text": text, "path": path, "hops": len(path) - 1, "tokens": estimate_tokens(text), "tokens_baseline": base, "success": True}


def compare(store, query: str, budget: int = 400, viewer: Optional[Viewer] = None) -> Dict[str, Any]:
    """Everything the Context page shows, computed from the live graph with the same code the agents use."""
    viewer = viewer or Viewer()
    with store._lock:
        nodes_all = dict(store.nodes)
        edges = list(store.edges)
    scoped = _in_scope(nodes_all.values(), viewer)
    live = _healthy(scoped)
    by_id = {n.id: n for n in scoped}
    raw = estimate_tokens(json.dumps({"nodes": [_raw(n) for n in scoped], "edges": [e.model_dump(mode="json") for e in edges if e.source in by_id and e.target in by_id]}, indent=2))
    idx = {n.id: n for n in scoped}
    prose = estimate_tokens(
        "\n".join(
            f"{_kind(n).title()} \"{n.label}\" ({n.id}) is {n.status.value.lower()}" + (f": {n.text}" if n.text else "") + (f" Asserted by {n.asserted_by.name}." if n.asserted_by else "")
            for n in scoped
        )
        + "\n"
        + "\n".join(f"{idx[e.source].label} {e.type.value.lower().replace('_', ' ')} {idx[e.target].label}." for e in edges if e.source in idx and e.target in idx)
    )
    p = pack(store, query, budget=budget, viewer=viewer)
    m = build_map(store, viewer)
    live_ids = {n.id for n in live}
    live_raw = estimate_tokens(json.dumps({"nodes": [_raw(n) for n in live]}, indent=2))
    relevant_raw = estimate_tokens(json.dumps([_raw(by_id[i]) for i in p["ids"] if i in by_id], indent=2))
    return {
        "nodes": len(scoped),
        "healthy": len(live_ids),
        "query": query,
        "budget": budget,
        "raw_json_tokens": raw,
        "prose_tokens": prose,
        "top8_tokens": p["tokens_baseline"],
        "packed_tokens": p["tokens"],
        "map_tokens": m["tokens"],
        "packed": p["text"],
        "map": m["text"],
        "included": len(p["ids"]),
        "dropped": p["dropped"],
        "stages": [
            {"label": "Raw graph JSON", "tokens": raw, "note": "every field of every node and link"},
            {"label": "Healthy only", "tokens": live_raw, "note": f"{len(scoped) - len(live_ids)} quarantined, superseded or stale nodes never enter"},
            {"label": "Relevant slice", "tokens": relevant_raw, "note": f"{len(p['ids'])} nodes matched the query or prove where a match came from"},
            {"label": "Compact lines + budget", "tokens": p["tokens"], "note": f"one line per node, short ids, within {budget} tokens, {p['dropped']} dropped"},
        ],
    }
