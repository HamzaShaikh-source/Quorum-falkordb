"""The chat layer and hub-to-hub connection, end to end, with two hubs in one process (the network is a TestClient)."""
import uuid
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.graph import queries
from app.main import app
from app.services import federation as fed
from app.services import mcp_chat as chat
from app.services import mcp_tools as t
from app.services.mcp_guard import _with_notice

client = TestClient(app)


class _Net:
    """Stands in for httpx.Client: every URL is answered by the app under test, so two hubs can talk inside one test."""

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    @staticmethod
    def _path(url):
        u = urlparse(url)
        return u.path + (f"?{u.query}" if u.query else "")

    def get(self, url, headers=None):
        return client.get(self._path(url), headers=headers or {})

    def post(self, url, json=None, headers=None):
        return client.post(self._path(url), json=json, headers=headers or {})


@pytest.fixture()
def hubs(monkeypatch):
    """hub A (the asker) and hub B (the one being joined, which is the app's default tenant)."""
    a, b = f"fa{uuid.uuid4().hex[:6]}", f"fb{uuid.uuid4().hex[:6]}"
    from app.routes.connect import _public_limiter

    _public_limiter._b.clear()
    queries.forget_stores()
    fed.reset_for_tests()
    monkeypatch.setattr(settings, "DEFAULT_TENANT", b)
    monkeypatch.setattr(settings, "DISCOVERY", True)
    monkeypatch.setattr(settings, "PUBLIC_URL", "http://127.0.0.1:9")
    monkeypatch.setattr(fed, "_http", lambda: _Net())
    sa, sb = queries.get_graph_store(a), queries.get_graph_store(b)
    fed.announce(sa, [{"hub_id": fed.hello(sb)["hub_id"], "name": "Maya Hub", "url": "http://127.0.0.1:9"}])
    yield {"a": f"token_{a}_human", "b": f"token_{b}_human", "sa": sa, "sb": sb, "ta": a, "tb": b}
    queries.forget_stores()
    fed.reset_for_tests()


# ---------- connecting ----------
def test_discover_request_notify_approve_and_use_the_other_hub(hubs):
    d = t.quorum_discover(hubs["a"])
    assert [p["name"] for p in d["peers"]] == ["Maya Hub"] and "quorum_connect" in d["text"]

    r = t.quorum_connect("maya", "hello from A", hubs["a"])
    assert r["success"] is True and r["status"] == "pending"
    assert t.quorum_connect("maya", "", hubs["a"])["success"] is False, "a second request while one is open is refused"

    # the other admin finds out inside the chat they are already in
    assert fed.pending_count(hubs["sb"]) == 1
    noted = _with_notice("get_map", {"epoch": 1}, {"token": hubs["b"]})
    assert "connection request" in noted["notice"]
    inbox = t.quorum_inbox(hubs["b"])
    rid = inbox["requests"][0]["id"]
    assert "waiting for you" in inbox["text"].lower()

    assert t.quorum_respond(rid, "approve", "reviewer", "", hubs["b"])["status"] == "approved"
    assert "notice" not in _with_notice("get_map", {"epoch": 1}, {"token": hubs["b"]}), "no nagging once answered"

    c = t.quorum_connections(False, hubs["a"])
    assert c["changed"] == [{"peer": "Maya Hub", "status": "approved"}]
    assert c["connections"][0]["role"] == "auditor" and "token" not in c["connections"][0]
    again = t.quorum_connections(False, hubs["a"])
    assert again["changed"] == [] and again["connections"][0]["peer_name"] == "Maya Hub"

    # B remembers something; A reads it through the connection, as the member B approved
    t.remember_fact("refund_window", "30 days", "Refunds are allowed within 30 days of delivery.", token=hubs["b"])
    got = t.peer_retrieve("maya", "how long do customers have to ask for a refund", 3, hubs["a"])
    assert got["count"] >= 1 and "30" in got["text"]

    cfg = t.quorum_connections(True, hubs["a"])["config"]["Maya Hub"]
    assert cfg["mcp_url"].endswith("/mcp") and "Authorization" in cfg["claude_code"]


def test_the_token_is_delivered_once_and_only_to_the_holder_of_the_secret(hubs):
    sb = hubs["sb"]
    req = fed.create_request(sb, "Dana", "Dana Hub", "")
    rid, secret = req["request_id"], req["secret"]
    assert fed.collect(sb, rid, secret) == {"status": "pending"}
    with pytest.raises(fed.FedError):
        fed.collect(sb, rid, "wrong")
    fed.respond(sb, rid, True, "agent")
    first = fed.collect(sb, rid, secret)
    assert first["token"].startswith("qrm_")
    assert fed.collect(sb, rid, secret) == {"status": "approved", "delivered": True}
    assert not any("qrm_" in str(v) for v in sb.workspace["fed"]["inbox"]), "no plaintext token is stored on the approving hub"


def test_decline_and_duplicate_and_limits(hubs):
    sb = hubs["sb"]
    r = fed.create_request(sb, "Eli", "E", "")
    fed.respond(sb, r["request_id"], False)
    assert fed.collect(sb, r["request_id"], r["secret"]) == {"status": "declined"}
    with pytest.raises(fed.FedError):
        fed.respond(sb, r["request_id"], True)
    for i in range(fed.MAX_PENDING):
        fed.create_request(sb, f"P{i}", "X", "")
    with pytest.raises(fed.FedError) as e:
        fed.create_request(sb, "Overflow", "X", "")
    assert e.value.status == 429


def test_admin_only_and_a_hub_can_be_invisible(hubs, monkeypatch):
    contributor = f"token_{hubs['ta']}_agent"
    for call in (lambda: t.quorum_connect("maya", "", contributor), lambda: t.quorum_inbox(contributor), lambda: t.quorum_respond("x", "approve", "agent", "", contributor)):
        out = call()
        assert out["success"] is False and "Admin" in out["error"]
    monkeypatch.setattr(settings, "DISCOVERY", False)
    assert client.post("/api/connect/request", json={"person": "Z"}).status_code == 403


def test_peer_addresses_must_be_local_and_well_formed():
    for bad in ("ftp://192.168.1.2", "http://user:pw@192.168.1.2", "http://8.8.8.8:8000", "not a url", "javascript:alert(1)"):
        with pytest.raises(fed.FedError):
            fed.check_peer_url(bad)
    assert fed.check_peer_url("http://192.168.1.20:8000/").endswith("192.168.1.20:8000")
    assert fed.check_peer_url("http://127.0.0.1:8000").endswith("127.0.0.1:8000")


def test_the_public_handshake_is_rate_limited_and_validated(hubs):
    assert client.get("/api/connect/hello").json()["quorum"] == 1
    assert client.post("/api/connect/request", json={"person": ""}).status_code == 422
    assert client.get("/api/connect/requests/cr_nope/status", headers={"X-Connect-Secret": "x"}).status_code == 404
    from app.routes.connect import _public_limiter

    _public_limiter._b.clear()
    codes = [client.post("/api/connect/request", json={"person": f"R{i}"}).status_code for i in range(10)]
    assert 429 in codes


# ---------- the chat layer ----------
def test_help_lists_every_registered_tool():
    from app.mcp_embed import TOOLS

    assert set(chat.COMMANDS) == set(TOOLS)


def test_help_and_the_survey_then_setup(hubs):
    h = t.quorum_help(hubs["a"])
    assert "quorum_connect" in h["text"] and h["your_level"] == "Admin"
    q = t.quorum_start(0, "", "", None, None, hubs["a"])
    assert len(q["survey"]) == 5 and "team" in q["text"].lower()
    done = t.quorum_start(6, "Support", "customer support", True, True, hubs["a"])
    assert done["success"] is True and "Maya Hub" in done["text"]
    team = hubs["sa"].workspace["teams"][0]
    assert (team["name"], team["seats"]) == ("Support", 6)
    again = t.quorum_start(8, "Support Desk", "engineering", False, False, hubs["a"])
    assert again["success"] and hubs["sa"].workspace["teams"][0]["seats"] == 8 and len(hubs["sa"].workspace["teams"]) == 1
    assert t.quorum_start(3, "x", "", None, None, f"token_{hubs['ta']}_agent")["success"] is False


def test_manage_people_and_levels_from_chat(hubs):
    tok = hubs["a"]
    t.quorum_start(5, "Ops", "operations", True, False, tok)
    added = t.quorum_manage("member_add", name="Priya", team="Ops", level="contributor", token=tok)
    assert added["success"] and added["token"].startswith("qrm_") and "claude mcp add" in added["text"]
    assert t.quorum_manage("member_set_level", member="Priya", level="reviewer", token=tok)["success"]
    member = next(m for m in hubs["sa"].workspace["members"] if m["name"] == "Priya")
    assert member["role"] == "auditor"
    assert t.quorum_manage("team_create", name="Billing", seats=3, token=tok)["success"]
    assert t.quorum_manage("member_move", member="Priya", team="Billing", token=tok)["success"]
    t.quorum_manage("member_add", name="Quinn", team="Billing", level="admin", token=tok)
    assert t.quorum_manage("link", member="Priya", other="Quinn", scope="team", token=tok)["success"]
    assert "paused" in t.quorum_manage("pause_link", member="Priya", other="Quinn", token=tok)["text"]
    assert t.quorum_manage("link", member="Priya", other="Quinn", token=tok)["success"] is False, "already linked"
    ov = t.quorum_manage("overview", token=tok)
    assert "Priya" in ov["text"] and "Billing" in ov["text"]
    assert t.quorum_manage("member_new_token", member="Priya", token=tok)["token"].startswith("qrm_")
    assert t.quorum_manage("member_remove", member="Priya", token=tok)["success"]
    assert t.quorum_manage("nonsense", token=tok)["success"] is False
    assert t.quorum_manage("member_set_level", member="Nobody", level="admin", token=tok)["success"] is False

    # a Contributor is refused, with the reason and who can fix it
    new_agent = t.quorum_manage("member_add", name="Rhea", team="Ops", level="contributor", token=tok)["token"]
    refused = t.quorum_manage("team_create", name="Hack", seats=1, token=new_agent)
    assert refused["success"] is False and "Admin" in refused["error"]


def test_status_says_what_is_remembered_what_will_be_and_what_is_shared(hubs):
    tok = hubs["a"]
    t.quorum_start(4, "Support", "customer support", True, False, tok)
    t.remember_fact("limit", "100", "The refund limit is 100 euros per order.", token=tok)
    s = t.quorum_status(tok)
    assert s["counts"]["facts"] == 1 and "capture is on" in s["text"]
    assert "Remembered: 1 facts" in s["text"] and "Shared with" in s["text"] and "Tokens saved" in s["text"]
    assert s["requests_waiting"] == 0


def test_tool_profile_core_lists_only_the_memory_tools(monkeypatch):
    from app.mcp_embed import COORD_TOOLS, CORE_TOOLS, TOOLS, active_tools

    assert len(CORE_TOOLS) == 15 and len(TOOLS) == 28
    monkeypatch.setattr(settings, "TOOL_PROFILE", "core")
    assert active_tools() == CORE_TOOLS + COORD_TOOLS and len(active_tools()) == 17
    monkeypatch.setattr(settings, "TOOL_PROFILE", "full")
    assert active_tools() == TOOLS


def test_connect_by_address_when_discovery_is_blocked(hubs):
    fed.reset_for_tests()  # nothing discovered: the beacon heard nothing
    assert t.quorum_discover(hubs["a"])["peers"] == []
    r = t.quorum_connect("http://127.0.0.1:9", "found you by address", hubs["a"])
    assert r["success"] is True, r
    assert fed.pending_count(hubs["sb"]) == 1
    assert t.quorum_connect("http://127.0.0.1:9", "", hubs["a"])["success"] is False, "no second open request"
    rid = t.quorum_inbox(hubs["b"])["requests"][0]["id"]
    t.quorum_respond(rid, "approve", "contributor", "", hubs["b"])
    assert t.quorum_connections(False, hubs["a"])["connections"][0]["role"] == "agent"
    bad = t.quorum_connect("http://8.8.8.8:8000", "", hubs["a"])
    assert bad["success"] is False and "local network" in bad["error"]
    own = t.quorum_connect("http://127.0.0.1:9", "", hubs["b"])
    assert own["success"] is False
