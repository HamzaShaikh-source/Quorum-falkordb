"""Retrieval says "I do not know" about things memory has never seen; read-only POSTs skip the durable sync; the health check does not stampede."""
import threading
import uuid

import pytest

from app import db
from app.config import settings
from app.graph import queries
from app.main import _is_read_only_post
from app.services import mcp_tools as t


@pytest.fixture()
def tenant():
    name = f"gl{uuid.uuid4().hex[:6]}"
    queries.forget_stores()
    yield name
    queries.forget_stores()


def seed(tenant, n=60):
    tok = f"token_{tenant}_agent"
    store = queries.get_graph_store(tenant)
    from app.models.common import NodeKind, Status
    from app.models.graph import GraphNode

    for i in range(n):
        store.add_node(GraphNode(id=f"p{i}", kind=NodeKind.FACT, label=f"pkg{i}", status=Status.HEALTHY, stale=False, key=f"pkg{i}.version", value=f"1.{i}",
                                 text=f"package alpha{i}lib version 1.{i} installed"))
    return tok


def test_a_question_about_something_memory_has_never_seen_is_not_answered_with_generic_matches(tenant):
    tok = seed(tenant)
    known = t.retrieve_context("Which version of alpha7lib is installed?", tok, format="full")
    assert known["outcome"] == "ANSWERED" and any("alpha7lib" in (c["text"] or "") for c in known["context"])
    unknown = t.retrieve_context("Which version of zorblax-wibbleton77 is installed?", tok, format="full")
    assert unknown["outcome"] == "NOT_IN_MEMORY" and unknown["count"] == 0
    compact = t.retrieve_context("Which version of zorblax-wibbleton77 is installed?", tok, format="compact")
    assert compact["outcome"] == "NOT_IN_MEMORY"


def test_a_small_memory_is_left_alone(tenant):
    """Below 30 facts every word is informative; the gate would only cause false refusals there."""
    tok = seed(tenant, n=10)
    out = t.retrieve_context("Which version of mystery is installed?", tok, format="full")
    assert out["count"] >= 1


def test_the_gate_can_be_relaxed_by_the_vector_channel(tenant, monkeypatch):
    tok = seed(tenant)
    monkeypatch.setattr(settings, "RANK_UNGROUNDED_MIN_COS", 0.0)
    assert t.retrieve_context("Which version of zorblax-wibbleton77 is installed?", tok, format="full")["count"] > 0


def test_a_paraphrase_that_names_a_known_entity_still_answers(tenant):
    tok = seed(tenant)
    out = t.retrieve_context("what number does alpha12lib report these days, whatever flavour", tok, format="full")
    assert any("alpha12lib" in (c["text"] or "") for c in out["context"])


def test_read_only_posts_skip_the_durable_sync_and_writes_do_not():
    for p in ("/api/context/retrieve", "/api/work/announce", "/api/brain/ask", "/api/blast/preview", "/api/events"):
        assert _is_read_only_post(p), p
    for p in ("/api/observe", "/api/workspace/members", "/api/actions/stage", "/api/scenario/reset", "/api/connect/request", "/api/handoffs/h1/claim"):
        assert not _is_read_only_post(p), p


def test_health_checks_are_single_flight_under_concurrency(monkeypatch):
    """While one caller refreshes, concurrent callers reuse the last answer instead of each opening a connection."""
    import redis

    db._health.update({"checked_at": 1.0, "up": True, "error": None, "latency_ms": 1.0, "version": "x"})  # stale, but known
    db._health_refreshing = False
    opened = {"n": 0}
    gate = threading.Event()

    class FakeConn:
        def __init__(self, *a, **k):
            opened["n"] += 1

        def ping(self):
            gate.wait(3)

        def execute_command(self, *a, **k):
            return [(0, "graph")]

        def info(self, *a):
            return {"redis_version": "8"}

        def close(self):
            pass

    monkeypatch.setattr(redis, "Redis", FakeConn)
    results = []
    first = threading.Thread(target=lambda: results.append(db.check_health()))
    first.start()
    for _ in range(100):
        if opened["n"]:
            break
        gate.wait(0.01)
    others = [threading.Thread(target=lambda: results.append(db.check_health())) for _ in range(20)]
    for th in others:
        th.start()
    for th in others:
        th.join(5)
    gate.set()
    first.join(5)
    assert opened["n"] == 1, f"{opened['n']} connections opened for 21 concurrent health checks"
    assert len(results) == 21
    db._health_refreshing = False
    db._health["checked_at"] = 0.0
