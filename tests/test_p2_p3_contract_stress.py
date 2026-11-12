"""
Quorum Cross-Repo & P2/P3 Contract Boundary Stress Test Suite
(tests/test_p2_p3_contract_stress.py)

Author: Subagent 20 (Cross-Repo & P2/P3 Contract Boundary Stress Tester)
Validates:
1. contracts/events.schema.json and backend integration models.
2. All 25 Cypher functions in graph/queries.py conform to P2 and P3 expected schemas.
3. Error handling: invalid parameters return structured dicts or clear exceptions, no unhandled FalkorDB crashes.
4. Function signatures, return keys, and status enums (HEALTHY, QUARANTINED, STALE, COMPENSATED, PENDING, IN_PROGRESS, COMPLETED).
"""

import json
import os
import time
import pytest
import jsonschema

from graph import queries

TENANT = "contract_stress_tenant"
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "..", "contracts", "events.schema.json")


@pytest.fixture(scope="module")
def events_schema():
    with open(SCHEMA_PATH, "r") as f:
        return json.load(f)


def reset_graph(tenant: str = TENANT, agent_id: str = "agent_scout"):
    g = queries.get_graph(tenant, "commons")
    try:
        g.delete()
    except Exception:
        pass
    queries.ensure_indexes(tenant)
    raw_g = queries.get_graph(tenant, f"agent_{agent_id}:raw")
    try:
        raw_g.delete()
    except Exception:
        pass
    return g


# =========================================================================
# 1. CONTRACTS/EVENTS.SCHEMA.JSON & CORE DEFINITIONS VALIDATION
# =========================================================================

def test_events_schema_loading_and_defs(events_schema):
    """Verifies that events.schema.json is valid draft 2020-12 schema and contains required defs."""
    jsonschema.Draft202012Validator.check_schema(events_schema)
    defs = events_schema.get("$defs", {})
    assert "Status" in defs
    assert "CompensationStatus" in defs
    assert "GraphNode" in defs
    assert "GraphEdge" in defs
    assert "Handoff" in defs

    # Verify Enum definitions
    assert defs["Status"]["enum"] == ["HEALTHY", "SUPERSEDED", "QUARANTINED"]
    assert defs["CompensationStatus"]["enum"] == [
        "PENDING_COMPENSATION", "DISPATCHING", "COMPENSATED", "FAILED"
    ]
    assert defs["Handoff"]["properties"]["status"]["enum"] == [
        "PENDING", "IN_PROGRESS", "COMPLETED", "TIMED_OUT"
    ]


def test_events_schema_event_envelopes(events_schema):
    """Validates representative SSE envelopes against events.schema.json."""
    validator = jsonschema.Draft202012Validator(events_schema)

    # Status Changed event
    ev_status = {
        "seq": 101,
        "ts": int(time.time() * 1000),
        "epoch": 2,
        "type": "status_changed",
        "request_id": "req_status_test",
        "data": {
            "id": "fact_test_1",
            "status": "QUARANTINED",
            "stale": False,
            "depth": 0,
            "root_id": "fact_test_1"
        }
    }
    validator.validate(ev_status)

    # Handoff Claimed event
    ev_claim = {
        "seq": 102,
        "ts": int(time.time() * 1000),
        "epoch": 2,
        "type": "handoff_claimed",
        "request_id": "req_claim_test",
        "data": {
            "handoff_id": "hand_01",
            "winner": "agent_alpha",
            "losers": ["agent_beta"],
            "lease_until": int(time.time() * 1000) + 60000
        }
    }
    validator.validate(ev_claim)

    # Compensation Updated event
    ev_comp = {
        "seq": 103,
        "ts": int(time.time() * 1000),
        "epoch": 3,
        "type": "compensation_updated",
        "data": {
            "action_id": "act_test_1",
            "status": "PENDING_COMPENSATION",
            "attempts": 0
        }
    }
    validator.validate(ev_comp)


# =========================================================================
# 2. ALL 25 CYPHER FUNCTIONS CONFORMANCE & SIGNATURE VERIFICATION
# =========================================================================

def test_all_25_functions_exist():
    """Verify all 25 expected Cypher functions are callable in graph/queries.py."""
    expected_functions = [
        "stage_action",
        "derive",
        "execute_guard",
        "confirm_action",
        "cascade",
        "create_compensations",
        "supersede",
        "supersede_with_review",
        "restore",
        "claim_handoff",
        "retrieve",
        "as_of",
        "provenance",
        "who_knows",
        "blast_preview",
        "invariant_check",
        "graph_snapshot",
        "record_episode",
        "upsert_procedure",
        "score_procedure",
        "rank_procedures",
        "close_session",
        "resume_agent",
        "session_history",
        "brain_ask",
    ]
    assert len(expected_functions) == 25
    for fn in expected_functions:
        assert hasattr(queries, fn), f"Function {fn} missing from graph.queries"
        assert callable(getattr(queries, fn)), f"{fn} is not callable"


def test_conformance_stage_action_and_derive():
    """Verify stage_action and derive input parameters and return schemas."""
    g = reset_graph()
    g.query(
        "CREATE (f:Fact:MemoryNode {id: 'f_root', status: 'HEALTHY', text: 'Root Fact', valid_from: timestamp(), stale: false})"
    )

    # 1. derive
    res_derive = queries.derive(TENANT, "c_derived", ["f_root"], "Derived conclusion", is_decision=True, owner_team="ops-team")
    assert res_derive["success"] is True
    assert res_derive["conclusion_id"] == "c_derived"
    assert res_derive["status"] == "HEALTHY"

    # 2. stage_action
    res_stage = queries.stage_action(
        tenant_id=TENANT,
        action_id="act_01",
        parent_ids=["c_derived"],
        action_type="REFUND",
        payload={"order_id": 1042},
        reversibility="COMPENSABLE",
        gate="PASSED",
        procedure_id="proc_refund",
        idempotency_key="idem_act_01"
    )
    assert res_stage["success"] is True
    assert res_stage["action_id"] == "act_01"
    assert res_stage["status"] == "HEALTHY"
    assert res_stage["gate"] == "PASSED"


def test_conformance_execute_guard_and_confirm_action():
    """Verify execute_guard and confirm_action schemas and states."""
    g = reset_graph()
    g.query("CREATE (f:Fact:MemoryNode {id: 'f_guard', status: 'HEALTHY', text: 'Guard Parent'})")
    queries.derive(TENANT, "c_guard", ["f_guard"], "Guard Conclusion")
    queries.stage_action(TENANT, "act_guard", ["c_guard"], "REFUND", {"id": 1}, "COMPENSABLE", gate="PASSED")

    # execute_guard
    res_exec = queries.execute_guard(TENANT, "act_guard")
    assert res_exec["success"] is True
    assert res_exec["action_id"] == "act_guard"
    assert res_exec["executed"] is True
    assert isinstance(res_exec["executed_at"], int)
    assert "idempotency_key" in res_exec
    assert res_exec["action_type"] == "REFUND"

    # confirm_action
    confirmed = queries.confirm_action(TENANT, "act_guard")
    assert confirmed is True

    # Check node in graph
    res_node = g.query("MATCH (a:Action {id: 'act_guard'}) RETURN a.executed, a.confirmed").result_set[0]
    assert res_node[0] is True
    assert res_node[1] is True


def test_conformance_cascade_and_compensations():
    """Verify cascade and create_compensations conforming to Blast and Compensation schemas."""
    g = reset_graph()
    g.query("CREATE (f:Fact:MemoryNode {id: 'f_poison', status: 'HEALTHY', text: 'Poison Fact'})")
    queries.derive(TENANT, "c_poison", ["f_poison"], "Poison Conclusion")
    queries.stage_action(TENANT, "act_poison_exec", ["c_poison"], "REFUND", {}, "COMPENSABLE", gate="PASSED")
    queries.execute_guard(TENANT, "act_poison_exec")

    # cascade
    res_casc = queries.cascade(TENANT, "f_poison")
    assert res_casc["success"] is True
    assert res_casc["root_id"] == "f_poison"
    assert res_casc["affected_count"] >= 3
    assert isinstance(res_casc["ms_to_containment"], float)
    assert isinstance(res_casc["node_ids_by_depth"], dict)
    assert len(res_casc["executed_actions"]) == 1
    assert res_casc["executed_actions"][0]["action_id"] == "act_poison_exec"
    assert res_casc["executed_actions"][0]["reversibility"] == "COMPENSABLE"

    # create_compensations
    comps = queries.create_compensations(TENANT, res_casc["executed_actions"])
    assert len(comps) == 1
    assert comps[0]["action_id"] == "act_poison_exec"
    assert comps[0]["compensation_id"] == "comp_act_poison_exec"
    assert comps[0]["status"] == "PENDING_COMPENSATION"


def test_conformance_supersede_and_supersede_with_review():
    """Verify supersede flows, stale flag propagation, and REVIEW handoff creation."""
    g = reset_graph()
    vec = [0.05] * 384
    g.query(
        "CREATE (f:Fact:MemoryNode {id: 'f_old', key: 'policy.window', value: '30', "
        "status: 'HEALTHY', text: 'Return window 30 days', embedding: vecf32($vec), "
        "valid_from: timestamp(), stale: false})",
        {"vec": vec}
    )
    queries.derive(TENANT, "dec_policy", ["f_old"], "Decision using 30 days", is_decision=True)

    # 1. supersede
    new_vec = [0.08] * 384
    res_sup = queries.supersede(TENANT, "f_old", "f_new", "60", "Return window 60 days", new_vec)
    assert res_sup["success"] is True
    assert res_sup["new_fact_id"] == "f_new"
    assert res_sup["stale_conclusions_count"] >= 1
    assert isinstance(res_sup["epoch"], int)

    # Verify old fact status is SUPERSEDED
    old_status = g.query("MATCH (f:Fact {id: 'f_old'}) RETURN f.status").result_set[0][0]
    assert old_status == "SUPERSEDED"

    # 2. supersede_with_review
    res_rev = queries.supersede_with_review(TENANT, "policy.window", "f_new2", "Return window 90 days", "90")
    assert res_rev["success"] is True
    assert res_rev["new_fact_id"] == "f_new2"
    assert "review_handoffs_created" in res_rev


def test_conformance_restore():
    """Verify restore only restores if all parents are HEALTHY and never restores executed actions."""
    g = reset_graph()
    g.query("CREATE (f:Fact:MemoryNode {id: 'f_rest_root', status: 'HEALTHY', text: 'Restore Root'})")
    queries.derive(TENANT, "c_rest", ["f_rest_root"], "Restore Conc")
    queries.stage_action(TENANT, "act_rest_staged", ["c_rest"], "EMAIL", {}, "REVERSIBLE", gate="PASSED")
    queries.stage_action(TENANT, "act_rest_exec", ["c_rest"], "REFUND", {}, "COMPENSABLE", gate="PASSED")
    queries.execute_guard(TENANT, "act_rest_exec")

    # Quarantine cascade
    queries.cascade(TENANT, "f_rest_root")

    # Restore root
    res_rest = queries.restore(TENANT, "f_rest_root")
    assert res_rest["success"] is True
    assert "f_rest_root" in res_rest["restored_nodes"]
    assert "c_rest" in res_rest["restored_nodes"]
    assert "act_rest_staged" in res_rest["restored_nodes"]
    # NEVER restore executed actions
    assert "act_rest_exec" not in res_rest["restored_nodes"]

    # Verify executed action status remains QUARANTINED
    exec_status = g.query("MATCH (a:Action {id: 'act_rest_exec'}) RETURN a.status").result_set[0][0]
    assert exec_status == "QUARANTINED"


def test_conformance_claim_handoff():
    """Verify claim_handoff atomic claim, lease timestamp, and winner schema."""
    g = reset_graph()
    g.query("CREATE (:Handoff {id: 'h_task_01', kind: 'TASK', status: 'PENDING'})")

    res_claim = queries.claim_handoff(TENANT, "h_task_01", "agent_worker_1", role="agent")
    assert res_claim["success"] is True
    assert res_claim["handoff_id"] == "h_task_01"
    assert res_claim["status"] == "IN_PROGRESS"
    assert res_claim["claimed_by"] == "agent_worker_1"
    assert res_claim["winner"] == "agent_worker_1"
    assert isinstance(res_claim["lease_until"], int)


def test_conformance_retrieval_and_as_of():
    """Verify retrieve and as_of point-in-time belief recall."""
    g = reset_graph()
    vec = [0.1] * 384
    t_past = int(time.time() * 1000) - 10000
    g.query(
        "CREATE (f:Fact:MemoryNode {id: 'f_past', status: 'HEALTHY', text: 'Historical belief', "
        "embedding: vecf32($vec), valid_from: $t, stale: false})",
        {"vec": vec, "t": t_past}
    )

    # retrieve
    retrieved = queries.retrieve(TENANT, vec, k=5)
    assert len(retrieved) >= 1
    assert retrieved[0]["id"] == "f_past"
    assert retrieved[0]["status"] == "HEALTHY"

    # as_of
    as_of_res = queries.as_of(TENANT, int(time.time() * 1000))
    assert any(n["id"] == "f_past" for n in as_of_res)


def test_conformance_brain_provenance_and_who_knows():
    """Verify provenance, who_knows, and brain_ask conform to CONTRACT.md DecisionTrace, WhoKnows, and BrainAnswer schemas."""
    g = reset_graph()
    g.query(
        "CREATE (team:Team {id: 'team_support', name: 'Customer Support'}) "
        "CREATE (person:Person {id: 'pers_alice', name: 'Alice Smith'}) "
        "CREATE (person)-[:MEMBER_OF]->(team) "
        "CREATE (topic:Topic {id: 'top_returns', name: 'Returns Policy'}) "
        "CREATE (src:Source {id: 'src_doc_1', title: 'Returns Guide v2', url: 'https://acme.test/returns', version: '2.0', kind: 'DOC'}) "
        "CREATE (team)-[:OWNS]->(src) "
        "CREATE (person)-[:AUTHORED]->(src) "
        "CREATE (src)-[:ABOUT]->(topic) "
        "CREATE (f:Fact:MemoryNode {id: 'f_prov', text: 'Customers can return items within 30 days', quote: 'Within 30 days', status: 'HEALTHY', valid_from: timestamp(), stale: false}) "
        "CREATE (f)-[:FROM_SOURCE]->(src) "
        "CREATE (dec:Decision:Conclusion:MemoryNode {id: 'dec_refund_policy', text: 'Approve refund for valid window', status: 'HEALTHY', stale: false}) "
        "CREATE (f)-[:LEADS_TO]->(dec)"
    )

    # 1. provenance (conforms to DecisionTrace schema)
    trace = queries.provenance(TENANT, "dec_refund_policy")
    assert len(trace) >= 1
    item = trace[0]
    assert item["claim"] == "Customers can return items within 30 days"
    assert item["quote"] == "Within 30 days"
    assert item["source_title"] == "Returns Guide v2"
    assert item["source"] == "Returns Guide v2"
    assert item["url"] == "https://acme.test/returns"
    assert item["version"] == "2.0"
    assert item["owner"] == "Customer Support"

    # 2. who_knows (conforms to WhoKnows schema)
    who = queries.who_knows(TENANT, "top_returns")
    assert len(who) >= 1
    w_item = who[0]
    assert w_item["person"] == "Alice Smith"
    assert w_item["team"] == "Customer Support"
    assert w_item["evidence_count"] >= 1
    assert "src_doc_1" in w_item["source_ids"]

    # 3. brain_ask (conforms to BrainAskResponse schema)
    ask_res = queries.brain_ask(TENANT, "What is the return window?")
    assert ask_res["grounded"] is True
    assert ask_res["refused"] is False
    assert len(ask_res["citations"]) >= 1
    cit = ask_res["citations"][0]
    assert cit["source_title"] == "Returns Guide v2"
    assert cit["url"] == "https://acme.test/returns"
    assert cit["version"] == "2.0"
    assert cit["owner_team"] == "Customer Support"
    assert cit["stale"] is False


def test_conformance_blast_preview_invariants_and_snapshot(events_schema):
    """Verify blast_preview, invariant_check, and graph_snapshot match CONTRACT.md and JSON Schema."""
    g = reset_graph()
    g.query("CREATE (f:Fact:MemoryNode {id: 'f_snap_root', status: 'HEALTHY', text: 'Snapshot Root'})")
    queries.derive(TENANT, "c_snap", ["f_snap_root"], "Snapshot Conc")
    queries.stage_action(TENANT, "act_snap_staged", ["c_snap"], "REFUND", {}, "REVERSIBLE", gate="PASSED")
    queries.stage_action(TENANT, "act_snap_exec", ["c_snap"], "CANCEL", {}, "COMPENSABLE", gate="PASSED")
    queries.execute_guard(TENANT, "act_snap_exec")

    # 1. blast_preview (conforms to BlastPreview schema)
    preview = queries.blast_preview(TENANT, "f_snap_root")
    assert preview["root_id"] == "f_snap_root"
    assert isinstance(preview["node_ids"], list)
    assert preview["facts"] == 1
    assert preview["conclusions"] == 1
    assert "actions" in preview
    assert preview["actions"]["staged"] == 1
    assert preview["actions"]["executed_compensable"] == 1
    assert preview["actions"]["executed_reversible"] == 0

    # 2. invariant_check (conforms to Invariants schema)
    inv = queries.invariant_check(TENANT)
    assert inv["ok"] is True
    assert inv["all_passed"] is True
    assert isinstance(inv["checked_at"], int)
    assert isinstance(inv["checks"], list)
    assert len(inv["checks"]) >= 2
    for check in inv["checks"]:
        assert "name" in check
        assert "ok" in check
        assert "value" in check

    # 3. graph_snapshot (conforms to GraphResponse schema)
    snap = queries.graph_snapshot(TENANT)
    assert snap["contract_version"] == "0.1.0"
    assert snap["tenant"] == TENANT
    assert isinstance(snap["epoch"], int)
    assert isinstance(snap["seq"], int)
    assert isinstance(snap["nodes"], list)
    assert isinstance(snap["edges"], list)

    # Validate node and edge objects against events.schema.json $defs
    node_schema = dict(events_schema["$defs"]["GraphNode"])
    node_schema["$defs"] = events_schema["$defs"]
    edge_schema = dict(events_schema["$defs"]["GraphEdge"])
    edge_schema["$defs"] = events_schema["$defs"]
    validator_node = jsonschema.Draft202012Validator(node_schema)
    validator_edge = jsonschema.Draft202012Validator(edge_schema)

    for node in snap["nodes"]:
        validator_node.validate(node)

    for edge in snap["edges"]:
        validator_edge.validate(edge)


def test_conformance_memory_and_procedures():
    """Verify episodic, procedural, and history functions."""
    g = reset_graph()
    agent_id = "agent_scout"
    session_id = "sess_001"

    # 1. record_episode
    rec = queries.record_episode(TENANT, agent_id, session_id, "msg_01", "user", "Hello support")
    assert rec["success"] is True
    rec2 = queries.record_episode(TENANT, agent_id, session_id, "msg_02", "assistant", "How can I help?")
    assert rec2["success"] is True

    # 2. session_history
    hist = queries.session_history(TENANT, agent_id, session_id)
    assert len(hist) == 2
    assert hist[0]["content"] == "Hello support"
    assert hist[1]["content"] == "How can I help?"

    # 3. close_session
    closed = queries.close_session(TENANT, agent_id, session_id)
    assert closed["success"] is True
    assert closed["msg_count"] == 2

    # 4. resume_agent (conforms to Resume schema)
    res_agent = queries.resume_agent(TENANT, agent_id)
    assert res_agent["resumed"] is True
    assert res_agent["session_id"] == session_id
    assert "summary" in res_agent
    assert isinstance(res_agent["memories"], list)

    # 5. upsert_procedure, score_procedure, rank_procedures
    up_proc = queries.upsert_procedure(TENANT, "proc_refund_flow", "Refund Procedure", "Steps to refund", ["check", "refund"])
    assert up_proc["id"] == "proc_refund_flow"
    assert "rank_score" in up_proc

    scored = queries.score_procedure(TENANT, "proc_refund_flow", "success")
    assert scored["success"] is True
    assert scored["uses"] == 1
    assert scored["successes"] == 1
    assert "rank_score" in scored

    ranked = queries.rank_procedures(TENANT)
    assert len(ranked) >= 1
    assert ranked[0]["id"] == "proc_refund_flow"
    assert "rank_score" in ranked[0]
    assert "task_type" in ranked[0]
    assert "seeded" in ranked[0]


# =========================================================================
# 3. ERROR HANDLING & ROBUSTNESS (NO FALKORDB CRASHES ON INVALID INPUTS)
# =========================================================================

def test_error_handling_invalid_parameters():
    """Verify that all functions return structured error dicts or clean handling on invalid/boundary inputs."""
    reset_graph()

    # 1. stage_action with missing parent
    res_bad_stage = queries.stage_action(TENANT, "act_bad", ["nonexistent_parent"], "REFUND", {}, "COMPENSABLE")
    assert res_bad_stage["success"] is False
    assert "error" in res_bad_stage

    # 2. derive with empty parent_ids
    res_bad_derive = queries.derive(TENANT, "conc_bad", [], "No parents")
    assert res_bad_derive["success"] is False
    assert "error" in res_bad_derive

    # 3. execute_guard on non-existent action
    res_bad_exec = queries.execute_guard(TENANT, "nonexistent_action")
    assert res_bad_exec["success"] is False
    assert "reason" in res_bad_exec

    # 4. confirm_action on non-existent action
    res_bad_confirm = queries.confirm_action(TENANT, "nonexistent_action")
    assert res_bad_confirm is False

    # 5. cascade on non-existent root
    res_bad_casc = queries.cascade(TENANT, "nonexistent_root")
    assert res_bad_casc["success"] is False
    assert res_bad_casc["affected_count"] == 0

    # 6. restore on non-quarantined / missing root
    res_bad_rest = queries.restore(TENANT, "nonexistent_root")
    assert res_bad_rest["success"] is False
    assert "error" in res_bad_rest

    # 7. claim_handoff on non-existent handoff
    res_bad_claim = queries.claim_handoff(TENANT, "nonexistent_handoff", "agent_1")
    assert res_bad_claim["success"] is False
    assert "error" in res_bad_claim

    # 8. supersede on non-existent old fact
    res_bad_sup = queries.supersede(TENANT, "nonexistent_fact", "new_f", "val", "txt", [0.1] * 384)
    assert res_bad_sup["success"] is False
    assert "error" in res_bad_sup

    # 9. supersede_with_review on non-existent key
    res_bad_sup_rev = queries.supersede_with_review(TENANT, "nonexistent.key", "new_f2", "txt", "val")
    assert res_bad_sup_rev["success"] is False
    assert "error" in res_bad_sup_rev

    # 10. score_procedure on non-existent procedure
    res_bad_score = queries.score_procedure(TENANT, "nonexistent_proc", "success")
    assert res_bad_score["success"] is False
    assert "error" in res_bad_score

    # 11. brain_ask on empty query / ungrounded topic -> returns structured refusal, NOT crash
    res_refusal = queries.brain_ask(TENANT, "xyznonexistenttopic999")
    assert res_refusal["refused"] is True
    assert res_refusal["grounded"] is False
    assert "reason" in res_refusal
    assert res_refusal["answer"] is None
    assert res_refusal["citations"] == []

    # 12. provenance on non-existent decision -> returns empty list, no crash
    res_empty_prov = queries.provenance(TENANT, "nonexistent_decision")
    assert res_empty_prov == []

    # 13. who_knows on non-existent topic -> returns empty list, no crash
    res_empty_who = queries.who_knows(TENANT, "nonexistent_topic")
    assert res_empty_who == []


# =========================================================================
# 4. STATUS ENUMS & RETURN KEYS STRICT VERIFICATION
# =========================================================================

def test_status_enums_and_keys_adherence():
    """Verify that statuses and enums across all graph states adhere strictly to the contract."""
    g = reset_graph()
    g.query("CREATE (f:Fact:MemoryNode {id: 'f_enum_test', status: 'HEALTHY', stale: false})")

    # Verify HEALTHY status
    node_res = g.query("MATCH (f:Fact {id: 'f_enum_test'}) RETURN f.status, f.stale").result_set[0]
    assert node_res[0] == "HEALTHY"
    assert node_res[1] is False

    # Quarantine -> QUARANTINED status
    queries.cascade(TENANT, "f_enum_test")
    node_res_q = g.query("MATCH (f:Fact {id: 'f_enum_test'}) RETURN f.status").result_set[0]
    assert node_res_q[0] == "QUARANTINED"

    # Restore -> HEALTHY status
    queries.restore(TENANT, "f_enum_test")
    node_res_r = g.query("MATCH (f:Fact {id: 'f_enum_test'}) RETURN f.status").result_set[0]
    assert node_res_r[0] == "HEALTHY"

    # Supersede -> SUPERSEDED status
    queries.supersede(TENANT, "f_enum_test", "f_enum_new", "v", "t", [0.1] * 384)
    node_res_s = g.query("MATCH (f:Fact {id: 'f_enum_test'}) RETURN f.status").result_set[0]
    assert node_res_s[0] == "SUPERSEDED"

    # Compensation -> PENDING_COMPENSATION status
    g.query("CREATE (a:Action:MemoryNode {id: 'act_enum_test', reversibility: 'COMPENSABLE', executed: true, idempotency_key: 'idem_enum'})")
    comps = queries.create_compensations(TENANT, [{"action_id": "act_enum_test"}])
    assert comps[0]["status"] == "PENDING_COMPENSATION"

    # Handoff -> PENDING, IN_PROGRESS, COMPLETED
    g.query("CREATE (h:Handoff {id: 'h_enum_test', kind: 'TASK', status: 'PENDING'})")
    h_stat_1 = g.query("MATCH (h:Handoff {id: 'h_enum_test'}) RETURN h.status").result_set[0][0]
    assert h_stat_1 == "PENDING"

    claim = queries.claim_handoff(TENANT, "h_enum_test", "agent_1")
    assert claim["status"] == "IN_PROGRESS"

    g.query("MATCH (h:Handoff {id: 'h_enum_test'}) SET h.status = 'COMPLETED'")
    h_stat_3 = g.query("MATCH (h:Handoff {id: 'h_enum_test'}) RETURN h.status").result_set[0][0]
    assert h_stat_3 == "COMPLETED"
