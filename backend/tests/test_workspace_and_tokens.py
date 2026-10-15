"""Workspace (plan, teams, seats, members, links), member tokens as real credentials, team scope, MCP call log and savings."""
import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app import db
from app.auth import resolve_token
from app.config import settings
from app.graph import queries
from app.main import app
from app.services import mcp_tools as t
from app.services import workspace as ws
from app.services.event_bus import event_bus

client = TestClient(app)


@pytest.fixture()
def tenant():
    name = f"wt{uuid.uuid4().hex[:6]}"
    queries.forget_stores()
    yield name
    queries.forget_stores()


def H(tenant, role="human"):
    return {"Authorization": f"Bearer token_{tenant}_{role}"}


def seed_team(tenant, name="Support", seats=3):
    return client.post("/api/workspace/teams", json={"name": name, "seats": seats}, headers=H(tenant)).json()


def team_id(view, name):
    return next(x["id"] for x in view["teams"] if x["name"] == name)


def add_member(tenant, name, tid, role="agent", client_name="claude-code"):
    r = client.post("/api/workspace/members", json={"name": name, "role": role, "team_id": tid, "client": client_name, "device": f"{name} laptop"}, headers=H(tenant))
    assert r.status_code == 200, r.text
    return r.json()["issued"]["token"], r.json()["issued"]["member"]


# ---------- workspace rules ----------
def test_a_new_tenant_starts_empty_not_with_demo_people(tenant):
    v = client.get("/api/workspace", headers=H(tenant)).json()
    assert v["teams"] == [] and v["members"] == [] and v["links"] == []


def test_seats_and_plan_limits_are_enforced_by_the_server(tenant):
    v = seed_team(tenant, "Support", 1)
    tid = team_id(v, "Support")
    add_member(tenant, "Mira", tid)
    r = client.post("/api/workspace/members", json={"name": "Jon", "role": "agent", "team_id": tid, "client": "cursor"}, headers=H(tenant))
    assert r.status_code == 409 and "no free seats" in r.json()["error"]["message"]
    client.put("/api/workspace", json={"plan": "team"}, headers=H(tenant))
    for i in range(2):
        r = client.post("/api/workspace/teams", json={"name": f"T{i}", "seats": 3}, headers=H(tenant))
    assert r.status_code == 409 and "allows 2 teams" in r.json()["error"]["message"]


def test_validation_and_duplicates(tenant):
    assert client.post("/api/workspace/teams", json={"name": "  ", "seats": 3}, headers=H(tenant)).status_code == 422
    seed_team(tenant, "Support")
    assert client.post("/api/workspace/teams", json={"name": "support", "seats": 3}, headers=H(tenant)).status_code == 409
    assert client.post("/api/workspace/teams", json={"name": "X", "seats": 0}, headers=H(tenant)).status_code == 422
    v = client.get("/api/workspace", headers=H(tenant)).json()
    tid = team_id(v, "Support")
    add_member(tenant, "Mira", tid)
    assert client.post("/api/workspace/members", json={"name": "mira", "role": "agent", "team_id": tid, "client": "cursor"}, headers=H(tenant)).status_code == 409
    assert client.post("/api/workspace/members", json={"name": "Zed", "role": "wizard", "team_id": tid, "client": "cursor"}, headers=H(tenant)).status_code == 422
    assert client.put("/api/workspace", json={"hub": "javascript:alert(1)"}, headers=H(tenant)).status_code == 422


def test_only_humans_change_the_workspace(tenant):
    seed_team(tenant)
    for role in ("agent", "auditor"):
        assert client.post("/api/workspace/teams", json={"name": "Nope", "seats": 2}, headers=H(tenant, role)).status_code == 403
    assert client.get("/api/workspace", headers=H(tenant, "agent")).status_code == 200


def test_the_token_is_shown_once_and_only_its_hash_is_kept(tenant):
    tid = team_id(seed_team(tenant), "Support")
    token, member = add_member(tenant, "Mira", tid)
    assert token.startswith(f"qrm_{tenant}_")
    view = client.get("/api/workspace", headers=H(tenant)).json()
    assert token not in json.dumps(view) and "token_hash" not in json.dumps(view)
    stored = queries.get_graph_store(tenant).workspace["members"][0]
    assert stored["token_hash"] == ws.hash_token(token) and token not in json.dumps(stored)


# ---------- tokens are credentials ----------
def test_a_member_token_authenticates_with_the_members_role_and_team(tenant):
    tid = team_id(seed_team(tenant), "Support")
    token, member = add_member(tenant, "Mira", tid, role="auditor")
    info = resolve_token(token)
    assert info["tenant"] == tenant and info["role"] == "auditor" and info["member_id"] == member["id"] and info["team_id"] == tid
    assert client.get("/api/meta", headers={"Authorization": f"Bearer {token}"}).json()["role"] == "auditor"


def test_forged_rotated_and_removed_tokens_are_refused(tenant):
    tid = team_id(seed_team(tenant), "Support")
    token, member = add_member(tenant, "Mira", tid)
    assert resolve_token(f"qrm_{tenant}_{'0' * 32}") is None
    other = f"{tenant}x"
    assert resolve_token(token.replace(tenant, other, 1)) is None  # same secret, other tenant
    new = client.post(f"/api/workspace/members/{member['id']}/rotate", headers=H(tenant)).json()["issued"]["token"]
    assert resolve_token(token) is None and resolve_token(new) is not None
    client.delete(f"/api/workspace/members/{member['id']}", headers=H(tenant))
    assert resolve_token(new) is None


def test_member_tokens_are_valid_in_production_when_demo_tokens_are_not(tenant, monkeypatch):
    tid = team_id(seed_team(tenant), "Support")
    token, _ = add_member(tenant, "Mira", tid)
    monkeypatch.setattr(settings, "ENV", "production")
    monkeypatch.setattr(settings, "ALLOW_DYNAMIC_TOKENS", None)
    monkeypatch.setattr(settings, "DEMO_TOKENS", {})
    assert resolve_token(token) is not None
    assert resolve_token(f"token_{tenant}_human") is None


# ---------- team scope ----------
def _two_teams(tenant):
    v = seed_team(tenant, "Support", 3)
    v = client.post("/api/workspace/teams", json={"name": "Finance", "seats": 3}, headers=H(tenant)).json()
    a_token, a = add_member(tenant, "Mira", team_id(v, "Support"))
    b_token, b = add_member(tenant, "Aisha", team_id(v, "Finance"))
    return a_token, a, b_token, b


def test_team_facts_are_private_until_teams_are_linked(tenant):
    a_token, a, b_token, b = _two_teams(tenant)
    r = t.remember_fact("price_floor", "40", "Support-only pricing floor is 40 percent", a_token, id="f_priv", scope="team")
    assert r["fact_id"] == "f_priv"
    t.remember_fact("refund_days", "30", "The refund window is 30 days", a_token, id="f_pub")

    mine = t.retrieve_context("pricing floor", a_token, format="compact")
    theirs = t.retrieve_context("pricing floor", b_token, format="compact")
    assert "40 percent" in mine["packed"].lower() and "40 percent" not in theirs["packed"].lower()
    assert "refund window" in t.retrieve_context("refund window", b_token, format="compact")["packed"].lower()

    link = client.post("/api/workspace/links", json={"a": a["id"], "b": b["id"], "scope": "team"}, headers=H(tenant)).json()["links"][0]
    assert "40 percent" in t.retrieve_context("pricing floor", b_token, format="compact")["packed"].lower()

    client.patch(f"/api/workspace/links/{link['id']}", json={"paused": True}, headers=H(tenant))
    assert "40 percent" not in t.retrieve_context("pricing floor", b_token, format="compact")["packed"].lower()
    client.patch(f"/api/workspace/links/{link['id']}", json={"paused": False}, headers=H(tenant))
    client.delete(f"/api/workspace/links/{link['id']}", headers=H(tenant))
    assert "40 percent" not in t.retrieve_context("pricing floor", b_token, format="compact")["packed"].lower()


def test_private_facts_do_not_leak_through_the_graph_node_map_or_derive(tenant):
    a_token, a, b_token, b = _two_teams(tenant)
    t.remember_fact("price_floor", "40", "Support-only pricing floor is 40 percent", a_token, id="f_priv", scope="team")
    b_auth = {"Authorization": f"Bearer {b_token}"}
    assert "f_priv" not in {n["id"] for n in client.get("/api/graph", headers=b_auth).json()["nodes"]}
    assert client.get("/api/node/f_priv", headers=b_auth).status_code == 404
    assert "pricing" not in t.get_map(b_token)["map"].lower()
    assert t.get_node("f_priv", b_token)["success"] is False
    d = t.derive("A conclusion about it", ["f_priv"], b_token)
    assert d["success"] is False  # the other team cannot even reference it
    ok = t.derive("Mira's conclusion", ["f_priv"], a_token, id="c_priv")
    assert ok["success"] and queries.get_graph_store(tenant).nodes["c_priv"].scope_team == a["team_id"]  # inherits the privacy


def test_scope_needs_a_member_token_on_a_team(tenant):
    r = t.remember_fact("k", "v", "text", f"token_{tenant}_agent", scope="team")
    assert r["success"] is False and "member token" in r["error"]
    assert t.remember_fact("k", "v", "text", f"token_{tenant}_agent", scope="galaxy")["success"] is False


# ---------- the MCP call log, presence and savings ----------
def _fill(tenant, n=40):
    tok = f"token_{tenant}_agent"
    for i in range(n):
        t.remember_fact(f"refund_{i}", str(10 + i), f"Ticket {1000 + i} says the refund window for region R{i % 5} is {10 + i} days, confirmed by email.", tok, id=f"f{i}")
    return tok


def test_every_call_is_logged_with_tokens_and_a_baseline(tenant):
    tok = _fill(tenant, 5)
    t.guarded(t.retrieve_context)("refund window", tok, format="compact")
    store = queries.get_graph_store(tenant)
    last = store.calls[-1]
    assert last["tool"] == "retrieve_context" and last["summary"] == "refund window" and last["tokens"] > 0 and last["baseline"] > 0 and last["ok"]


def test_compact_costs_far_fewer_tokens_than_full_for_the_same_question(tenant):
    tok = _fill(tenant, 40)
    full = t.retrieve_context("refund window", tok, k=8)
    compact = t.retrieve_context("refund window", tok, format="compact", budget_tokens=250)
    assert compact["tokens"] < full["tokens"] * 0.6
    assert compact["tokens_baseline"] == full["tokens_baseline"] > 0
    assert "context" in full and "context" not in compact


def test_stats_endpoint_counts_only_measured_calls_and_can_go_negative(tenant):
    tok = _fill(tenant, 40)
    g = t.guarded(t.retrieve_context)
    for _ in range(3):
        g("refund window", tok, format="compact", budget_tokens=250)
    g("refund window", tok, k=8)  # full format: costs about the baseline, so saves about nothing
    s = client.get("/api/token-stats", headers=H(tenant)).json()
    assert s["measured_calls"] == 4 and s["calls"] == 4  # the fill used the tools unguarded, so only these four were logged
    assert s["saved"] > 0 and 0 < s["saved_pct"] < 100
    tools = {r["name"]: r for r in s["by_tool"]}
    assert tools["retrieve_context"]["calls"] == 4


def test_activity_feed_and_presence_come_from_real_calls(tenant):
    tid = team_id(seed_team(tenant), "Support")
    token, member = add_member(tenant, "Mira", tid)
    v = client.get("/api/workspace", headers=H(tenant)).json()
    assert v["members"][0]["status"] == "offline" and v["members"][0]["calls"] == 0
    t.guarded(t.retrieve_context)("anything", token, format="compact")
    v = client.get("/api/workspace", headers=H(tenant)).json()
    assert v["members"][0]["status"] == "online" and v["members"][0]["calls"] == 1
    act = client.get("/api/mcp/activity", headers=H(tenant)).json()
    assert act[0]["tool"] == "retrieve_context" and act[0]["member_id"] == member["id"]
    assert client.get(f"/api/mcp/activity?member_id={member['id']}", headers=H(tenant)).json()
    assert client.get("/api/mcp/activity?member_id=nobody", headers=H(tenant)).json() == []


def test_a_call_publishes_a_live_event(tenant):
    tok = _fill(tenant, 3)
    buf = event_bus.get_tenant_buffer(tenant)
    before = buf.current_seq
    t.guarded(t.get_map)(tok)
    evs = [e for e in buf.history if e.seq > before and e.type == "mcp_call"]
    assert evs and evs[-1].data["call"]["tool"] == "get_map"


def test_a_bad_token_is_not_logged_and_returns_an_error(tenant):
    out = t.guarded(t.get_map)("not-a-token")
    assert out["success"] is False
    assert queries.get_graph_store(tenant).calls == []


def test_delta_reports_what_stopped_being_true_and_what_is_new(tenant):
    tok = _fill(tenant, 6)
    t.retrieve_context("refund window", tok, format="compact", budget_tokens=900)
    store = queries.get_graph_store(tenant)
    store.cascade_quarantine("f2")
    t.remember_fact("refund_new", "99", "Brand new refund window 99 days policy update", tok, id="f_new")
    d = t.delta(tok)
    assert "f2" in d["text"] or any(x.startswith("F") for x in d["invalidated"])
    assert d["invalidated"], d
    assert any(x for x in d["new"])
    again = t.delta(tok)
    assert "no changes" in again["text"]
    assert again["tokens"] < d["tokens"] + 5


def test_delta_before_any_read_says_so(tenant):
    assert "nothing read yet" in t.delta(f"token_{tenant}_agent")["text"]


# ---------- context preview ----------
def test_context_preview_endpoint_matches_the_tools(tenant):
    _fill(tenant, 40)
    p = client.get("/api/context/preview?query=refund%20window&budget=300", headers=H(tenant)).json()
    # New privacy metadata increases JSON size. Prose versus top-8 JSON has no
    # universal ordering; compact context must beat both measured baselines.
    assert p["packed_tokens"] < min(p["top8_tokens"], p["prose_tokens"])
    assert max(p["top8_tokens"], p["prose_tokens"]) < p["raw_json_tokens"]
    assert p["map_tokens"] > 0 and "## areas" in p["map"]


# ---------- persistence (needs FalkorDB) ----------
def test_workspace_and_call_log_survive_a_restart(tenant, monkeypatch):
    monkeypatch.setattr(settings, "PERSISTENCE", "falkordb")
    db.reset_client()
    db._health["checked_at"] = 0.0
    if not db.check_health(force=True)["up"]:
        pytest.skip("FalkorDB is not running. Start it with: docker compose up -d falkordb")
    queries.forget_stores()
    try:
        with TestClient(app) as c:
            h = {"Authorization": f"Bearer token_{tenant}_human"}
            tid = c.post("/api/workspace/teams", json={"name": "Support", "seats": 2}, headers=h).json()["teams"][0]["id"]
            r = c.post("/api/workspace/members", json={"name": "Mira", "role": "agent", "team_id": tid, "client": "cursor"}, headers=h).json()
            token = r["issued"]["token"]
            t.guarded(t.get_map)(token)
        queries.forget_stores()
        with TestClient(app) as c:
            h = {"Authorization": f"Bearer {token}"}
            v = c.get("/api/workspace", headers=h).json()
            assert [m["name"] for m in v["members"]] == ["Mira"] and v["members"][0]["calls"] == 1
            assert c.get("/api/mcp/activity", headers=h).json()[0]["tool"] == "get_map"
    finally:
        try:
            db.graph_for(tenant).delete()
        except Exception:
            pass
        queries.forget_stores()


def test_live_events_respect_team_scope(tenant):
    from types import SimpleNamespace

    from app.services.event_scope import event_visible

    a_token, a, b_token, b = _two_teams(tenant)
    t.remember_fact("price_floor", "40", "Support-only pricing floor is 40 percent", a_token, id="f_priv", scope="team")
    store = queries.get_graph_store(tenant)
    node = store.nodes["f_priv"].model_dump(mode="json")
    t.remember_fact("public", "1", "A public endpoint", a_token, id="other")
    vb = ws.viewer_for(store, b["id"])
    va = ws.viewer_for(store, a["id"])
    ev_node = SimpleNamespace(type="node_added", data={"node": node})
    ev_status = SimpleNamespace(type="status_changed", data={"id": "f_priv"})
    ev_edge = SimpleNamespace(type="edge_added", data={"edge": {"source": "f_priv", "target": "other"}})
    for ev in (ev_node, ev_status, ev_edge):
        assert event_visible(store, vb, ev) is False
        assert event_visible(store, va, ev) is True
    assert event_visible(store, ws.viewer_for(store, None), ev_node) is True  # an unscoped service token sees all
    call = SimpleNamespace(type="mcp_call", data={"call": {"member_id": a["id"], "scope_teams": [a["team_id"]]}})
    assert event_visible(store, vb, call) is False and event_visible(store, va, call) is True
    legacy_call = SimpleNamespace(type="mcp_call", data={"call": {"member_id": a["id"]}})
    assert event_visible(store, va, legacy_call) is False


def test_a_forged_member_token_cannot_create_a_tenant(monkeypatch):
    monkeypatch.setattr(settings, "PERSISTENCE", "falkordb")
    db.reset_client()
    db._health["checked_at"] = 0.0
    if not db.check_health(force=True)["up"]:
        pytest.skip("FalkorDB is not running. Start it with: docker compose up -d falkordb")
    ghost = f"ghost{uuid.uuid4().hex[:6]}"
    queries.forget_stores()
    assert resolve_token(f"qrm_{ghost}_{'a' * 32}") is None
    assert f"quorum:{ghost}" not in db.get_client().list_graphs()
    assert resolve_token("qrm_BAD TENANT_zzzz") is None
    assert resolve_token("qrm_x") is None
