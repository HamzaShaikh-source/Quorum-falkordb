"""Regression proofs for the audited production boundaries."""
from concurrent.futures import ThreadPoolExecutor
import asyncio

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.config import Settings, settings
from app.graph.queries import GraphStore, get_graph_store
from app.main import app
from app.models.common import NodeKind, Status
from app.models.graph import GraphNode
from app.services import mcp_tools as tools


def test_production_cannot_enable_forged_tokens():
    assert not Settings(ENV="production", ALLOW_DYNAMIC_TOKENS=True).dynamic_tokens_allowed()
    assert Settings(ENV="development", ALLOW_DYNAMIC_TOKENS=True).dynamic_tokens_allowed()


def test_create_cannot_replace_quarantined_or_other_kind():
    store = GraphStore("collision")
    node = GraphNode(id="f", kind=NodeKind.FACT, label="original", status=Status.QUARANTINED)
    store.add_node(node)
    for kind in (NodeKind.FACT, NodeKind.ACTION):
        with pytest.raises(HTTPException) as exc:
            store.add_node(GraphNode(id="f", kind=kind, label="changed", status=Status.HEALTHY))
        assert exc.value.status_code == 409
    assert store.nodes["f"] is node and node.status == Status.QUARANTINED


def test_concurrent_creates_have_one_winner():
    store = GraphStore("concurrent")

    def create(i):
        try:
            store.add_node(GraphNode(id="same", kind=NodeKind.FACT, label=str(i), status=Status.HEALTHY))
            return True
        except HTTPException:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(create, range(32))) == 1


def test_harmless_retries_do_not_recreate_nodes_edges_or_handoffs():
    token = "token_retries_human"
    assert tools.remember_fact("policy", "30", "A policy fact", token=token, id="f")["success"]
    assert tools.remember_fact("policy", "30", "A policy fact", token=token, id="f")["success"]
    assert tools.derive("A conclusion", ["f"], token=token, id="c")["success"]
    payload = {"recipient": "local-recipient", "subject": "Ticket"}
    assert tools.stage_action("ticket", "ticket", "REVERSIBLE", ["c"], token=token, id="a", payload=payload)["success"]
    store = get_graph_store("retries")
    original = store.nodes["a"]
    assert tools.derive("A conclusion", ["f"], token=token, id="c")["success"]
    assert tools.stage_action("ticket", "ticket", "REVERSIBLE", ["c"], token=token, id="a", payload=payload)["success"]
    assert len(store.nodes) == 3 and len(store.edges) == 2 and not store.handoffs
    assert store.nodes["a"] is original
    assert original.action.payload == payload
    assert not tools.derive("different", ["f"], token=token, id="c")["success"]


def test_private_actions_with_real_member_tokens(monkeypatch):
    from app.services import workspace as ws

    client = TestClient(app)
    tenant = "auditprivacy"
    admin = {"Authorization": f"Bearer token_{tenant}_human"}
    tokens, members = [], []
    for name in ("A", "B"):
        response = client.post("/api/workspace/teams", json={"name": name, "seats": 2}, headers=admin)
        tid = next(t["id"] for t in response.json()["teams"] if t["name"] == name)
        response = client.post("/api/workspace/members", json={"name": name, "role": "human", "team_id": tid, "client": "cursor"}, headers=admin)
        tokens.append(response.json()["issued"]["token"])
        members.append(response.json()["issued"]["member"])
    # Real configured member tokens continue to work in production; dynamic service token no longer needed.
    monkeypatch.setattr(settings, "ENV", "production")
    a, b = tokens
    assert tools.remember_fact("secret", "1", "Private policy", token=a, id="private", scope="team")["success"]
    body = {"id": "private_action", "label": "internal", "action_type": "ticket", "reversibility": "REVERSIBLE", "parent_ids": ["private"]}
    def headers(t):
        return {"Authorization": f"Bearer {t}"}
    assert client.post("/api/actions/stage", json=body, headers=headers(a)).status_code == 200
    assert client.get("/api/actions", headers=headers(b)).json() == []
    assert client.post("/api/actions/private_action/execute", headers=headers(b)).status_code == 404
    assert not tools.execute_action("private_action", token=b)["success"]
    assert not tools.derive("steal", ["private"], token=b)["success"]
    body["id"] = "stolen"
    assert client.post("/api/actions/stage", json=body, headers=headers(b)).status_code in (404, 409)
    store = get_graph_store(tenant)
    # Exercise the established member-link path, rather than injecting a privileged viewer.
    link = ws.link(store, members[0]["id"], members[1]["id"], "team")
    assert len(client.get("/api/actions", headers=headers(b)).json()) == 1
    assert tools.remember_fact("other", "2", "B private policy", token=b, id="private_b", scope="team")["success"]
    mixed = tools.derive("Combined private conclusion", ["private", "private_b"], token=a, id="mixed")
    assert mixed["success"]
    assert len(store.nodes["mixed"].scope_teams) == 2
    ws.unlink(store, link["id"])
    for token in tokens:
        assert client.get("/api/node/mixed", headers=headers(token)).status_code == 404
    assert not tools.remember_fact("secret", "hacked", "overwrite", token=b, id="private")["success"]
    assert store.nodes["private"].value == "1"


@pytest.mark.asyncio
async def test_private_stream_refreshes_links_and_revocation():
    from app.auth import AuthContext, resolve_token
    from app.routes.events import sse_events
    from app.services import workspace as ws
    from app.services.event_bus import event_bus

    client = TestClient(app)
    tenant = "streamprivacy"
    admin = {"Authorization": f"Bearer token_{tenant}_human"}
    tokens, members = [], []
    for name in ("A", "B"):
        team = client.post("/api/workspace/teams", json={"name": name, "seats": 1}, headers=admin).json()["teams"][-1]
        issued = client.post("/api/workspace/members", json={"name": name, "role": "human", "team_id": team["id"]}, headers=admin).json()["issued"]
        tokens.append(issued["token"])
        members.append(issued["member"])
    store = get_graph_store(tenant)
    link = ws.link(store, members[0]["id"], members[1]["id"], "team")
    tools.remember_fact("private", "1", "private text", token=tokens[0], id="secret", scope="team")
    response = await sse_events(last_event_id=None, query_last_id=None,
                                auth=AuthContext(**resolve_token(tokens[1]), token=tokens[1]))
    stream = response.body_iterator
    assert "heartbeat" in await anext(stream)
    waiting = asyncio.create_task(anext(stream))
    await asyncio.sleep(0)
    ws.unlink(store, link["id"])
    event_bus.publish(tenant, "node_added", {"node": store.nodes["secret"].model_dump(mode="json")})
    event_bus.publish(tenant, "workspace_changed", {})
    delivered = await asyncio.wait_for(waiting, 2)
    assert "workspace_changed" in delivered and "private text" not in delivered
    # Removing the member revokes its token while the original response is still open.
    client.delete(f"/api/workspace/members/{members[1]['id']}", headers=admin)
    waiting = asyncio.create_task(anext(stream))
    await asyncio.sleep(0)
    event_bus.publish(tenant, "workspace_changed", {})
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(waiting, 2)

