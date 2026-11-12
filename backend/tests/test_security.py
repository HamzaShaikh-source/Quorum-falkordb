"""Secure by default, sign-in, lockout, size cap, headers, token expiry, and the audit trail for what people do."""
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from app import ratelimit
from app.auth import resolve_token
import app.config as cfg
from app.config import Settings
from app.graph import queries
from app.main import app
from app.services import workspace as ws

client = TestClient(app)


@pytest.fixture()
def tenant():
    name = f"sc{uuid.uuid4().hex[:6]}"
    queries.forget_stores()
    yield name
    queries.forget_stores()


def H(tenant, role="human"):
    return {"Authorization": f"Bearer token_{tenant}_{role}"}


def test_nothing_set_means_production_rules():
    assert Settings.model_fields["ENV"].default == "production"
    prod = Settings(ENV="production")
    assert prod.dynamic_tokens_allowed() is False and prod.mcp_header_required() is True
    dev = Settings(ENV="development")
    assert dev.dynamic_tokens_allowed() is True and dev.mcp_header_required() is False


def test_dev_config_exists_only_in_development(monkeypatch):
    r = client.get("/api/dev-config")
    assert r.status_code == 200 and r.json()["tokens"]["acme"]["human"] == "token_human"
    monkeypatch.setattr(cfg.settings, "ENV", "production")
    assert client.get("/api/dev-config").status_code == 404


def test_whoami_is_how_the_console_signs_in(tenant):
    r = client.get("/api/whoami", headers=H(tenant, "auditor"))
    assert r.status_code == 200 and r.json()["tenant"] == tenant and r.json()["role"] == "auditor"
    assert client.get("/api/whoami", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/api/whoami").status_code == 401


def test_every_response_carries_security_headers():
    r = client.get("/api/health")
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY" and r.headers["referrer-policy"] == "no-referrer"
    assert client.get("/api/whoami", headers={"Authorization": "Bearer token_human"}).headers["cache-control"] == "no-store"


def test_oversized_bodies_are_refused(monkeypatch):
    monkeypatch.setattr(cfg.settings, "MAX_BODY_BYTES", 1000)
    r = client.post("/api/observe", content=b"x" * 2000, headers={"Authorization": "Bearer token_agent_scout", "content-type": "application/json"})
    assert r.status_code == 413 and r.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"


def test_repeated_failed_sign_ins_lock_the_address_out(monkeypatch):
    monkeypatch.setattr("app.main.auth_failures", ratelimit.FailureTracker(3))
    bad = {"Authorization": "Bearer forged"}
    assert [client.get("/api/whoami", headers=bad).status_code for _ in range(3)] == [401, 401, 401]
    r = client.get("/api/whoami", headers=bad)
    assert r.status_code == 429 and r.json()["error"]["code"] == "LOCKED_OUT" and int(r.headers["Retry-After"]) >= 1
    # a correct token from the same address is locked out too until the minute passes: that is the point
    assert client.get("/api/whoami", headers={"Authorization": "Bearer token_human"}).status_code == 429


def test_failures_age_out():
    t = ratelimit.FailureTracker(2)
    t.record("a")
    t.record("a")
    assert t.locked("a") > 0 and t.locked("b") == 0
    t._hits["a"] = [time.monotonic() - 61, time.monotonic() - 61]
    assert t.locked("a") == 0


def test_mcp_failures_count_towards_the_lockout(monkeypatch):
    monkeypatch.setattr("app.main.auth_failures", ratelimit.FailureTracker(2))
    for _ in range(2):
        assert client.post("/mcp/", json={}, headers={"Authorization": "Bearer forged"}).status_code == 401
    assert client.post("/mcp/", json={}, headers={"Authorization": "Bearer forged"}).status_code == 429


# ---------- member tokens expire ----------
def _team(tenant):
    v = client.post("/api/workspace/teams", json={"name": "Support", "seats": 3}, headers=H(tenant)).json()
    return v["teams"][0]["id"]


def test_a_member_token_can_expire_and_rotating_renews_it(tenant):
    tid = _team(tenant)
    r = client.post("/api/workspace/members", json={"name": "Mira", "team_id": tid, "expires_in_days": 30}, headers=H(tenant)).json()
    token, mid = r["issued"]["token"], r["issued"]["member"]["id"]
    assert resolve_token(token) is not None
    exp = r["issued"]["member"]["expires_at"]
    assert abs(exp - (time.time() + 30 * 86400) * 1000) < 60_000
    store = queries.get_graph_store(tenant)
    next(m for m in store.workspace["members"] if m["id"] == mid)["expires_at"] = int(time.time() * 1000) - 1
    assert resolve_token(token) is None, "an expired token is refused"
    assert client.get("/api/whoami", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    new = client.post(f"/api/workspace/members/{mid}/rotate", headers=H(tenant)).json()["issued"]
    assert resolve_token(new["token"]) is not None and new["member"]["expires_at"] > time.time() * 1000


def test_zero_means_never_and_the_hub_default_applies(tenant, monkeypatch):
    tid = _team(tenant)
    never = client.post("/api/workspace/members", json={"name": "Aisha", "team_id": tid, "expires_in_days": 0}, headers=H(tenant)).json()["issued"]["member"]
    assert never.get("expires_at") is None
    monkeypatch.setattr(cfg.settings, "MEMBER_TOKEN_TTL_DAYS", 90)
    dflt = client.post("/api/workspace/members", json={"name": "Jon", "team_id": tid}, headers=H(tenant)).json()["issued"]["member"]
    assert dflt["expires_at"] and abs(dflt["expires_at"] - (time.time() + 90 * 86400) * 1000) < 60_000
    assert client.post("/api/workspace/members", json={"name": "Bad", "team_id": tid, "expires_in_days": -1}, headers=H(tenant)).status_code == 422
    assert ws.settings_ttl(None) == 90


# ---------- what people do is in the audit log ----------
def test_console_actions_are_audited_and_failures_are_not(tenant):
    tid = _team(tenant)  # POST /workspace/teams
    client.post("/api/workspace/teams", json={"name": "Support", "seats": 3}, headers=H(tenant))  # 409 duplicate: must not be logged
    client.post("/api/workspace/members", json={"name": "Mira", "team_id": tid}, headers=H(tenant))
    events = client.get("/api/audit", headers=H(tenant)).json()["events"]
    texts = [e["text"] for e in events]
    assert "changed the workspace: added a team" in texts and "changed the workspace: added a member and issued a token" in texts
    assert texts.count("changed the workspace: added a team") == 1
    assert all(e["severity"] == "alert" for e in events if e["tool"] == "console.workspace")


def test_the_capture_hook_is_audited(tenant):
    client.post("/api/observe", json={"text": "The refund window in Chile is 21 days from delivery.", "source": "hook"}, headers=H(tenant, "agent"))
    texts = [e["text"] for e in client.get("/api/audit", headers=H(tenant)).json()["events"]]
    assert any(x.startswith("captured facts from text “The refund window in Chile") for x in texts)
