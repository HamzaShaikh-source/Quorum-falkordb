"""
Multi-Vector QA & Boundary Stress Tests for RBAC, Teams & Workspace Governance.
Subagent_02 QA Suite:
- 3-tier RBAC enforcement across workspace routes and MCP operations
- Tenant isolation across queries and mutations
- Seat limits and team limits enforcement under single-call and concurrent conditions
- Token generation, hashing, one-time reveal, and lifecycle (rotation/revocation/expiry)
- Boundary and edge case parameter validation (empty strings, special chars, negative/zero seats, invalid clients/roles)
"""
import concurrent.futures
import json
import uuid
import pytest
from fastapi.testclient import TestClient

from app.auth import resolve_token
from app.config import settings
from app.graph import queries
from app.main import app
from app.services import workspace as ws
from app.services import roles

client = TestClient(app)


@pytest.fixture()
def tenant_a():
    name = f"wta{uuid.uuid4().hex[:6]}"
    queries.forget_stores()
    yield name
    queries.forget_stores()


@pytest.fixture()
def tenant_b():
    name = f"wtb{uuid.uuid4().hex[:6]}"
    queries.forget_stores()
    yield name
    queries.forget_stores()


def H(tenant: str, role: str = "human"):
    return {"Authorization": f"Bearer token_{tenant}_{role}"}


# =========================================================================
# 1. 3-TIER RBAC TESTS (Contributor, Reviewer, Admin vs agent, auditor, human)
# =========================================================================

def test_rbac_contributor_and_reviewer_cannot_call_admin_workspace_routes(tenant_a):
    """Verify 3-tier RBAC: Contributor (agent) and Reviewer (auditor) cannot mutate the workspace."""
    # Seed a team as human (admin)
    r = client.post("/api/workspace/teams", json={"name": "DevOps", "seats": 5}, headers=H(tenant_a, "human"))
    assert r.status_code == 200
    team_id = r.json()["teams"][0]["id"]

    # Seed a member
    r_mem = client.post(
        "/api/workspace/members",
        json={"name": "Alice", "role": "agent", "team_id": team_id, "client": "cursor"},
        headers=H(tenant_a, "human"),
    )
    assert r_mem.status_code == 200
    member_id = r_mem.json()["issued"]["member"]["id"]
    alice_token = r_mem.json()["issued"]["token"]

    # Test all mutating workspace endpoints with Contributor ("agent") and Reviewer ("auditor")
    unauthorized_roles = ["agent", "auditor"]
    for role in unauthorized_roles:
        h = H(tenant_a, role)

        # PUT /workspace
        res = client.put("/api/workspace", json={"org": "Hacked Org"}, headers=h)
        assert res.status_code == 403, f"Role {role} should be forbidden on PUT /workspace"
        assert res.json()["error"]["code"] == "FORBIDDEN_ROLE"

        # POST /workspace/teams
        res = client.post("/api/workspace/teams", json={"name": f"Team_{role}", "seats": 3}, headers=h)
        assert res.status_code == 403, f"Role {role} should be forbidden on POST /workspace/teams"

        # PATCH /workspace/teams/{team_id}
        res = client.patch(f"/api/workspace/teams/{team_id}", json={"seats": 10}, headers=h)
        assert res.status_code == 403, f"Role {role} should be forbidden on PATCH /workspace/teams"

        # POST /workspace/members
        res = client.post(
            "/api/workspace/members",
            json={"name": f"Bob_{role}", "role": "agent", "team_id": team_id, "client": "cursor"},
            headers=h,
        )
        assert res.status_code == 403, f"Role {role} should be forbidden on POST /workspace/members"

        # PATCH /workspace/members/{member_id}
        res = client.patch(f"/api/workspace/members/{member_id}", json={"role": "human"}, headers=h)
        assert res.status_code == 403, f"Role {role} should be forbidden on PATCH /workspace/members"

        # POST /workspace/members/{member_id}/rotate
        res = client.post(f"/api/workspace/members/{member_id}/rotate", headers=h)
        assert res.status_code == 403, f"Role {role} should be forbidden on member rotate"

        # DELETE /workspace/members/{member_id}
        res = client.delete(f"/api/workspace/members/{member_id}", headers=h)
        assert res.status_code == 403, f"Role {role} should be forbidden on DELETE /workspace/members"

        # DELETE /workspace/teams/{team_id}
        res = client.delete(f"/api/workspace/teams/{team_id}", headers=h)
        assert res.status_code == 403, f"Role {role} should be forbidden on DELETE /workspace/teams"

    # Also test with a real issued Contributor member token
    h_member = {"Authorization": f"Bearer {alice_token}"}
    res = client.post("/api/workspace/teams", json={"name": "AliceTeam", "seats": 3}, headers=h_member)
    assert res.status_code == 403
    assert "Contributor" in res.json()["error"]["message"]

    # But GET /workspace is readable by any authenticated member or role
    assert client.get("/api/workspace", headers=H(tenant_a, "agent")).status_code == 200
    assert client.get("/api/workspace", headers=H(tenant_a, "auditor")).status_code == 200
    assert client.get("/api/workspace", headers=h_member).status_code == 200


def test_roles_service_metadata_and_refusal_messages():
    """Verify role catalog, ordering, naming, and human-readable refusal messages."""
    assert roles.ORDER == ("agent", "auditor", "human")
    assert roles.level_name("agent") == "Contributor"
    assert roles.level_name("auditor") == "Reviewer"
    assert roles.level_name("human") == "Admin"
    assert roles.level_name("unknown") == "unknown"

    cat = roles.catalog()
    assert len(cat["levels"]) == 3
    assert any(c["key"] == "manage" and c["human"] is True and c["agent"] is False for c in cat["capabilities"])

    refusal_msg = roles.refusal("Alice", "agent", "manage teams", "human")
    assert "Alice is a Contributor and cannot manage teams. That needs Admin." in refusal_msg


# =========================================================================
# 2. TENANT ISOLATION TESTS
# =========================================================================

def test_tenant_isolation_member_and_workspace_boundaries(tenant_a, tenant_b):
    """Ensure a member/admin of Tenant A cannot query, mutate, link, or access Tenant B's workspace."""
    # Seed Tenant A
    r_a = client.post("/api/workspace/teams", json={"name": "TeamA", "seats": 5}, headers=H(tenant_a))
    assert r_a.status_code == 200
    team_a_id = r_a.json()["teams"][0]["id"]
    r_ma = client.post(
        "/api/workspace/members",
        json={"name": "MemberA", "role": "agent", "team_id": team_a_id, "client": "vscode"},
        headers=H(tenant_a),
    )
    token_a = r_ma.json()["issued"]["token"]
    member_a_id = r_ma.json()["issued"]["member"]["id"]

    # Seed Tenant B
    r_b = client.post("/api/workspace/teams", json={"name": "TeamB", "seats": 5}, headers=H(tenant_b))
    assert r_b.status_code == 200
    team_b_id = r_b.json()["teams"][0]["id"]
    r_mb = client.post(
        "/api/workspace/members",
        json={"name": "MemberB", "role": "agent", "team_id": team_b_id, "client": "cursor"},
        headers=H(tenant_b),
    )
    member_b_id = r_mb.json()["issued"]["member"]["id"]

    # 1. Tenant A Admin cannot mutate Tenant B's member
    res = client.patch(f"/api/workspace/members/{member_b_id}", json={"role": "human"}, headers=H(tenant_a))
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "NOT_FOUND"

    # 2. Tenant A Admin cannot rotate Tenant B's member token
    res = client.post(f"/api/workspace/members/{member_b_id}/rotate", headers=H(tenant_a))
    assert res.status_code == 404

    # 3. Tenant A Admin cannot delete Tenant B's member
    res = client.delete(f"/api/workspace/members/{member_b_id}", headers=H(tenant_a))
    assert res.status_code == 404

    # 4. Tenant A Admin cannot delete Tenant B's team
    res = client.delete(f"/api/workspace/teams/{team_b_id}", headers=H(tenant_a))
    assert res.status_code == 404

    # 5. Tenant A Admin cannot add a member into Tenant B's team
    res = client.post(
        "/api/workspace/members",
        json={"name": "Intruder", "team_id": team_b_id},
        headers=H(tenant_a),
    )
    assert res.status_code == 404

    # 6. Tenant A Admin cannot link Tenant A member with Tenant B member
    res = client.post(
        "/api/workspace/links",
        json={"a": member_a_id, "b": member_b_id, "scope": "team"},
        headers=H(tenant_a),
    )
    assert res.status_code == 404

    # 7. Forged token across tenants is rejected
    forged_token = token_a.replace(tenant_a, tenant_b, 1)
    res = client.get("/api/workspace", headers={"Authorization": f"Bearer {forged_token}"})
    assert res.status_code == 401


# =========================================================================
# 3. SEAT LIMITS & PLAN LIMITS ENFORCEMENT
# =========================================================================

def test_seat_and_team_limits_enforcement(tenant_a):
    """Test plan seat caps, team seat caps, and plan downgrade restrictions."""
    # Set plan to "team" (10 seats, 2 teams)
    r = client.put("/api/workspace", json={"plan": "team"}, headers=H(tenant_a))
    assert r.status_code == 200
    assert r.json()["limits"]["seats"] == 10
    assert r.json()["limits"]["teams"] == 2

    # Create Team 1 with 6 seats
    r1 = client.post("/api/workspace/teams", json={"name": "Team1", "seats": 6}, headers=H(tenant_a))
    assert r1.status_code == 200
    t1_id = r1.json()["teams"][0]["id"]

    # Create Team 2 with 4 seats
    r2 = client.post("/api/workspace/teams", json={"name": "Team2", "seats": 4}, headers=H(tenant_a))
    assert r2.status_code == 200
    t2_id = r2.json()["teams"][1]["id"]

    # Try to create Team 3 (max teams on "team" plan is 2)
    r3 = client.post("/api/workspace/teams", json={"name": "Team3", "seats": 2}, headers=H(tenant_a))
    assert r3.status_code == 409
    assert "allows 2 teams" in r3.json()["error"]["message"]

    # Add 6 members to Team 1
    for i in range(6):
        rm = client.post(
            "/api/workspace/members",
            json={"name": f"T1_Mem_{i}", "team_id": t1_id},
            headers=H(tenant_a),
        )
        assert rm.status_code == 200

    # 7th member in Team 1 should fail because Team 1 seats == 6
    rm_fail = client.post(
        "/api/workspace/members",
        json={"name": "T1_Mem_6", "team_id": t1_id},
        headers=H(tenant_a),
    )
    assert rm_fail.status_code == 409
    assert "no free seats" in rm_fail.json()["error"]["message"]

    # Add 4 members to Team 2
    for i in range(4):
        rm = client.post(
            "/api/workspace/members",
            json={"name": f"T2_Mem_{i}", "team_id": t2_id},
            headers=H(tenant_a),
        )
        assert rm.status_code == 200

    # Total members is now 10 (plan limit reached)
    # Even if we expand Team 2's seats to 8, adding member 11 should fail due to plan limit
    r_expand = client.patch(f"/api/workspace/teams/{t2_id}", json={"seats": 8}, headers=H(tenant_a))
    assert r_expand.status_code == 200

    rm_plan_cap = client.post(
        "/api/workspace/members",
        json={"name": "T2_Mem_Over", "team_id": t2_id},
        headers=H(tenant_a),
    )
    assert rm_plan_cap.status_code == 409
    assert "All 10 seats on the Team plan are used" in rm_plan_cap.json()["error"]["message"]

    # Try to reduce Team 1 seats below member count (has 6 members, set to 5)
    r_reduce = client.patch(f"/api/workspace/teams/{t1_id}", json={"seats": 5}, headers=H(tenant_a))
    assert r_reduce.status_code == 409
    assert "needs at least 6 seats" in r_reduce.json()["error"]["message"]


def test_plan_downgrade_conflicts(tenant_a):
    """Test plan downgrade prevents orphaned members or teams exceeding limits."""
    # Start on Business plan (default: 50 seats, 8 teams)
    # Create 3 teams
    t_ids = []
    for i in range(3):
        r = client.post("/api/workspace/teams", json={"name": f"BTeam_{i}", "seats": 5}, headers=H(tenant_a))
        t_ids.append(r.json()["teams"][-1]["id"])

    # Try to downgrade to "team" plan (allows only 2 teams) -> should conflict 409
    r_down = client.put("/api/workspace", json={"plan": "team"}, headers=H(tenant_a))
    assert r_down.status_code == 409
    assert "do not fit the Team plan (2 teams)" in r_down.json()["error"]["message"]


# =========================================================================
# 4. TOKEN GENERATION, HASHING, AND SECRET REVEAL
# =========================================================================

def test_token_security_and_storage(tenant_a):
    """Ensure token secret is revealed once, only sha256 hash is persisted, and public_view never leaks hashes."""
    r_team = client.post("/api/workspace/teams", json={"name": "SecurityTeam", "seats": 5}, headers=H(tenant_a))
    t_id = r_team.json()["teams"][0]["id"]

    r_mem = client.post(
        "/api/workspace/members",
        json={"name": "MiraSec", "role": "agent", "team_id": t_id, "client": "cursor"},
        headers=H(tenant_a),
    )
    assert r_mem.status_code == 200
    issued = r_mem.json()["issued"]
    token = issued["token"]
    member = issued["member"]

    # Token structure
    assert token.startswith(f"qrm_{tenant_a}_")
    assert len(token.split("_")[-1]) == 32  # 16 bytes hex

    # Verify sha256 hash stored in backend graph store
    store = queries.get_graph_store(tenant_a)
    stored_member = next(m for m in store.workspace["members"] if m["id"] == member["id"])
    expected_hash = ws.hash_token(token)
    assert stored_member["token_hash"] == expected_hash
    assert token not in json.dumps(stored_member)

    # Verify public_view strips token_hash and plaintext token
    pv = ws.public_view(store)
    pv_json = json.dumps(pv)
    assert token not in pv_json
    assert expected_hash not in pv_json
    assert "token_hash" not in pv_json

    # Member API key authentication works
    auth_info = resolve_token(token)
    assert auth_info is not None
    assert auth_info["tenant"] == tenant_a
    assert auth_info["role"] == "agent"
    assert auth_info["member_id"] == member["id"]

    # Token rotation: invalidates old token immediately and issues new one
    r_rot = client.post(f"/api/workspace/members/{member['id']}/rotate", headers=H(tenant_a))
    assert r_rot.status_code == 200
    new_token = r_rot.json()["issued"]["token"]
    assert new_token != token
    assert resolve_token(token) is None
    assert resolve_token(new_token) is not None

    # Member deletion: invalidates new token immediately
    client.delete(f"/api/workspace/members/{member['id']}", headers=H(tenant_a))
    assert resolve_token(new_token) is None


# =========================================================================
# 5. MULTI-VECTOR BOUNDARY & STRESS TESTS
# =========================================================================

def test_boundary_empty_and_whitespace_names(tenant_a):
    """Boundary test: empty team/member names, whitespace-only, and max-length constraints."""
    # Empty team name
    assert client.post("/api/workspace/teams", json={"name": "", "seats": 5}, headers=H(tenant_a)).status_code == 422
    assert client.post("/api/workspace/teams", json={"name": "   ", "seats": 5}, headers=H(tenant_a)).status_code == 422

    # Over 60 characters team name
    long_name = "A" * 61
    res = client.post("/api/workspace/teams", json={"name": long_name, "seats": 5}, headers=H(tenant_a))
    assert res.status_code == 422
    assert "too long" in res.json()["error"]["message"]

    # Exactly 60 characters team name should pass
    exact_60 = "B" * 60
    res_ok = client.post("/api/workspace/teams", json={"name": exact_60, "seats": 5}, headers=H(tenant_a))
    assert res_ok.status_code == 200
    t_id = res_ok.json()["teams"][0]["id"]

    # Empty member name
    assert client.post("/api/workspace/members", json={"name": "", "team_id": t_id}, headers=H(tenant_a)).status_code == 422
    assert client.post("/api/workspace/members", json={"name": "   ", "team_id": t_id}, headers=H(tenant_a)).status_code == 422

    # Member name over 60 chars
    res_m_long = client.post("/api/workspace/members", json={"name": "M" * 61, "team_id": t_id}, headers=H(tenant_a))
    assert res_m_long.status_code == 422


def test_boundary_seats_values(tenant_a):
    """Boundary test: 0 seats, negative seats, floating point / string, and max 200 seats."""
    # 0 seats
    assert client.post("/api/workspace/teams", json={"name": "ZeroTeam", "seats": 0}, headers=H(tenant_a)).status_code == 422

    # Negative seats
    assert client.post("/api/workspace/teams", json={"name": "NegTeam", "seats": -5}, headers=H(tenant_a)).status_code == 422

    # Seats > 200
    assert client.post("/api/workspace/teams", json={"name": "OverTeam", "seats": 201}, headers=H(tenant_a)).status_code == 422

    # Valid seats boundaries: 1 and 200
    res_1 = client.post("/api/workspace/teams", json={"name": "MinTeam", "seats": 1}, headers=H(tenant_a))
    assert res_1.status_code == 200
    t_id = res_1.json()["teams"][-1]["id"]

    # PATCH seats boundary: 0, negative, >200
    assert client.patch(f"/api/workspace/teams/{t_id}", json={"seats": 0}, headers=H(tenant_a)).status_code == 422
    assert client.patch(f"/api/workspace/teams/{t_id}", json={"seats": -1}, headers=H(tenant_a)).status_code == 422
    assert client.patch(f"/api/workspace/teams/{t_id}", json={"seats": 201}, headers=H(tenant_a)).status_code == 422


def test_boundary_special_characters_in_names(tenant_a):
    """Boundary test: unicode, emoji, script tags, cypher/sql syntax in member and team names."""
    r_team = client.post(
        "/api/workspace/teams",
        json={"name": "チーム 🚀 Engineering", "seats": 10},
        headers=H(tenant_a),
    )
    assert r_team.status_code == 200
    t_id = r_team.json()["teams"][0]["id"]
    assert r_team.json()["teams"][0]["name"] == "チーム 🚀 Engineering"

    # Member with special characters & symbols
    special_names = [
        "Agent-007",
        "José Manuel",
        "<script>alert(1)</script>",
        "Robert'); DROP TABLE Members;--",
        "MATCH (n) DETACH DELETE n",
        'Alice "The Architect"',
    ]
    for s_name in special_names:
        r_m = client.post(
            "/api/workspace/members",
            json={"name": s_name, "team_id": t_id},
            headers=H(tenant_a),
        )
        assert r_m.status_code == 200, f"Failed for name {s_name}: {r_m.text}"
        assert r_m.json()["issued"]["member"]["name"] == s_name


def test_boundary_invalid_enums_and_fields(tenant_a):
    """Boundary test: invalid roles, invalid AI clients, invalid hubs, invalid link scopes."""
    r_team = client.post("/api/workspace/teams", json={"name": "ValidTeam", "seats": 5}, headers=H(tenant_a))
    t_id = r_team.json()["teams"][0]["id"]

    # Invalid roles
    for bad_role in ["superuser", "root", "Admin", "Reviewer", "Contributor", ""]:
        r = client.post(
            "/api/workspace/members",
            json={"name": f"BadRole_{bad_role}", "role": bad_role, "team_id": t_id},
            headers=H(tenant_a),
        )
        assert r.status_code == 422

    # Invalid clients
    for bad_client in ["sublime", "emacs", "unknown_client", ""]:
        r = client.post(
            "/api/workspace/members",
            json={"name": f"BadClient_{bad_client}", "client": bad_client, "team_id": t_id},
            headers=H(tenant_a),
        )
        assert r.status_code == 422

    # Invalid link scopes and self-link
    r_m1 = client.post("/api/workspace/members", json={"name": "M1", "team_id": t_id}, headers=H(tenant_a))
    r_m2 = client.post("/api/workspace/members", json={"name": "M2", "team_id": t_id}, headers=H(tenant_a))
    m1_id = r_m1.json()["issued"]["member"]["id"]
    m2_id = r_m2.json()["issued"]["member"]["id"]

    # Self link
    assert client.post("/api/workspace/links", json={"a": m1_id, "b": m1_id}, headers=H(tenant_a)).status_code == 422
    # Invalid scope
    assert client.post("/api/workspace/links", json={"a": m1_id, "b": m2_id, "scope": "invalid"}, headers=H(tenant_a)).status_code == 422

    # Invalid hub URLs
    assert client.put("/api/workspace", json={"hub": "ftp://example.com"}, headers=H(tenant_a)).status_code == 422
    assert client.put("/api/workspace", json={"hub": "javascript:alert(1)"}, headers=H(tenant_a)).status_code == 422


# =========================================================================
# 6. CONCURRENCY & RACE CONDITION TESTS
# =========================================================================

def test_concurrency_race_condition_seat_limits_exact_cap(tenant_a):
    """
    Stress test: Launch 30 concurrent threads trying to add a member when only 1 seat is left.
    Under a proper lock / atomic check, EXACTLY 1 request must succeed (200),
    and all 29 others must be rejected with 409 Conflict.
    Total members in the team must never exceed the seat cap.
    """
    # Create team with 1 seat
    r_team = client.post("/api/workspace/teams", json={"name": "HighContention", "seats": 1}, headers=H(tenant_a))
    assert r_team.status_code == 200
    t_id = r_team.json()["teams"][0]["id"]

    def try_add_member(idx: int):
        # Separate client calls in parallel threads
        return client.post(
            "/api/workspace/members",
            json={"name": f"Contender_{idx}", "team_id": t_id, "client": "cursor"},
            headers=H(tenant_a),
        )

    num_threads = 30
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(try_add_member, i) for i in range(num_threads)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    success_count = sum(1 for r in results if r.status_code == 200)
    conflict_count = sum(1 for r in results if r.status_code == 409)

    assert success_count == 1, f"Expected exactly 1 success under seat cap, got {success_count}"
    assert conflict_count == num_threads - 1, f"Expected {num_threads - 1} conflicts, got {conflict_count}"

    # Verify final workspace state
    vw = client.get("/api/workspace", headers=H(tenant_a)).json()
    assert len(vw["members"]) == 1


def test_concurrency_race_condition_plan_seats_cap(tenant_a):
    """
    Stress test: Plan has 10 seats. We have 2 teams with 10 seats each.
    Workspace already has 9 members. 1 plan seat remains.
    Launch 30 concurrent requests across both teams.
    EXACTLY 1 request must succeed, and 29 must receive 409 Conflict.
    """
    client.put("/api/workspace", json={"plan": "team"}, headers=H(tenant_a))
    r_t1 = client.post("/api/workspace/teams", json={"name": "TeamA", "seats": 10}, headers=H(tenant_a))
    r_t2 = client.post("/api/workspace/teams", json={"name": "TeamB", "seats": 10}, headers=H(tenant_a))
    t1_id = r_t1.json()["teams"][0]["id"]
    t2_id = r_t2.json()["teams"][1]["id"]

    # Pre-fill 9 members
    for i in range(9):
        rm = client.post("/api/workspace/members", json={"name": f"BaseMem_{i}", "team_id": t1_id}, headers=H(tenant_a))
        assert rm.status_code == 200

    def try_add_concurrent(idx: int):
        target_tid = t1_id if idx % 2 == 0 else t2_id
        return client.post(
            "/api/workspace/members",
            json={"name": f"BurstMem_{idx}", "team_id": target_tid},
            headers=H(tenant_a),
        )

    num_threads = 30
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(try_add_concurrent, i) for i in range(num_threads)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    successes = [r for r in results if r.status_code == 200]
    conflicts = [r for r in results if r.status_code == 409]

    assert len(successes) == 1, f"Expected exactly 1 success under plan seat cap, got {len(successes)}"
    assert len(conflicts) == num_threads - 1

    vw = client.get("/api/workspace", headers=H(tenant_a)).json()
    assert len(vw["members"]) == 10
