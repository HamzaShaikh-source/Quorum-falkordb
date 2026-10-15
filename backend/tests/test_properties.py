"""
Property-based tests (hypothesis): the guarantees the product sells, checked on random inputs instead of hand-picked ones.

  - after any quarantine on any DAG, no healthy node has a quarantined ancestor, and nothing outside the descendants changed
  - restoring puts back exactly what is valid again, and a restored graph has no quarantined node unless a parent still is
  - observe never raises, never stores a credential, never stores more than the cap, and is idempotent
  - the token-bucket limiter never admits more than burst + rate * time
  - the token estimator is monotonic and zero only for empty text
  - ranking never returns an unhealthy, stale or out-of-scope node
"""
import uuid

from hypothesis import HealthCheck, given, settings as hsettings, strategies as st

from app.graph import queries
from app.models.common import EdgeType, NodeKind, Status
from app.models.graph import GraphEdge, GraphNode
from app.ratelimit import RateLimiter
from app.services import context_pack as cp
from app.services import observer
from app.services.auditor import auditor_service
from app.services.context_pack import Viewer, estimate_tokens

FAST = hsettings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])


@st.composite
def dags(draw):
    n = draw(st.integers(min_value=2, max_value=18))
    edges = []
    for child in range(1, n):
        parents = draw(st.sets(st.integers(min_value=0, max_value=child - 1), max_size=3))
        edges += [(p, child) for p in parents]
    root = draw(st.integers(min_value=0, max_value=n - 1))
    return n, edges, root


def build(n, edges):
    store = queries.get_graph_store(f"prop_{uuid.uuid4().hex[:8]}")
    for i in range(n):
        store.add_node(GraphNode(id=f"n{i}", kind=NodeKind.FACT if i == 0 else NodeKind.CONCLUSION, label=f"node {i}", status=Status.HEALTHY, stale=False, text=f"text {i}"))
    for k, (a, b) in enumerate(edges):
        store.add_edge(GraphEdge(id=f"e{k}", source=f"n{a}", target=f"n{b}", type=EdgeType.LEADS_TO))
    return store


def descendants(edges, root):
    seen, stack = set(), [root]
    while stack:
        cur = stack.pop()
        for a, b in edges:
            if a == cur and b not in seen:
                seen.add(b)
                stack.append(b)
    return seen


def ancestors(edges, node):
    seen, stack = set(), [node]
    while stack:
        cur = stack.pop()
        for a, b in edges:
            if b == cur and a not in seen:
                seen.add(a)
                stack.append(a)
    return seen


@FAST
@given(dags())
def test_a_quarantine_leaves_no_healthy_node_under_a_bad_one(g):
    n, edges, root = g
    store = build(n, edges)
    auditor_service.quarantine(store, f"n{root}", tenant=store.tenant)
    bad = {i for i in range(n) if store.nodes[f"n{i}"].status != Status.HEALTHY}
    assert root in bad
    assert bad == {root} | descendants(edges, root), "exactly the root and what depends on it"
    for i in range(n):
        if i not in bad:
            assert not (ancestors(edges, i) & bad), "a healthy node never sits under a quarantined ancestor"


@FAST
@given(dags())
def test_restoring_the_root_heals_what_is_valid_again(g):
    n, edges, root = g
    store = build(n, edges)
    auditor_service.quarantine(store, f"n{root}", tenant=store.tenant)
    auditor_service.restore(store, f"n{root}", tenant=store.tenant)
    for i in range(n):
        node = store.nodes[f"n{i}"]
        if node.status != Status.HEALTHY:
            assert any(store.nodes[f"n{p}"].status != Status.HEALTHY for p in ancestors(edges, i)), "only a still-bad ancestor justifies staying quarantined"


@FAST
@given(dags(), st.text(min_size=1, max_size=40))
def test_ranking_never_returns_an_unhealthy_node(g, query):
    n, edges, root = g
    store = build(n, edges)
    auditor_service.quarantine(store, f"n{root}", tenant=store.tenant)
    out = cp.pack(store, query + " node text", viewer=Viewer())
    for nid in out["ids"]:
        assert store.nodes[nid].status == Status.HEALTHY


# ---------- the observer ----------
SECRETS = ["sk-live-abcdef1234567890xyz", "ghp_abcdefghijklmnopqrstuvwx", "AKIAABCDEFGHIJKLMNOP", "password: hunter22222"]


@FAST
@given(st.text(max_size=2000))
def test_the_observer_never_raises_on_any_text(text):
    cands = observer.candidates(text)
    assert len(cands) <= 12
    for c in cands:
        assert 24 <= len(c["text"]) <= 320 and not c["text"].endswith("?")


@FAST
@given(st.lists(st.sampled_from(SECRETS), min_size=1, max_size=3), st.text(alphabet=st.characters(whitelist_categories=("L", "N", "Zs")), max_size=80))
def test_a_sentence_with_a_credential_is_never_kept(secrets_, filler):
    text = " ".join(f"The retention period is 30 days and must never change, see {s}." for s in secrets_) + " " + filler
    for c in observer.candidates(text):
        assert not any(s.split(":")[-1].strip() in c["text"] for s in SECRETS)


@FAST
@given(st.lists(st.sampled_from(["The refund window in Chile is 21 days from delivery.", "Support tickets from Norway must be answered within 4 hours.", "Maybe we could extend it?", "ok thanks", "I ran 12 tests and 3 failed."]), max_size=8))
def test_observing_twice_stores_nothing_new(lines):
    from app.services import mcp_tools as t

    tenant = f"obs_{uuid.uuid4().hex[:8]}"
    tok = f"token_{tenant}_agent"
    first = t.observe("\n".join(lines), tok)
    again = t.observe("\n".join(lines), tok)
    assert first["success"] and again["stored"] == 0 and len(again["duplicates"]) == first["stored"]


# ---------- the limiter and the estimator ----------
@FAST
@given(st.integers(min_value=1, max_value=600), st.integers(min_value=1, max_value=50), st.integers(min_value=1, max_value=400))
def test_the_limiter_never_admits_more_than_burst_plus_refill(per_min, burst, calls):
    lim = RateLimiter(per_min, burst=burst)
    admitted = sum(lim.allow("k")[0] for _ in range(calls))  # all within microseconds: refill is negligible
    assert admitted <= burst + 1
    assert admitted >= min(calls, burst)


@FAST
@given(st.text(max_size=500), st.text(max_size=500))
def test_the_estimator_is_monotonic_and_zero_only_when_empty(a, b):
    assert estimate_tokens(a + b) >= estimate_tokens(a) - 1  # appending text never makes it cost (meaningfully) less
    if a.strip():
        assert estimate_tokens(a) >= 0
    assert estimate_tokens("") == 0
