"""Routes and services the other suites touch only on the happy path: the event stream, the action routes, workspace errors,
system routes, the event bus, the optional LLM adapter, and the embedded MCP mount."""
import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app.graph import queries
from app.main import app
from app.models.common import NodeKind, Status
from app.models.graph import GraphNode
from app.services import mcp_tools as t
from app.services.event_bus import event_bus

client = TestClient(app)


@pytest.fixture()
def tenant():
    name = f"rc{uuid.uuid4().hex[:6]}"
    queries.forget_stores()
    yield name
    queries.forget_stores()


def H(tenant, role="human"):
    return {"Authorization": f"Bearer token_{tenant}_{role}"}


def fact(store, i, text="Refunds are allowed within 30 days of delivery."):
    store.add_node(GraphNode(id=i, kind=NodeKind.FACT, label=text[:60], status=Status.HEALTHY, stale=False, key=i, value="30", text=text))


# ---------- the event stream ----------
def _read_stream(tenant, last_id, until):
    """Reads an SSE response's chunks straight from the route (a TestClient waits for an endless stream to finish)."""
    import asyncio

    from app.auth import AuthContext
    from app.routes.events import sse_events

    async def run():
        resp = await sse_events(last_event_id=None, query_last_id=last_id, auth=AuthContext(tenant=tenant, role="human", token=f"token_{tenant}_human"))
        seen = []
        try:
            async for chunk in resp.body_iterator:
                seen.append(chunk if isinstance(chunk, str) else chunk.decode())
                if until(seen):
                    break
        finally:
            await resp.body_iterator.aclose()
        return "".join(seen)

    return asyncio.run(asyncio.wait_for(run(), timeout=10))


def test_the_event_stream_opens_with_a_heartbeat_and_replays_missed_events(tenant):
    store = queries.get_graph_store(tenant)
    fact(store, "f1")
    event_bus.publish(tenant, "node_added", {"node": store.nodes["f1"].model_dump()})
    text = _read_stream(tenant, 0, lambda seen: any("event: node_added" in c for c in seen))
    assert text.startswith("event: heartbeat") and "event: node_added" in text


def test_a_stale_last_event_id_asks_the_console_to_resync(tenant):
    assert "event: resync_required" in _read_stream(tenant, 999_999, lambda seen: any("resync_required" in c for c in seen))


def test_the_event_stream_needs_a_token():
    assert client.get("/api/events").status_code == 401


# ---------- action routes ----------
def test_staging_and_executing_an_action_by_hand(tenant):
    store = queries.get_graph_store(tenant)
    fact(store, "f1")
    r = client.post("/api/actions/stage", json={"label": "Refund order 7", "action_type": "refund", "reversibility": "COMPENSABLE", "parent_ids": ["f1"]}, headers=H(tenant))
    assert r.status_code == 200, r.text
    aid = r.json()["id"]
    assert any(a["id"] == aid for a in client.get("/api/actions", headers=H(tenant)).json())
    bad = client.post("/api/actions/stage", json={"label": "x", "action_type": "refund", "reversibility": "COMPENSABLE", "parent_ids": ["nope"]}, headers=H(tenant))
    assert bad.status_code == 404 and bad.json()["error"]["code"] == "NOT_FOUND"
    texts = [e["text"] for e in client.get("/api/audit", headers=H(tenant)).json()["events"]]
    assert any("staged in the console" in x for x in texts)


def test_executing_an_unknown_action_is_a_clean_error(tenant):
    r = client.post("/api/actions/does_not_exist/execute", headers=H(tenant))
    assert r.status_code in (404, 409) and "error" in r.json()


# ---------- workspace errors ----------
def test_workspace_routes_refuse_nonsense_cleanly(tenant):
    h = H(tenant)
    assert client.patch("/api/workspace/teams/none", json={"seats": 3}, headers=h).status_code == 404
    assert client.delete("/api/workspace/teams/none", headers=h).status_code == 404
    assert client.post("/api/workspace/members/none/rotate", headers=h).status_code == 404
    assert client.delete("/api/workspace/members/none", headers=h).status_code == 404
    assert client.patch("/api/workspace/links/none", json={"paused": True}, headers=h).status_code == 404
    assert client.delete("/api/workspace/links/none", headers=h).status_code == 404
    assert client.post("/api/workspace/links", json={"a": "x", "b": "y", "scope": "team"}, headers=h).status_code in (404, 409, 422)
    assert client.put("/api/workspace", json={"plan": "galactic"}, headers=h).status_code in (409, 422)
    assert client.post("/api/workspace/teams", json={"name": "A", "seats": 1}, headers=H(tenant, "agent")).status_code == 403


def test_the_full_workspace_lifecycle_through_the_routes(tenant):
    h = H(tenant)
    v = client.post("/api/workspace/teams", json={"name": "Support", "seats": 3}, headers=h).json()
    tid = v["teams"][0]["id"]
    assert client.patch(f"/api/workspace/teams/{tid}", json={"seats": 4}, headers=h).json()["teams"][0]["seats"] == 4
    a = client.post("/api/workspace/members", json={"name": "Mira", "team_id": tid}, headers=h).json()["issued"]["member"]["id"]
    b = client.post("/api/workspace/members", json={"name": "Aisha", "team_id": tid}, headers=h).json()["issued"]["member"]["id"]
    link = client.post("/api/workspace/links", json={"a": a, "b": b, "scope": "team"}, headers=h).json()["links"][0]["id"]
    assert client.patch(f"/api/workspace/links/{link}", json={"paused": True}, headers=h).json()["links"][0]["paused"] is True
    assert client.delete(f"/api/workspace/links/{link}", headers=h).json()["links"] == []
    assert client.delete(f"/api/workspace/members/{a}", headers=h).status_code == 200
    assert client.delete(f"/api/workspace/teams/{tid}", headers=h).status_code in (200, 409)
    texts = [e["text"] for e in client.get("/api/audit", headers=h).json()["events"]]
    for expected in ("added a team", "added a member and issued a token", "linked two members", "paused or resumed a link", "removed a link", "removed a member and revoked their token"):
        assert any(expected in x for x in texts), expected


# ---------- system routes ----------
def test_system_routes_report_what_is_real(tenant):
    h = H(tenant)
    assert isinstance(client.get("/api/cypher-log", headers=h).json(), list)
    denied = client.get("/api/tenants/denied-demo", headers=h).json()
    assert denied["tenant"] == tenant and denied["result"] == "DENIED"
    r = client.get("/api/bench/results", headers=h)
    assert r.status_code in (200, 404)
    if r.status_code == 200:
        assert r.json()["params"]["source"] == "bench/results.json"


# ---------- the event bus ----------
def test_event_bus_sequences_and_scopes_per_tenant():
    a, b = f"eb{uuid.uuid4().hex[:5]}", f"eb{uuid.uuid4().hex[:5]}"
    e1 = event_bus.publish(a, "epoch_changed", {"epoch": 1})
    e2 = event_bus.publish(a, "epoch_changed", {"epoch": 2})
    f1 = event_bus.publish(b, "epoch_changed", {"epoch": 1})
    assert e2.seq == e1.seq + 1 and f1.seq == 1
    buf = event_bus.get_tenant_buffer(a)
    assert [e.seq for e in buf.get_events_since(e1.seq)] == [e2.seq]
    assert buf.get_events_since(10_000) is None  # too old to replay: the console is told to resync


# ---------- the optional LLM adapter has a safe no-key path ----------
def test_llm_adapter_degrades_without_a_key(monkeypatch):
    for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    from app.services.llm import LLMAdapter

    llm = LLMAdapter()
    assert llm.is_available is False and llm.active_provider == "none"
    out = llm.generate_session_summary("s1", [{"role": "user", "content": "hello"}])
    assert isinstance(out, str)


# ---------- the embedded MCP mount ----------
def test_the_embedded_mcp_registers_every_tool_and_serves_routes():
    from app.mcp_embed import TOOLS, build_mcp_app

    mcp_app = build_mcp_app()
    assert mcp_app is not None and len(TOOLS) == 28
    assert any(getattr(r, "path", "").startswith("/mcp") for r in mcp_app.routes)


# ---------- review and claim, the human and agent halves ----------
def test_review_and_claim_handoffs_enforce_roles(tenant):
    from app.models.common import HandoffKind, HandoffStatus
    from app.models.handoffs import Handoff

    store = queries.get_graph_store(tenant)
    store.add_handoff(Handoff(id="h_task", kind=HandoffKind.TASK, task="do it", status=HandoffStatus.PENDING))
    store.add_handoff(Handoff(id="h_rev", kind=HandoffKind.REVIEW, task="decide", status=HandoffStatus.PENDING))
    agent, human = f"token_{tenant}_agent", f"token_{tenant}_human"
    assert t.claim_handoff("h_task", agent)["success"] is True
    assert t.claim_handoff("h_task", agent)["success"] is False, "a live lease cannot be taken twice"
    assert t.claim_handoff("h_rev", agent)["success"] is False, "agents may not claim review handoffs"
    assert t.claim_handoff("missing", agent)["success"] is False
    assert t.review_handoff("h_rev", "approve", agent)["success"] is False
    assert t.review_handoff("h_rev", "approve", human)["status"] == "COMPLETED"
    assert t.review_handoff("missing", "approve", human)["success"] is False


def test_quarantine_restore_and_blast_radius_tools(tenant):
    store = queries.get_graph_store(tenant)
    fact(store, "root")
    auditor, agent = f"token_{tenant}_auditor", f"token_{tenant}_agent"
    assert t.quarantine("root", agent)["success"] is False
    assert t.explain_blast_radius("root", agent)["predicted_impact_count"] == 0
    assert t.quarantine("root", auditor)["affected_count"] == 1
    assert store.nodes["root"].status == Status.QUARANTINED
    assert t.restore("root", agent)["success"] is False
    assert t.restore("root", auditor)["restored_count"] == 1


def test_derive_and_stage_refuse_unhealthy_parents(tenant):
    store = queries.get_graph_store(tenant)
    fact(store, "p")
    tok = f"token_{tenant}_agent"
    assert t.derive("a conclusion", ["p"], tok)["success"] is True
    t.quarantine("p", f"token_{tenant}_auditor")
    assert t.derive("another", ["p"], tok)["success"] is False
    assert t.stage_action("x", "refund", "compensable", ["p"], tok)["success"] is False
    assert json.dumps(t.get_node("p", tok))  # still readable: quarantined nodes are explained, not hidden
