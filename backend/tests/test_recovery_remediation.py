"""Failure injection for durable dispatch, leases, idempotency and candidate invalidation."""
import threading
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.graph.queries import GraphStore
from app.models.common import CompensationStatus, GateStatus, NodeKind, Reversibility, Status
from app.models.graph import ActionInfo, CompensationInfo, GraphNode
from app.services import saga, executor
from app.services.context_pack import Viewer
from app.services.helpdesk import HTTPHelpdeskService, MockHelpdeskService, external_key


def action(store):
    node = GraphNode(id="action", kind=NodeKind.ACTION, label="Test action", status=Status.QUARANTINED,
                     action=ActionInfo(type="ticket", reversibility=Reversibility.REVERSIBLE, gate=GateStatus.PASSED,
                                       executed=True, compensation=CompensationInfo(status=CompensationStatus.PENDING_COMPENSATION)))
    store.add_node(node)
    return node


def test_expired_lease_recovered_and_key_stable(monkeypatch):
    store = GraphStore("leases")
    node = action(store)
    node.action.compensation.status = CompensationStatus.DISPATCHING
    node.action.compensation.lease_until = 1
    node.action.compensation.lease_owner = "dead-process"
    mock = MockHelpdeskService()
    monkeypatch.setattr(saga, "helpdesk_service", mock)
    worker = saga.OutboxSagaWorker()
    assert worker.process_pending(store, "leases") == [node]
    assert node.action.compensation.status == CompensationStatus.COMPENSATED
    assert node.action.compensation.attempts == 1
    assert external_key("leases", node) in mock.tombstones


def test_active_claim_blocks_concurrent_worker(monkeypatch):
    store = GraphStore("claims")
    node = action(store)
    started, release = threading.Event(), threading.Event()

    class Blocking(MockHelpdeskService):
        def compensate_action(self, *args, **kwargs):
            started.set()
            assert release.wait(5)
            return super().compensate_action(*args, **kwargs)

    monkeypatch.setattr(saga, "helpdesk_service", Blocking())
    worker = saga.OutboxSagaWorker()
    thread = threading.Thread(target=worker.process_pending, args=(store, "claims"))
    thread.start()
    try:
        assert started.wait(5)
        assert saga.OutboxSagaWorker().process_pending(store, "claims") == []
        assert node.action.compensation.attempts == 1
    finally:
        release.set()
        thread.join(5)


def test_retry_schedule_and_exhaustion(monkeypatch):
    store = GraphStore("retry")
    node = action(store)

    class Failing:
        def compensate_action(self, **kwargs):
            return {"status": 503, "error": "temporarily unavailable"}

    monkeypatch.setattr(saga, "helpdesk_service", Failing())
    worker = saga.OutboxSagaWorker(max_attempts=2)
    worker.process_pending(store, "retry")
    comp = node.action.compensation
    assert comp.attempts == 1 and comp.next_attempt_at > 0
    worker.process_pending(store, "retry")
    assert comp.attempts == 1
    comp.next_attempt_at = 0
    worker.process_pending(store, "retry")
    assert comp.status == CompensationStatus.FAILED and comp.last_error
    assert len(store.handoffs) == 1


def test_persistence_failure_prevents_external_execution(monkeypatch):
    store = GraphStore("durable")
    node = action(store)
    node.status = Status.HEALTHY
    node.action.executed = False
    node.action.compensation = None
    mock = MockHelpdeskService()
    monkeypatch.setattr(executor, "helpdesk_service", mock)

    def unavailable(*args):
        raise ConnectionError("persistence unavailable")

    monkeypatch.setattr(store, "commit", unavailable)
    with pytest.raises(ConnectionError):
        executor.action_executor.execute(store, node.id, "durable")
    assert not mock.executed_actions


def test_execution_success_before_confirmation_crash_replays_once(monkeypatch):
    store = GraphStore("crash")
    node = action(store)
    node.status = Status.HEALTHY
    node.action.compensation = None
    node.action.execution_attempts = 1
    node.action.execution_lease_until = 1
    mock = MockHelpdeskService()
    key = external_key("crash", node)
    mock.execute_action(key, "ticket")  # provider succeeded; persisted local intent is still unconfirmed
    monkeypatch.setattr(saga, "helpdesk_service", mock)
    saga.OutboxSagaWorker().process_pending(store, "crash")
    assert node.action.confirmed and len(mock.executed_actions) == 1


def test_legacy_uncertain_execution_requests_review_without_new_side_effect(monkeypatch):
    store = GraphStore("legacy")
    node = action(store)
    node.status = Status.HEALTHY
    node.action.compensation = None
    node.action.external_key = "historical-key"
    node.action.execution_attempts = 0
    mock = MockHelpdeskService()
    monkeypatch.setattr(saga, "helpdesk_service", mock)
    worker = saga.OutboxSagaWorker()
    worker.process_pending(store, "legacy")
    worker.process_pending(store, "legacy")
    assert not mock.executed_actions
    assert node.action.delivery_error == "LEGACY_REQUIRES_REVIEW" and len(store.handoffs) == 1


def test_candidate_index_rechecks_mutation_scope_delete_and_restore():
    store = GraphStore("index")
    fact = GraphNode(id="fact", kind=NodeKind.FACT, label="policy", status=Status.HEALTHY)
    store.add_node(fact)
    viewer = Viewer(team_id="A", scoped=True)
    assert store.nodes.candidates(viewer) == [fact]
    fact.status = Status.QUARANTINED
    assert store.nodes.candidates(viewer) == []
    store.restore_node(fact.id)
    assert store.nodes.candidates(viewer) == [fact]
    fact.stale = True
    assert store.nodes.candidates(viewer) == []
    fact.stale = False
    fact.scope_teams.append("B")
    assert store.nodes.candidates(viewer) == []
    viewer.linked_teams.add("B")
    assert store.nodes.candidates(viewer) == [fact]
    del store.nodes[fact.id]
    fact.status = Status.HEALTHY
    assert store.nodes.candidates(viewer) == []


def test_http_adapter_against_local_service():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, payload, self.headers["Idempotency-Key"]))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"result":"success"}')

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        service = HTTPHelpdeskService(f"http://127.0.0.1:{server.server_port}")
        assert service.execute_action("stable", "ticket", {"recipient": "local-test"})["status"] == 200
        assert service.compensate_action("stable", "action", "ticket")["status"] == 200
        assert requests[0][1]["details"] == {"recipient": "local-test"}
        assert requests[0][2] == requests[1][2] == "stable"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_demo_reset_with_http_adapter_preserves_provider_history(monkeypatch):
    from app.services import scenario_orchestrator as scenario
    from app.graph.queries import get_graph_store

    monkeypatch.setattr(scenario, "helpdesk_service", HTTPHelpdeskService("http://127.0.0.1:1"))
    # Reset must not call a destructive external reset endpoint or require one.
    scenario.scenario_orchestrator.reset("demo_http")
    store = get_graph_store("demo_http")
    first = store.workspace["demo_run_id"]
    scenario.scenario_orchestrator.reset("demo_http")
    assert store.workspace["demo_run_id"] != first
    assert "fact_30d" in store.nodes


def test_correction_uses_actual_generic_action_payload_and_verified_provenance():
    from app.models.common import EdgeType, Tier
    from app.models.graph import AssertedBy, GraphEdge

    store = GraphStore("generic_correction")
    root = GraphNode(id="verified", kind=NodeKind.FACT, label="Verified delivery policy", key="delivery", value="2",
                     text="Deliveries take two days.", status=Status.HEALTHY,
                     asserted_by=AssertedBy(id="root", name="Root", tier=Tier.ROOT))
    old = GraphNode(id="invalid", kind=NodeKind.FACT, label="Old delivery policy", key="delivery", value="1", status=Status.QUARANTINED)
    node = action(store)
    node.action.payload = {"recipient": "local-recipient", "subject": "Delivery update"}
    store.add_node(root)
    store.add_node(old)
    store.add_edge(GraphEdge(id="cause", source=old.id, target=node.id, type=EdgeType.LEADS_TO))
    notice = saga.generate_correction_notice(node, store)
    assert notice["recipient"] == "local-recipient" and notice["original_subject"] == "Delivery update"
    assert notice["status"] == "READY_FOR_HUMAN_APPROVAL"
    assert notice["provenance"][0]["id"] == root.id
    assert root.text in notice["drafted_body"] and "refund" not in notice["drafted_body"].lower()


def test_restored_candidate_keeps_original_tie_order():
    store = GraphStore("tie_order")
    for name in ("first", "second"):
        store.add_node(GraphNode(id=name, kind=NodeKind.FACT, label="same", status=Status.HEALTHY))
    before = [n.id for n in store.nodes.candidates(Viewer())]
    store.cascade_quarantine("first")
    store.restore_node("first")
    assert [n.id for n in store.nodes.candidates(Viewer())] == before


def test_failed_query_metadata_cannot_relabel_a_later_success(monkeypatch):
    from app.persistence import Persister
    from app import db

    store = GraphStore("query_failures")
    persister = Persister(store)

    class UnavailableGraph:
        def query(self, *args):
            raise ConnectionError("local injected database failure")

    monkeypatch.setattr(db, "graph_for", lambda tenant: UnavailableGraph())
    with pytest.raises(ConnectionError):
        persister._q("failed", "RETURN 1", {})
    store.log_query("successful", "RETURN 2", .2, 1, real=True, timing_boundary="server_execution")
    assert [(q.name, q.outcome) for q in store.query_log] == [("failed", "failure"), ("successful", "success")]
    assert store.query_log[0].origin == "falkordb"
