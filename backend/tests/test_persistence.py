"""FalkorDB is the system of record: what is written survives a restart, per tenant, and is really in the graph."""
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from app import db
from app.config import settings
from app.graph import queries
from app.main import app
from app.models.common import EdgeType, HandoffKind, HandoffStatus, NodeKind, Status
from app.models.graph import GraphEdge, GraphNode
from app.models.handoffs import Handoff


@pytest.fixture()
def falkor(monkeypatch):
    monkeypatch.setattr(settings, "PERSISTENCE", "falkordb")
    db.reset_client()
    db._health["checked_at"] = 0.0
    if not db.check_health(force=True)["up"]:
        pytest.skip("FalkorDB is not running. Start it with: docker compose up -d falkordb")
    tenant = f"ptest_{uuid.uuid4().hex[:8]}"
    queries.forget_stores()
    yield tenant
    for t in (tenant, tenant + "_b"):
        try:
            db.graph_for(t).delete()
        except Exception:
            pass
    queries.forget_stores()


def _fact(i: str, label: str) -> GraphNode:
    return GraphNode(id=i, kind=NodeKind.FACT, label=label, status=Status.HEALTHY, stale=False, text=f"text of {label}")


def test_atomic_quarantine_includes_recovery_intent_and_survives_restart(falkor):
    from app.models.common import CompensationStatus, GateStatus, Reversibility
    from app.models.graph import ActionInfo
    from app.services.auditor import auditor_service

    store = queries.get_graph_store(falkor)
    store.add_node(_fact("root", "original"))
    store.add_node(GraphNode(id="action", kind=NodeKind.ACTION, label="ticket", status=Status.HEALTHY,
                             action=ActionInfo(type="ticket", reversibility=Reversibility.REVERSIBLE,
                                               gate=GateStatus.PASSED, executed=True)))
    store.add_edge(GraphEdge(id="cause", source="root", target="action", type=EdgeType.LEADS_TO))
    store.commit()
    auditor_service.quarantine(store, "root", tenant=falkor)
    queries.forget_stores()
    restored = queries.get_graph_store(falkor)
    assert restored.nodes["root"].status == Status.QUARANTINED
    assert restored.nodes["action"].status == Status.QUARANTINED
    assert restored.nodes["action"].action.compensation.status == CompensationStatus.PENDING_COMPENSATION
    assert restored.epoch == 2


def test_compensation_external_success_then_confirmation_failure_recovers(falkor, monkeypatch):
    from app.models.common import CompensationStatus, GateStatus, Reversibility
    from app.models.graph import ActionInfo, CompensationInfo
    from app.services import saga
    from app.services.helpdesk import MockHelpdeskService

    store = queries.get_graph_store(falkor)
    node = GraphNode(id="a", kind=NodeKind.ACTION, label="ticket", status=Status.QUARANTINED,
                     action=ActionInfo(type="ticket", reversibility=Reversibility.REVERSIBLE,
                                       gate=GateStatus.PASSED, executed=True,
                                       compensation=CompensationInfo(status=CompensationStatus.PENDING_COMPENSATION)))
    store.add_node(node)
    store.commit()
    service = MockHelpdeskService()
    monkeypatch.setattr(saga, "helpdesk_service", service)
    real = store.persister._q

    def fail_confirmation(*args, **kwargs):
        if node.action.compensation.status == CompensationStatus.COMPENSATED:
            raise ConnectionError("confirmation persistence failed")
        return real(*args, **kwargs)

    monkeypatch.setattr(store.persister, "_q", fail_confirmation)
    with pytest.raises(ConnectionError):
        saga.OutboxSagaWorker().process_pending(store, falkor)
    queries.forget_stores()
    recovered = queries.get_graph_store(falkor)
    comp = recovered.nodes["a"].action.compensation
    assert comp.status == CompensationStatus.DISPATCHING and comp.attempts == 1
    assert recovered.nodes["a"].action.external_key in service.tombstones
    comp.lease_until = 1
    assert saga.OutboxSagaWorker().process_pending(recovered, falkor)
    queries.forget_stores()
    assert queries.get_graph_store(falkor).nodes["a"].action.compensation.status == CompensationStatus.COMPENSATED


def test_projection_failure_recovers_from_atomic_checkpoint(falkor, monkeypatch):
    store = queries.get_graph_store(falkor)
    store.add_node(_fact("checkpoint_fact", "durable recovery fact"))
    store.record_call({"tool": "checkpoint-test"})
    real = store.persister._q

    def fail_projection(name, *args, **kwargs):
        if name == "upsert_fact":
            raise ConnectionError("projection interrupted after checkpoint committed")
        return real(name, *args, **kwargs)

    monkeypatch.setattr(store.persister, "_q", fail_projection)
    with pytest.raises(ConnectionError):
        store.commit()
    queries.forget_stores()
    recovered = queries.get_graph_store(falkor)
    assert recovered.nodes["checkpoint_fact"].label == "durable recovery fact"
    assert recovered.calls[-1]["tool"] == "checkpoint-test"
    recovered.commit()
    rows = db.graph_for(falkor).query("MATCH (n:QNode {id:'checkpoint_fact'}) RETURN n.json").result_set
    assert GraphNode.model_validate_json(rows[0][0]).id == "checkpoint_fact"


def test_writes_survive_a_restart(falkor):
    s = queries.get_graph_store(falkor)
    s.add_node(_fact("f1", "Refund window is 30 days"))
    s.add_node(_fact("f2", "Standard: 30 days"))
    s.add_edge(GraphEdge(id="e1", source="f1", target="f2", type=EdgeType.LEADS_TO))
    s.add_handoff(Handoff(id="h1", kind=HandoffKind.TASK, task="t", status=HandoffStatus.PENDING))
    s.bump_epoch()
    assert s.commit() > 0

    queries.forget_stores()  # what a restart does to memory
    again = queries.get_graph_store(falkor)
    assert {n.id for n in again.nodes.values()} == {"f1", "f2"}
    assert [e.id for e in again.edges] == ["e1"]
    assert again.epoch == 2
    assert any(h.id == "h1" for h in again.handoffs)


def test_status_changes_and_cascade_are_persisted(falkor):
    s = queries.get_graph_store(falkor)
    for i in ("a", "b", "c"):
        s.add_node(_fact(i, i))
    s.add_edge(GraphEdge(id="e1", source="a", target="b", type=EdgeType.LEADS_TO))
    s.add_edge(GraphEdge(id="e2", source="b", target="c", type=EdgeType.LEADS_TO))
    s.commit()
    s.cascade_quarantine("a")
    s.commit()
    queries.forget_stores()
    again = queries.get_graph_store(falkor)
    assert [again.nodes[i].status for i in ("a", "b", "c")] == [Status.QUARANTINED] * 3
    assert again.check_invariants()["ok"]


def test_it_is_a_real_graph_in_falkordb(falkor):
    s = queries.get_graph_store(falkor)
    s.add_node(_fact("x", "X"))
    s.add_node(_fact("y", "Y"))
    s.add_edge(GraphEdge(id="e", source="x", target="y", type=EdgeType.LEADS_TO))
    s.commit()
    rows = db.graph_for(falkor).query("MATCH (a:Fact)-[:LEADS_TO]->(b:Fact) RETURN a.id, b.id").result_set
    assert rows == [["x", "y"]]


def test_removed_things_are_removed_from_the_database(falkor):
    s = queries.get_graph_store(falkor)
    s.add_node(_fact("gone", "Gone"))
    s.commit()
    s.nodes.clear()
    s.commit()
    assert db.graph_for(falkor).query("MATCH (n:QNode) RETURN count(n)").result_set[0][0] == 0


def test_tenants_are_separate_graphs_and_handoffs_do_not_leak(falkor):
    a = queries.get_graph_store(falkor)
    b = queries.get_graph_store(falkor + "_b")
    a.add_node(_fact("only_a", "A"))
    a.add_handoff(Handoff(id="ha", kind=HandoffKind.TASK, task="t", status=HandoffStatus.PENDING))
    a.commit()
    b.commit()
    assert "only_a" not in b.nodes
    assert not any(h.id == "ha" for h in b.handoffs)


def test_failed_write_is_retried_not_lost(falkor, monkeypatch):
    s = queries.get_graph_store(falkor)
    s.add_node(_fact("keep", "Keep"))
    real = s.persister._q

    def flaky(*a, **k):
        raise ConnectionError("database went away")

    monkeypatch.setattr(s.persister, "_q", flaky)
    with pytest.raises(ConnectionError):
        s.commit()
    monkeypatch.setattr(s.persister, "_q", real)
    assert s.commit() > 0
    queries.forget_stores()
    assert "keep" in queries.get_graph_store(falkor).nodes


def test_api_writes_are_durable_before_the_response(falkor, monkeypatch):
    monkeypatch.setattr(settings, "DEMO_TOKENS", {**settings.DEMO_TOKENS, f"tok_{falkor}": {"tenant": falkor, "role": "human", "agent_id": "h"}})
    h = {"Authorization": f"Bearer tok_{falkor}"}
    queries.forget_stores()
    with TestClient(app) as c:
        assert c.post("/api/scenario/reset", headers=h).status_code == 200
        time.sleep(0.6)
        assert c.post("/api/scenario/inject_poison", headers=h).status_code == 200
        time.sleep(1.5)
        g1 = c.get("/api/graph", headers=h).json()
    queries.forget_stores()
    with TestClient(app) as c:
        g2 = c.get("/api/graph", headers=h).json()
    assert {n["id"] for n in g1["nodes"]} == {n["id"] for n in g2["nodes"]}
    assert len(g2["nodes"]) >= 3
