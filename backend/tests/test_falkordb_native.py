"""The decisive graph work (quarantine cascade, execute-guard ancestry, blast radius) runs as Cypher inside FalkorDB, gives the
same answer as the in-process walk, and says which engine answered."""
import random
import uuid

import pytest
from fastapi.testclient import TestClient

from app import db
from app.config import settings
from app.graph import queries
from app.main import app
from app.models.common import EdgeType, NodeKind, Status
from app.models.graph import GraphEdge, GraphNode

CAUSAL = ("LEADS_TO", "SUPPORTS", "FROM_SOURCE")
client = TestClient(app)


@pytest.fixture()
def falkor(monkeypatch):
    monkeypatch.setattr(settings, "PERSISTENCE", "falkordb")
    db.reset_client()
    db._health["checked_at"] = 0.0
    if not db.check_health(force=True)["up"]:
        pytest.skip("FalkorDB is not running. Start it with: docker compose up -d falkordb")
    tenant = f"fnative_{uuid.uuid4().hex[:8]}"
    queries.forget_stores()
    yield tenant
    try:
        db.graph_for(tenant).delete()
    except Exception:
        pass
    queries.forget_stores()


def _node(i: str) -> GraphNode:
    return GraphNode(id=i, kind=NodeKind.FACT, label=i, status=Status.HEALTHY, stale=False, text=i)


def _random_dag(store, n: int, seed: int) -> None:
    rng = random.Random(seed)
    for i in range(n):
        store.add_node(_node(f"n{i}"))
    k = 0
    for j in range(1, n):
        for i in rng.sample(range(j), min(j, rng.choice((1, 1, 2, 3)))):  # diamonds and shared parents
            store.add_edge(GraphEdge(id=f"e{k}", source=f"n{i}", target=f"n{j}", type=rng.choice((EdgeType.LEADS_TO, EdgeType.SUPPORTS))))
            k += 1
    store.commit()


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_the_cascade_runs_in_falkordb_and_matches_the_in_process_walk(falkor, seed):
    store = queries.get_graph_store(falkor)
    _random_dag(store, 40, seed)
    expected = store._process_closure("n0", CAUSAL)
    affected = store.cascade_quarantine("n0")
    assert store.last_cascade_engine == "falkordb"
    got = {a["id"]: a["depth"] for a in affected if a["id"] != "n0"}
    assert got == expected, "FalkorDB's closure and hop depths must equal the in-process oracle"
    assert store.nodes["n0"].status == Status.QUARANTINED
    assert all(store.nodes[i].status == Status.QUARANTINED for i in got)
    names = {e.name for e in store.query_log}
    assert {"traverse_hop", "cascade_flip"} <= names, "the Cypher statements really ran and were logged"
    # the database itself holds the quarantine, not only the API process
    rows = store.persister._q("check", "MATCH (n:QNode) WHERE n.status = 'QUARANTINED' RETURN count(n)", {}).result_set
    assert rows[0][0] == len(affected)


def test_the_guard_and_blast_radius_use_falkordb_ancestry(falkor):
    store = queries.get_graph_store(falkor)
    _random_dag(store, 25, 9)
    before = dict(queries.GRAPH_STATS)
    desc = store.get_descendants("n0", edge_types=["LEADS_TO", "SUPPORTS"])
    anc = store.get_ancestors("n24", edge_types=["LEADS_TO", "SUPPORTS"])
    assert queries.GRAPH_STATS["falkordb"] >= before["falkordb"] + 2
    assert {n.id for n in desc} == set(store._process_closure("n0", ("LEADS_TO", "SUPPORTS")))
    assert all(isinstance(n, GraphNode) for n in anc)


def test_falls_back_to_the_process_walk_when_the_database_errors(falkor, monkeypatch):
    store = queries.get_graph_store(falkor)
    _random_dag(store, 20, 4)
    expected = store._process_closure("n0", CAUSAL)

    def boom(*a, **k):
        raise RuntimeError("database went away")

    monkeypatch.setattr(store.persister, "_q", boom)
    monkeypatch.setattr(store.persister, "sync", lambda *a, **k: 0)
    affected = store.cascade_quarantine("n0")
    assert store.last_cascade_engine == "process"
    assert {a["id"]: a["depth"] for a in affected if a["id"] != "n0"} == expected


def test_the_proof_endpoint_compares_both_engines(falkor):
    store = queries.get_graph_store(falkor)
    _random_dag(store, 30, 7)
    h = {"Authorization": f"Bearer token_{falkor}_human"}
    r = client.get("/api/falkordb/proof", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["comparison"]["identical"] is True
    assert body["comparison"]["falkordb"]["descendants"] == body["comparison"]["process"]["descendants"] > 0
    assert body["database"]["nodes"] == 30
    assert any("Quarantine cascade" == s["decision"] for s in body["statements"])
    assert client.get("/api/falkordb/proof", headers={"Authorization": f"Bearer token_{falkor}_agent"}).status_code == 403


def test_a_long_chain_is_quarantined_to_the_end_with_no_depth_limit(falkor):
    """The cascade must reach every descendant however deep: a 200-hop chain, then a cap that forces the unbounded fallback."""
    store = queries.get_graph_store(falkor)
    n = 200
    for i in range(n):
        store.add_node(_node(f"c{i}"))
    for i in range(n - 1):
        store.add_edge(GraphEdge(id=f"ce{i}", source=f"c{i}", target=f"c{i + 1}", type=EdgeType.LEADS_TO))
    store.commit()
    closure = store.closure_in_falkordb("c0", CAUSAL, "out")
    assert closure is not None and len(closure) == n - 1 and closure[f"c{n - 1}"] == n - 1
    affected = store.cascade_quarantine("c0")
    assert store.last_cascade_engine == "falkordb" and len(affected) == n
    assert all(store.nodes[f"c{i}"].status == Status.QUARANTINED for i in range(n))
    # a cap lower than the chain is never reported as a complete answer
    assert store.closure_in_falkordb("c0", CAUSAL, "out", max_depth=50) is None
