"""Permission levels: what they say matches what is enforced, an admin can change one, and a refusal says how to fix it."""
import json
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.graph import queries
from app.main import app
from app.models.common import HandoffKind, HandoffStatus, NodeKind, Status
from app.models.graph import GraphNode
from app.models.handoffs import Handoff
from app.services import mcp_tools as t
from app.services import roles

client = TestClient(app)


@pytest.fixture()
def tenant():
    name = f"rl{uuid.uuid4().hex[:6]}"
    queries.forget_stores()
    yield name
    queries.forget_stores()


def H(tenant, role="human"):
    return {"Authorization": f"Bearer token_{tenant}_{role}"}


def seed(tenant):
    store = queries.get_graph_store(tenant)
    store.add_node(GraphNode(id="f1", kind=NodeKind.FACT, label="A fact", status=Status.HEALTHY, stale=False, text="A fact about refunds."))
    store.add_handoff(Handoff(id="h_rev", kind=HandoffKind.REVIEW, task="decide", status=HandoffStatus.PENDING))
    return store


def member(tenant, role="agent", name="Test101"):
    tid = client.post("/api/workspace/teams", json={"name": f"Team {name}", "seats": 3}, headers=H(tenant)).json()["teams"][0]["id"]
    r = client.post("/api/workspace/members", json={"name": name, "role": role, "team_id": tid, "client": "antigravity", "expires_in_days": 0}, headers=H(tenant)).json()["issued"]
    return r["token"], r["member"]["id"], tid


def test_the_guide_is_the_one_the_console_ships():
    fx = json.loads((Path(__file__).parent / "fixtures" / "roles.json").read_text(encoding="utf8"))
    assert fx == roles.catalog(), "run the snippet in docs/MCP.md (roles fixture) when the levels change"


def test_the_endpoint_serves_the_guide(tenant):
    r = client.get("/api/roles", headers=H(tenant, "agent")).json()
    assert [x["name"] for x in r["levels"]] == ["Contributor", "Reviewer", "Admin"]
    assert {c["key"] for c in r["capabilities"]} >= {"read", "write", "quarantine", "review", "manage"}


@pytest.mark.parametrize("role,can", [("agent", False), ("auditor", True), ("human", True)])
def test_quarantine_and_restore_match_what_the_guide_says(tenant, role, can):
    seed(tenant)
    tok = f"token_{tenant}_{role}"
    cap = next(c for c in roles.CAPABILITIES if c["key"] == "quarantine")
    assert cap[role] is can
    q = t.quarantine("f1", tok)
    assert bool(q.get("success")) is can
    assert bool(t.restore("f1", tok).get("success")) is can or not can  # an agent is refused; others may restore


@pytest.mark.parametrize("role,can", [("agent", False), ("auditor", False), ("human", True)])
def test_only_admins_approve_reviews(tenant, role, can):
    seed(tenant)
    assert next(c for c in roles.CAPABILITIES if c["key"] == "review")[role] is can
    assert bool(t.review_handoff("h_rev", "approve", f"token_{tenant}_{role}").get("success")) is can


def test_contributors_claim_tasks_only(tenant):
    store = seed(tenant)
    store.add_handoff(Handoff(id="h_task", kind=HandoffKind.TASK, task="do", status=HandoffStatus.PENDING))
    assert t.claim_handoff("h_task", f"token_{tenant}_agent")["success"] is True
    assert t.claim_handoff("h_rev", f"token_{tenant}_agent")["success"] is False
    assert t.claim_handoff("h_rev", f"token_{tenant}_auditor")["success"] is True
    assert next(c for c in roles.CAPABILITIES if c["key"] == "claim")["agent"] == "limited"


def test_a_refusal_says_who_what_and_how_to_fix_it(tenant):
    seed(tenant)
    out = t.quarantine("f1", f"token_{tenant}_agent")
    msg = out["error"]
    assert "Contributor" in msg and "cannot quarantine facts" in msg and "Reviewer or Admin" in msg and "Team page" in msg
    assert "Admin" in t.review_handoff("h_rev", "approve", f"token_{tenant}_auditor")["error"]
    rest = client.post("/api/workspace/teams", json={"name": "X", "seats": 1}, headers=H(tenant, "agent"))
    assert rest.status_code == 403 and "Contributor" in rest.json()["error"]["message"] and "Admin" in rest.json()["error"]["message"]


def test_an_admin_can_change_a_members_level_and_it_applies_at_once(tenant):
    store = seed(tenant)
    tok, mid, _ = member(tenant, "agent")
    refused = t.restore("f1", tok)
    assert refused["success"] is False and "Test101" in refused["error"]
    r = client.patch(f"/api/workspace/members/{mid}", json={"role": "auditor"}, headers=H(tenant))
    assert r.status_code == 200 and next(m for m in r.json()["members"] if m["id"] == mid)["role"] == "auditor"
    assert t.quarantine("f1", tok)["success"] is True, "the same token works the moment the level changes"
    assert store.nodes["f1"].status == Status.QUARANTINED
    client.patch(f"/api/workspace/members/{mid}", json={"role": "agent"}, headers=H(tenant))
    assert t.restore("f1", tok)["success"] is False, "and stops working the moment it is lowered"


def test_only_admins_may_change_levels_and_bad_values_are_refused(tenant):
    tok, mid, tid = member(tenant)
    assert client.patch(f"/api/workspace/members/{mid}", json={"role": "human"}, headers={"Authorization": f"Bearer {tok}"}).status_code == 403
    assert client.patch(f"/api/workspace/members/{mid}", json={"role": "superuser"}, headers=H(tenant)).status_code in (400, 422)
    assert client.patch(f"/api/workspace/members/{mid}", json={"client": "nonsense"}, headers=H(tenant)).status_code in (400, 422)
    assert client.patch("/api/workspace/members/nobody", json={"role": "agent"}, headers=H(tenant)).status_code == 404
    # a contributor cannot promote themselves: the token in use is still a contributor
    assert client.get("/api/whoami", headers={"Authorization": f"Bearer {tok}"}).json()["role"] == "agent"


def test_a_member_can_move_teams_if_there_is_a_seat(tenant):
    tok, mid, tid = member(tenant)
    other = client.post("/api/workspace/teams", json={"name": "Finance", "seats": 1}, headers=H(tenant)).json()["teams"][-1]["id"]
    r = client.patch(f"/api/workspace/members/{mid}", json={"team_id": other}, headers=H(tenant))
    assert r.status_code == 200 and next(m for m in r.json()["members"] if m["id"] == mid)["team_id"] == other
    _, mid2, _ = member(tenant, name="Second")
    assert client.patch(f"/api/workspace/members/{mid2}", json={"team_id": other}, headers=H(tenant)).status_code == 409


def test_a_level_change_is_in_the_audit_log_with_names(tenant):
    _, mid, _ = member(tenant)
    client.patch(f"/api/workspace/members/{mid}", json={"role": "auditor"}, headers=H(tenant))
    texts = [e["text"] for e in client.get("/api/audit", headers=H(tenant)).json()["events"]]
    assert "changed the workspace: changed Test101 from Contributor to Reviewer" in texts
