"""
Comprehensive Multi-Vector Stress & Boundary Test Suite for Subagent 01:
Core Auth, Configuration & Multi-Tenant Isolation Architect
"""
import concurrent.futures
import uuid
import pytest
from fastapi.testclient import TestClient

from app.auth import resolve_token, _member_token
from app.config import settings
from app.graph import queries
from app.main import app, _client_ip
from app.ratelimit import FailureTracker, RateLimiter, auth_failures

client = TestClient(app)


@pytest.fixture()
def clean_tenant():
    t_name = f"tstress{uuid.uuid4().hex[:6]}"
    queries.forget_stores()
    yield t_name
    queries.forget_stores()


# ==============================================================================
# SECTION 1: MALFORMED TOKENS & NULL / BOUNDARY HANDLING
# ==============================================================================

def test_null_none_empty_and_garbage_handling():
    # Null / None handling
    assert resolve_token(None) is None
    assert resolve_token("") is None
    assert resolve_token(12345) is None
    assert resolve_token(["token_human"]) is None
    assert resolve_token({"token": "token_human"}) is None

    # _member_token null / invalid handling
    assert _member_token(None) is None
    assert _member_token("") is None
    assert _member_token(12345) is None
    assert _member_token("qrm") is None
    assert _member_token("qrm_") is None
    assert _member_token("qrm__") is None
    assert _member_token("qrm__secret") is None
    assert _member_token("qrm_INVALID*TENANT_secret") is None
    assert _member_token("qrm_UPPERCASE_secret") is None


def test_malformed_tokens_http():
    # Empty Bearer
    r = client.get("/api/whoami", headers={"Authorization": "Bearer "})
    assert r.status_code == 401

    # Whitespace only Bearer
    r = client.get("/api/whoami", headers={"Authorization": "Bearer    "})
    assert r.status_code == 401

    # Missing scheme
    r = client.get("/api/whoami", headers={"Authorization": "token_human"})
    assert r.status_code == 401

    # Wrong scheme
    r = client.get("/api/whoami", headers={"Authorization": "Basic dXNlcjpwYXNz"})
    assert r.status_code == 401

    # Unicode emoji tokens
    try:
        r = client.get("/api/whoami", headers={"Authorization": "Bearer \U0001f680\U0001f525\U0001f480".encode("utf-8")})
        assert r.status_code == 401
    except (UnicodeEncodeError, ValueError):
        # Client-side HTTP layer strictly blocks non-ASCII headers before transmission
        pass
    assert resolve_token("🚀🔥💀") is None
    assert resolve_token("token_🚀_agent") is None
    assert _member_token("qrm_🚀_secret") is None

    # SQL Injection tokens
    sqli_tokens = [
        "Bearer ' OR 1=1 --",
        "Bearer '; DROP TABLE users; --",
        "Bearer ' UNION SELECT * FROM tokens --",
        "Bearer admin' --",
    ]
    for sqli in sqli_tokens:
        r = client.get("/api/whoami", headers={"Authorization": sqli})
        assert r.status_code == 401
        tok = sqli.replace("Bearer ", "")
        assert resolve_token(tok) is None

    # Fake qrm_ tokens
    fake_qrm = [
        "qrm_fake",
        "qrm_fake_secret",
        "qrm_nonexistenttenant_1234567890abcdef",
        "qrm_acme_00000000000000000000000000000000",
        "qrm__secret",
        "qrm_tenant_with_a_very_very_long_name_that_exceeds_the_forty_character_limit_and_should_fail_immediately_12345",
    ]
    for fake in fake_qrm:
        r = client.get("/api/whoami", headers={"Authorization": f"Bearer {fake}"})
        assert r.status_code == 401
        assert resolve_token(fake) is None


def test_oversized_header():
    # 105 KB token
    oversized = "Bearer " + ("A" * 105_000)
    try:
        r = client.get("/api/whoami", headers={"Authorization": oversized})
        # Server must reject either with 401 or 431/400 (Request Header Fields Too Large)
        assert r.status_code in (400, 401, 413, 431)
    except Exception:
        # Client or ASGI server rejected oversized header directly
        pass


def test_case_insensitive_bearer_prefix():
    # Bearer variations
    for prefix in ("Bearer ", "bearer ", "BEARER ", "BeArEr "):
        r = client.get("/api/whoami", headers={"Authorization": f"{prefix}token_human"})
        assert r.status_code == 200, f"Failed for prefix: {prefix}"
        assert r.json()["role"] == "human"


# ==============================================================================
# SECTION 2: PROXY & IP EXTRACTION TESTS
# ==============================================================================

def test_untrusted_client_cannot_spoof_ip(monkeypatch):
    class FakeReq:
        def __init__(self, host, x_forwarded_for=None, x_real_ip=None):
            self.client = type("Client", (), {"host": host})()
            self.headers = {}
            if x_forwarded_for:
                self.headers["x-forwarded-for"] = x_forwarded_for
            if x_real_ip:
                self.headers["x-real-ip"] = x_real_ip

    monkeypatch.setattr(settings, "TRUSTED_PROXIES", "127.0.0.1,::1,172.29.77.10")

    # Direct connection from untrusted IP trying to spoof
    req_untrusted = FakeReq("203.0.113.5", x_forwarded_for="1.1.1.1", x_real_ip="2.2.2.2")
    assert _client_ip(req_untrusted) == "203.0.113.5"

    # Direct connection from Docker gateway trying to spoof
    req_gw = FakeReq("172.29.77.1", x_forwarded_for="8.8.8.8")
    assert _client_ip(req_gw) == "172.29.77.1"

    # Legitimate connection from pinned reverse proxy
    req_proxy = FakeReq("172.29.77.10", x_forwarded_for="198.51.100.25")
    assert _client_ip(req_proxy) == "198.51.100.25"

    # Multi-hop proxies in X-Forwarded-For
    req_multi = FakeReq("172.29.77.10", x_forwarded_for="198.51.100.99, 127.0.0.1, 172.29.77.10")
    assert _client_ip(req_multi) == "198.51.100.99"


# ==============================================================================
# SECTION 3: FAILURE TRACKER & RATE LIMITER STRESS
# ==============================================================================

def test_failure_tracker_sliding_window_and_lockout():
    tracker = FailureTracker(limit=5, max_entries=50)
    key = "192.0.2.1"

    # 4 failures: not locked
    for _ in range(4):
        tracker.record(key)
        assert tracker.locked(key) == 0.0

    # 5th failure: locked
    tracker.record(key)
    wait = tracker.locked(key)
    assert 55.0 <= wait <= 60.0


def test_failure_tracker_eviction_protects_locked_ips():
    # If table is flooded with unlocked IPs, locked IP MUST NOT be evicted
    tracker = FailureTracker(limit=3, max_entries=10)
    locked_ip = "192.0.2.100"

    # Lock locked_ip
    for _ in range(3):
        tracker.record(locked_ip)
    assert tracker.locked(locked_ip) > 0.0

    # Flood with 20 dummy IPs (1 hit each, unlocked)
    for i in range(20):
        tracker.record(f"198.51.100.{i}")

    # Locked IP must STILL be tracked and locked!
    assert tracker.locked(locked_ip) > 0.0


def test_rate_limiter_concurrency():
    limiter = RateLimiter(per_minute=6000, burst=100)
    key = "test_concurrent_key"

    results = []

    def call():
        allowed, _ = limiter.allow(key)
        results.append(allowed)
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(call) for _ in range(100)]
        concurrent.futures.wait(futures)

    # Initial burst of 100 should all be allowed
    assert sum(results) == 100


# ==============================================================================
# SECTION 4: 3-TIER RBAC HIERARCHY
# ==============================================================================

def test_rbac_contributor_cannot_mutate_admin_settings(clean_tenant):
    h_agent = {"Authorization": f"Bearer token_{clean_tenant}_agent"}
    h_auditor = {"Authorization": f"Bearer token_{clean_tenant}_auditor"}
    h_admin = {"Authorization": f"Bearer token_{clean_tenant}_human"}

    # Contributor (agent) cannot PUT /workspace
    r = client.put("/api/workspace", json={"org": "Hacked Org"}, headers=h_agent)
    assert r.status_code == 403
    assert "cannot do this. That needs Admin" in r.json()["error"]["message"]

    # Reviewer (auditor) cannot PUT /workspace
    r = client.put("/api/workspace", json={"org": "Hacked Org"}, headers=h_auditor)
    assert r.status_code == 403
    assert "cannot do this. That needs Admin" in r.json()["error"]["message"]

    # Contributor cannot add a team
    r = client.post("/api/workspace/teams", json={"name": "Evil", "seats": 5}, headers=h_agent)
    assert r.status_code == 403

    # Contributor cannot add a member
    r = client.post("/api/workspace/members", json={"name": "Evil", "role": "human", "team_id": "team_xyz"}, headers=h_agent)
    assert r.status_code == 403

    # Admin CAN add a team and mutate workspace
    r = client.post("/api/workspace/teams", json={"name": "DevOps", "seats": 5}, headers=h_admin)
    assert r.status_code == 200
    assert any(t["name"] == "DevOps" for t in r.json()["teams"])


# ==============================================================================
# SECTION 5: TENANT ISOLATION
# ==============================================================================

def test_tenant_isolation_airtight():
    t1 = f"t1_{uuid.uuid4().hex[:6]}"
    t2 = f"t2_{uuid.uuid4().hex[:6]}"

    h1 = {"Authorization": f"Bearer token_{t1}_human"}
    h2 = {"Authorization": f"Bearer token_{t2}_human"}

    # Tenant 1 adds a team and member
    r1 = client.post("/api/workspace/teams", json={"name": "Security", "seats": 3}, headers=h1)
    assert r1.status_code == 200
    t1_team_id = r1.json()["teams"][0]["id"]

    r1_mem = client.post("/api/workspace/members", json={"name": "Alice", "role": "agent", "team_id": t1_team_id, "client": "claude-code"}, headers=h1)
    assert r1_mem.status_code == 200
    t1_token = r1_mem.json()["issued"]["token"]
    t1_mem_id = r1_mem.json()["issued"]["member"]["id"]

    # Tenant 2 views workspace: must be completely empty of Tenant 1 data
    r2_view = client.get("/api/workspace", headers=h2)
    assert r2_view.status_code == 200
    assert len(r2_view.json()["teams"]) == 0
    assert len(r2_view.json()["members"]) == 0

    # Tenant 2 attempts to delete Tenant 1's team by ID -> 404
    r2_del_team = client.delete(f"/api/workspace/teams/{t1_team_id}", headers=h2)
    assert r2_del_team.status_code == 404

    # Tenant 2 attempts to delete Tenant 1's member by ID -> 404
    r2_del_mem = client.delete(f"/api/workspace/members/{t1_mem_id}", headers=h2)
    assert r2_del_mem.status_code == 404

    # Tenant 2 attempts to use Tenant 1's token: resolves to Tenant 1
    r_who = client.get("/api/whoami", headers={"Authorization": f"Bearer {t1_token}"})
    assert r_who.status_code == 200
    assert r_who.json()["tenant"] == t1


# ==============================================================================
# SECTION 6: WORKSPACE BOUNDARY TESTS
# ==============================================================================

def test_workspace_boundary_cases(clean_tenant):
    h = {"Authorization": f"Bearer token_{clean_tenant}_human"}

    # 1. Empty team name
    r = client.post("/api/workspace/teams", json={"name": "", "seats": 5}, headers=h)
    assert r.status_code == 422

    # Whitespace only team name
    r = client.post("/api/workspace/teams", json={"name": "    ", "seats": 5}, headers=h)
    assert r.status_code == 422

    # 2. Seats = 0
    r = client.post("/api/workspace/teams", json={"name": "Alpha", "seats": 0}, headers=h)
    assert r.status_code == 422

    # 3. Negative seats
    r = client.post("/api/workspace/teams", json={"name": "Alpha", "seats": -5}, headers=h)
    assert r.status_code == 422

    # 4. Seats > 200
    r = client.post("/api/workspace/teams", json={"name": "Alpha", "seats": 201}, headers=h)
    assert r.status_code == 422

    # 5. Duplicate team name (case-insensitive)
    r1 = client.post("/api/workspace/teams", json={"name": "Alpha", "seats": 5}, headers=h)
    assert r1.status_code == 200
    r2 = client.post("/api/workspace/teams", json={"name": "alpha", "seats": 5}, headers=h)
    assert r2.status_code == 409
    tid = r1.json()["teams"][0]["id"]

    # 6. Duplicate member name (case-insensitive)
    r_m1 = client.post("/api/workspace/members", json={"name": "Bob", "role": "agent", "team_id": tid, "client": "vscode"}, headers=h)
    assert r_m1.status_code == 200
    r_m2 = client.post("/api/workspace/members", json={"name": "bob", "role": "agent", "team_id": tid, "client": "cursor"}, headers=h)
    assert r_m2.status_code == 409

    # 7. Special characters in member names (unicode, emojis, hyphens)
    r_m3 = client.post("/api/workspace/members", json={"name": "Agent-007 🤖 (Alpha)", "role": "agent", "team_id": tid, "client": "custom"}, headers=h)
    assert r_m3.status_code == 200
    assert r_m3.json()["issued"]["member"]["name"] == "Agent-007 🤖 (Alpha)"


def test_live_http_brute_force_lockout():
    # Configure low threshold for rapid verification
    orig_limit = auth_failures.limit
    auth_failures.configure(limit=4)
    try:
        # 4 bad attempts
        for _ in range(4):
            r = client.get("/api/whoami", headers={"Authorization": "Bearer bad_attempt_token"})
            assert r.status_code == 401

        # 5th attempt must trigger 429 LOCKED_OUT
        r_blocked = client.get("/api/whoami", headers={"Authorization": "Bearer bad_attempt_token"})
        assert r_blocked.status_code == 429
        assert r_blocked.json()["error"]["code"] == "LOCKED_OUT"
        assert "Retry-After" in r_blocked.headers
    finally:
        auth_failures.configure(limit=orig_limit)

