"""
Comprehensive Unit & Invariant Tests for Quorum Query Library
"""

import pytest
from graph import queries

TENANT = "test_unit"

@pytest.fixture(autouse=True)
def setup_test_graph():
    try:
        db = queries.get_db()
        db.select_graph("probe_test_queries").query("RETURN 1")
    except Exception:
        pytest.skip("FalkorDB is not running on localhost:6379. Start it with: docker compose up -d falkordb")
    g = queries.get_graph(TENANT, "commons")
    try:
        g.delete()
    except Exception:
        pass
    queries.ensure_indexes(TENANT)
    yield


def test_atomic_derivation_guard():
    # Insert root fact directly
    g = queries.get_graph(TENANT, "commons")
    g.query("CREATE (:Fact:MemoryNode {id: 'f_root', status: 'HEALTHY', text: 'Root Fact'})")

    # Derive conclusion with healthy root -> must succeed
    res = queries.derive(TENANT, "c1", ["f_root"], "Derived Conclusion")
    assert res["success"] is True

    # Try deriving conclusion with missing parent -> must fail
    res_fail = queries.derive(TENANT, "c2", ["f_nonexistent"], "Failing Conclusion")
    assert res_fail["success"] is False

    # Stage action with c1 -> must succeed
    res_act = queries.stage_action(TENANT, "act_1", ["c1"], "REFUND", {"amount": 100}, "COMPENSABLE")
    assert res_act["success"] is True


def test_execute_guard_and_cascade():
    g = queries.get_graph(TENANT, "commons")
    g.query("CREATE (:Fact:MemoryNode {id: 'f_policy', status: 'HEALTHY', text: '30 day policy'})")

    # Derive conclusion and stage action
    queries.derive(TENANT, "c_elig", ["f_policy"], "Customer Eligible")
    queries.stage_action(TENANT, "act_exec", ["c_elig"], "REFUND", {"amount": 50}, "COMPENSABLE", gate="PASSED")

    # Execute guard before quarantine -> must succeed
    res_exec = queries.execute_guard(TENANT, "act_exec")
    assert res_exec["success"] is True
    assert res_exec["executed"] is True

    # Confirm action
    queries.confirm_action(TENANT, "act_exec")

    # Stage a second action that hasn't executed yet
    queries.stage_action(TENANT, "act_staged", ["c_elig"], "REFUND", {"amount": 75}, "COMPENSABLE", gate="PASSED")

    # Run quarantine cascade on f_policy
    cascade_res = queries.cascade(TENANT, "f_policy")
    assert cascade_res["success"] is True
    assert cascade_res["affected_count"] >= 3 # f_policy, c_elig, act_exec, act_staged

    # Invariant: Execute guard on act_staged MUST FAIL now that ancestor is quarantined
    res_refused = queries.execute_guard(TENANT, "act_staged")
    assert res_refused["success"] is False

    # Create compensations for executed action
    comps = queries.create_compensations(TENANT, cascade_res["executed_actions"])
    assert len(comps) == 1
    assert comps[0]["action_id"] == "act_exec"

    # Check invariants
    inv = queries.invariant_check(TENANT)
    assert inv["healthy_children_under_quarantined"] == 0


def test_supersede_and_stale_propagation():
    g = queries.get_graph(TENANT, "commons")
    g.query("""
    CREATE (f_old:Fact:MemoryNode {id: 'f_old', key: 'refund.window', value: '30', status: 'HEALTHY', text: '30 days'})
    CREATE (dec:Conclusion:Decision:MemoryNode {id: 'dec_1', status: 'HEALTHY', stale: false, owner_team: 'Ops', text: 'Decision'})
    CREATE (f_old)-[:LEADS_TO]->(dec)
    """)

    dummy_vec = [0.1] * 384
    res = queries.supersede(TENANT, "f_old", "f_new", "45", "45 days updated policy", dummy_vec)
    assert res["success"] is True
    assert res["stale_conclusions_count"] == 1

    # Verify old fact status is SUPERSEDED and decision is stale
    check_q = "MATCH (f:Fact {id: 'f_old'}), (d:Decision {id: 'dec_1'}) RETURN f.status, d.stale"
    row = g.query(check_q).result_set[0]
    assert row[0] == "SUPERSEDED"
    assert row[1] is True


def test_handoff_atomic_claim():
    g = queries.get_graph(TENANT, "commons")
    g.query("CREATE (:Handoff {id: 'h_task_1', kind: 'TASK', status: 'PENDING', lease_until: null})")

    # Agent claims handoff
    res = queries.claim_handoff(TENANT, "h_task_1", "agent_scout", role="agent")
    assert res["success"] is True
    assert res["status"] == "IN_PROGRESS"
    assert res["claimed_by"] == "agent_scout"

    # Second claim by another agent while active lease -> must fail
    res_race = queries.claim_handoff(TENANT, "h_task_1", "agent_refunder", role="agent")
    assert res_race["success"] is False

    # Test REVIEW handoff requires human or auditor
    g.query("CREATE (:Handoff {id: 'h_rev_1', kind: 'REVIEW', status: 'PENDING', lease_until: null})")
    res_agent_blocked = queries.claim_handoff(TENANT, "h_rev_1", "agent_scout", role="agent")
    assert res_agent_blocked["success"] is False

    res_human_ok = queries.claim_handoff(TENANT, "h_rev_1", "human_operator", role="human")
    assert res_human_ok["success"] is True
