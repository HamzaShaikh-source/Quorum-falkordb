from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_meta_endpoint():
    resp = client.get("/api/meta", headers={"Authorization": "Bearer token_auditor"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["contract_version"] == "0.1.0"
    assert data["tenant"] == "acme"
    assert data["role"] == "auditor"
    assert "epoch" in data
    assert "seq" in data


def test_auth_rejection_on_missing_token():
    resp = client.get("/api/meta")
    assert resp.status_code == 401
    data = resp.json()
    assert data["error"]["code"] == "UNAUTHENTICATED"


def test_graph_endpoint():
    resp = client.get("/api/graph", headers={"Authorization": "Bearer token_agent_scout"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["contract_version"] == "0.1.0"
    assert len(data["nodes"]) >= 4
    assert len(data["edges"]) >= 3


def test_node_detail_and_provenance():
    resp = client.get("/api/node/act_refund_2", headers={"Authorization": "Bearer token_agent_scout"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["node"]["id"] == "act_refund_2"
    assert len(data["path"]) >= 2
    assert len(data["status_history"]) >= 2


def test_handoff_role_enforcement():
    # Agent trying to claim a REVIEW handoff -> 403 FORBIDDEN_ROLE
    resp = client.post(
        "/api/handoffs/hand_email_gate/claim",
        headers={"Authorization": "Bearer token_agent_scout"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN_ROLE"

    # Human claiming REVIEW handoff -> 200 OK
    resp_human = client.post(
        "/api/handoffs/hand_email_gate/claim",
        headers={"Authorization": "Bearer token_human"},
    )
    assert resp_human.status_code == 200
    assert resp_human.json()["status"] == "IN_PROGRESS"


def test_brain_ask_with_citations_and_refusal():
    # Sourced answer
    resp = client.post(
        "/api/brain/ask",
        json={"question": "What is the policy for refunds?"},
        headers={"Authorization": "Bearer token_human"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["refused"] is False
    assert len(data["citations"]) >= 1
    assert data["citations"][0]["version"] == "3"

    # Unsourced refusal
    resp_refuse = client.post(
        "/api/brain/ask",
        json={"question": "What is the lunch menu in cafeteria?"},
        headers={"Authorization": "Bearer token_human"},
    )
    assert resp_refuse.status_code == 200
    data_refuse = resp_refuse.json()
    assert data_refuse["refused"] is True
    assert data_refuse["reason"] == "no sourced answer found"


def test_x_request_id_header():
    resp = client.get("/api/meta", headers={"Authorization": "Bearer token_human", "X-Request-Id": "custom_req_123"})
    assert resp.headers.get("X-Request-Id") == "custom_req_123"


def test_graph_seq_matches_event_stream_numbering():
    """A client opens the SSE stream from graph.seq; if it drifts from the bus, the console resyncs forever."""
    from app.services.event_bus import event_bus

    h = {"Authorization": "Bearer token_human"}
    client.post("/api/scenario/reset", headers=h)
    client.post("/api/scenario/inject_poison", headers=h)
    graph = client.get("/api/graph", headers=h).json()
    meta = client.get("/api/meta", headers=h).json()
    assert graph["seq"] == event_bus.get_tenant_buffer("acme").current_seq
    assert graph["seq"] == meta["seq"]
