import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.graph.queries import get_graph_store
from app.models.common import Status, CompensationStatus, Reversibility


@pytest.fixture
def client():
    return TestClient(app)


def test_scenario_step_rbac(client):
    """
    POST /api/scenario/{step} requires auditor or human role.
    Agent role must receive 403 FORBIDDEN_ROLE.
    """
    res = client.post(
        "/api/scenario/reset",
        headers={"Authorization": "Bearer token_agent_scout"},
    )
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "FORBIDDEN_ROLE"


def test_full_scenario_orchestration_flow(client):
    """
    Full end-to-end integration test of the 90-second demo scenario:
    1. reset
    2. inject_poison
    3. run_agents
    4. audit
    5. supersede
    6. restore
    """
    headers = {"Authorization": "Bearer token_auditor"}

    # 1. RESET
    res_reset = client.post("/api/scenario/reset", headers=headers)
    assert res_reset.status_code == 200
    assert res_reset.json()["accepted"] is True

    store = get_graph_store("acme")
    assert "fact_30d" in store.nodes
    assert store.nodes["fact_30d"].status == Status.HEALTHY
    assert store.nodes["fact_30d"].value == "30"

    # 2. INJECT POISON
    res_inject = client.post("/api/scenario/inject_poison", headers=headers)
    assert res_inject.status_code == 200

    assert "fact_90d" in store.nodes
    fact_90d = store.nodes["fact_90d"]
    assert fact_90d.status == Status.HEALTHY
    assert fact_90d.value == "90"
    assert fact_90d.origin == "webhook:partner-feed"

    # 3. RUN AGENTS
    res_agents = client.post("/api/scenario/run_agents", headers=headers)
    assert res_agents.status_code == 200

    # Analyst derived conclusion
    assert "conc_eligible_1042" in store.nodes
    conc = store.nodes["conc_eligible_1042"]
    assert conc.status == Status.HEALTHY
    assert conc.is_decision is True

    # Refunder staged 3 and executed 2
    act1 = store.nodes.get("act_refund_1")
    act2 = store.nodes.get("act_refund_2")
    act3 = store.nodes.get("act_refund_3")
    assert act1.action.executed is True and act1.action.confirmed is True
    assert act2.action.executed is True and act2.action.confirmed is True
    assert act3.action.executed is False

    # Comms action was executed and irreversible before audit
    email = store.nodes.get("act_email_cust")
    assert email is not None
    assert email.action.executed is True
    assert email.action.reversibility == Reversibility.IRREVERSIBLE

    # 4. AUDIT
    res_audit = client.post("/api/scenario/audit", headers=headers)
    assert res_audit.status_code == 200

    # Cascade quarantine hit fact_90d and its descendants
    assert store.nodes["fact_90d"].status == Status.QUARANTINED
    assert store.nodes["conc_eligible_1042"].status == Status.QUARANTINED
    assert act1.status == Status.QUARANTINED
    assert act2.status == Status.QUARANTINED
    assert act3.status == Status.QUARANTINED
    assert email.status == Status.QUARANTINED

    # Executed irreversible email action generated automated damage control retraction notice
    assert email.action.correction_notice is not None
    assert email.action.correction_notice["status"] == "NEEDS_HUMAN_INPUT"
    assert email.action.correction_notice["recipient"] is None
    assert "recipient" in email.action.correction_notice["missing_information"]
    assert email.action.correction_notice["correction_subject"].startswith("Correction:")
    assert any(p["id"] == "fact_30d" for p in email.action.correction_notice["provenance"])

    # Verify ESCALATION handoff was created with correction notice payload
    handoffs_res = client.get("/api/handoffs", headers=headers)
    assert handoffs_res.status_code == 200
    handoffs = handoffs_res.json()
    email_handoff = next((h for h in handoffs if h.get("context_root_id") == "act_email_cust" or (h.get("correction_notice") and h["correction_notice"].get("target_action_id") == "act_email_cust")), None)
    assert email_handoff is not None
    assert email_handoff["kind"] == "ESCALATION"
    assert email_handoff["correction_notice"] is not None
    assert email_handoff["correction_notice"]["target_action_id"] == "act_email_cust"

    # Hop depths correctly computed
    assert store.nodes["fact_90d"].depth == 0
    assert store.nodes["conc_eligible_1042"].depth == 1
    assert act1.depth == 2

    # Executed actions were compensated by Outbox Saga Worker
    assert act1.action.compensation.status == CompensationStatus.COMPENSATED
    assert act2.action.compensation.status == CompensationStatus.COMPENSATED

    # Staged unexecuted action act3 remains unexecuted (execute guard refused)
    assert act3.action.executed is False

    # Invariants stay green
    invariants = client.get("/api/invariants", headers=headers).json()
    assert invariants["ok"] is True
    for check in invariants["checks"]:
        assert check["ok"] is True

    # 5. SUPERSEDE
    res_super = client.post("/api/scenario/supersede", headers=headers)
    assert res_super.status_code == 200

    assert "fact_45d_v4" in store.nodes
    fact_45d = store.nodes["fact_45d_v4"]
    assert fact_45d.status == Status.HEALTHY
    assert fact_45d.value == "45"

    # Old fact marked SUPERSEDED
    assert store.nodes["fact_30d"].status == Status.SUPERSEDED

    # 6. RESTORE
    res_restore = client.post("/api/scenario/restore", headers=headers)
    assert res_restore.status_code == 200


def test_brain_endpoints(client):
    """
    Tests Company Brain (Track 03):
    - /api/brain/ask returns citations for grounded knowledge
    - /api/brain/ask refuses untraceable questions
    - /api/who-knows returns expert leaderboard
    - /api/decisions/{id}/trace returns provenance chain
    """
    headers = {"Authorization": "Bearer token_agent_scout"}

    # Seed clean state
    client.post("/api/scenario/reset", headers={"Authorization": "Bearer token_auditor"})

    # 1. Grounded query
    res_ask = client.post(
        "/api/brain/ask",
        headers=headers,
        json={"question": "What is the policy for refunds?"},
    )
    assert res_ask.status_code == 200
    data = res_ask.json()
    assert data["refused"] is False
    assert "30 days" in data["answer"]
    assert len(data["citations"]) >= 1
    cit = data["citations"][0]
    assert cit["source_title"] == "Refund policy"
    assert cit["version"] == "3"
    assert cit["owner_team"] == "Support Ops"

    # 2. Untraceable query -> strictly refused
    res_untraced = client.post(
        "/api/brain/ask",
        headers=headers,
        json={"question": "What is the secret recipe for cafeteria soup?"},
    )
    assert res_untraced.status_code == 200
    assert res_untraced.json()["refused"] is True
    assert res_untraced.json()["reason"] == "no sourced answer found"
    assert res_untraced.json()["citations"] == []

    # 3. Who-Knows Leaderboard
    res_who = client.get("/api/who-knows?topic=refunds", headers=headers)
    assert res_who.status_code == 200
    who_list = res_who.json()
    assert len(who_list) >= 2
    assert any(w["person"] == "Sarah Chen" for w in who_list)

    # 4. Decision Trace
    # Missing IDs used to return a fabricated canonical trace. Only real decisions are traceable.
    assert client.get("/api/decisions/conc_eligible_1042/trace", headers=headers).status_code == 404
    from app.services.mcp_tools import derive
    assert derive("A sourced decision", ["fact_30d"], token="token_agent_scout", id="conc_eligible_1042", is_decision=True)["success"]
    res_trace = client.get("/api/decisions/conc_eligible_1042/trace", headers=headers)
    assert res_trace.status_code == 200
    trace_items = res_trace.json()
    assert len(trace_items) >= 1
    assert trace_items[0]["owner"] == "Support Ops"


def test_prompt_diff_and_procedures(client):
    """
    Tests prompt-diff context filtering and adaptive procedural memory.
    """
    headers = {"Authorization": "Bearer token_auditor"}

    # Run up to audit so quarantined nodes exist
    client.post("/api/scenario/reset", headers=headers)
    client.post("/api/scenario/inject_poison", headers=headers)
    client.post("/api/scenario/run_agents", headers=headers)
    client.post("/api/scenario/audit", headers=headers)

    # Check prompt diff
    res_diff = client.get("/api/prompt-diff", headers=headers)
    assert res_diff.status_code == 200
    diff_data = res_diff.json()
    assert "fact_90d" in diff_data["removed_ids"]
    assert "conc_eligible_1042" in diff_data["removed_ids"]

    # Check procedure list and ranking
    res_proc = client.get("/api/procedures", headers=headers)
    assert res_proc.status_code == 200
    procs = res_proc.json()
    assert len(procs) >= 3
    # Top ranked refund procedure after rollback failure is strict procedure
    refund_procs = [p for p in procs if p["task_type"] == "refund"]
    assert refund_procs[0]["id"] == "proc_refund_strict"


def test_cypher_log_and_denied_demo(client):
    """
    Tests cypher query observability and cross-tenant ACL denial demo.
    """
    headers = {"Authorization": "Bearer token_auditor"}

    # Cypher log (a write first, so there is something to log)
    assert client.post("/api/scenario/reset", headers=headers).status_code == 200
    res_log = client.get("/api/cypher-log", headers=headers)
    assert res_log.status_code == 200
    log_items = res_log.json()
    assert len(log_items) > 0
    assert "cypher" in log_items[0]
    assert "ms" in log_items[0]
    assert "rows" in log_items[0]

    # Cross-tenant denied demo
    res_denied = client.get("/api/tenants/denied-demo", headers=headers)
    assert res_denied.status_code == 200
    denied_data = res_denied.json()
    assert denied_data["result"] == "DENIED"
    # A live check: the acme token asks for globex's graph and is refused. Nothing here is canned.
    assert denied_data["tenant"] == "acme"
    assert denied_data["attempted_graph"] == "quorum:globex"
    assert "FORBIDDEN_TENANT" in denied_data["detail"]
    assert "quorum:globex" not in denied_data["visible_graph_names"]
