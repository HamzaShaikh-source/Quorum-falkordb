"""
Comprehensive QA and Stress Test Suite for Auditor Service, Contradiction Detection, and CSV Export
Covers:
1. Contradiction Detection (Tier weights, 3x ratio, auto-quarantine, tie-break review handoff)
2. Invalidation Cascade (Reversible compensations, Irreversible damage control alerts, epoch bump)
3. CSV Export and Audit API (Formula injection sanitization, token tracking, severity filtering, POST /api/audit/events)
4. Multi-Vector Stress & Boundary Tests:
   - Contradiction storm: 50 conflicting facts asserted in parallel with varying tiers
   - Malicious CSV export payload containing Excel formula command injection
"""
import concurrent.futures
import csv
import io
import time
import pytest
from starlette.testclient import TestClient

from app.config import settings
from app.graph.queries import GraphStore, get_graph_store
from app.main import app
from app.models.common import (
    CompensationStatus,
    EdgeType,
    GateStatus,
    HandoffKind,
    HandoffStatus,
    NodeKind,
    Reversibility,
    Status,
    Tier,
)
from app.models.graph import ActionInfo, AssertedBy, GraphEdge, GraphNode
from app.models.handoffs import Handoff
from app.services.auditor import auditor_service
from app.services.event_bus import event_bus
from app.services.mcp_guard import record_event


@pytest.fixture
def test_store():
    tenant = f"test_audit_tenant_{int(time.time() * 1000)}"
    store = get_graph_store(tenant)
    return store


# ============================================================================
# 1. CONTRADICTION DETECTION TESTS
# ============================================================================

def test_contradiction_detection_tier_weights_and_ratios(test_store):
    """
    Test all 9 pairwise combinations of tiers:
    Weights: ROOT: 10.0, ESTABLISHED: 3.0, NEW: 0.5.
    Rule 1: ROOT vs Non-ROOT auto-quarantines non-ROOT challenger or old fact.
    Rule 2: >= 3x weight auto-quarantines weaker; < 3x triggers REVIEW handoff.
    """
    scenarios = [
        # (old_tier, new_tier, expected_verdict, old_quarantined, new_quarantined, handoff_created)
        (Tier.ROOT, Tier.NEW, "CHALLENGER_QUARANTINED", False, True, False),
        (Tier.ROOT, Tier.ESTABLISHED, "CHALLENGER_QUARANTINED", False, True, False),
        (Tier.ROOT, Tier.ROOT, "REVIEW_HANDOFF", False, False, True),  # 10 vs 10 (1x < 3x)
        (Tier.ESTABLISHED, Tier.ROOT, "OLD_FACT_QUARANTINED", True, False, False),
        (Tier.ESTABLISHED, Tier.ESTABLISHED, "REVIEW_HANDOFF", False, False, True),  # 3 vs 3 (1x < 3x)
        (Tier.ESTABLISHED, Tier.NEW, "CHALLENGER_QUARANTINED", False, True, False),  # 3 vs 0.5 (6x >= 3x)
        (Tier.NEW, Tier.ROOT, "OLD_FACT_QUARANTINED", True, False, False),
        (Tier.NEW, Tier.ESTABLISHED, "OLD_FACT_QUARANTINED", True, False, False),  # 3 vs 0.5 (6x >= 3x)
        (Tier.NEW, Tier.NEW, "REVIEW_HANDOFF", False, False, True),  # 0.5 vs 0.5 (1x < 3x)
    ]

    for idx, (old_t, new_t, exp_verdict, exp_old_q, exp_new_q, exp_handoff) in enumerate(scenarios):
        # Setup clean store for each scenario
        sub_store = GraphStore(f"tenant_sub_{idx}")
        f_old = GraphNode(
            id=f"f_old_{idx}",
            kind=NodeKind.FACT,
            label=f"Old fact {idx}",
            key="refund_window_days",
            value="30",
            status=Status.HEALTHY,
            asserted_by=AssertedBy(id=f"agent_old_{idx}", name="Agent Old", tier=old_t),
        )
        f_new = GraphNode(
            id=f"f_new_{idx}",
            kind=NodeKind.FACT,
            label=f"New fact {idx}",
            key="refund_window_days",
            value="90",
            status=Status.HEALTHY,
            asserted_by=AssertedBy(id=f"agent_new_{idx}", name="Agent New", tier=new_t),
        )
        sub_store.add_node(f_old)
        sub_store.add_node(f_new)

        res = auditor_service.check_contradiction(sub_store, f_new, tenant=sub_store.tenant)
        assert res is not None, f"Scenario {idx} ({old_t} vs {new_t}) returned None"
        assert res["verdict"] == exp_verdict, f"Scenario {idx} ({old_t} vs {new_t}) expected {exp_verdict}, got {res['verdict']}"

        assert (f_old.status == Status.QUARANTINED) == exp_old_q, f"Scenario {idx} f_old quarantine mismatch"
        assert (f_new.status == Status.QUARANTINED) == exp_new_q, f"Scenario {idx} f_new quarantine mismatch"

        if exp_handoff:
            assert len(sub_store.handoffs) > 0
            assert sub_store.handoffs[-1].kind == HandoffKind.REVIEW


def test_contradiction_same_value_no_conflict(test_store):
    """When two facts have identical key AND identical value, no contradiction should be raised."""
    f1 = GraphNode(
        id="f_same_1",
        kind=NodeKind.FACT,
        label="Fact 1",
        key="currency",
        value="USD",
        status=Status.HEALTHY,
        asserted_by=AssertedBy(id="a1", name="Agent 1", tier=Tier.ROOT),
    )
    f2 = GraphNode(
        id="f_same_2",
        kind=NodeKind.FACT,
        label="Fact 2",
        key="currency",
        value="USD",
        status=Status.HEALTHY,
        asserted_by=AssertedBy(id="a2", name="Agent 2", tier=Tier.NEW),
    )
    test_store.add_node(f1)
    test_store.add_node(f2)

    res = auditor_service.check_contradiction(test_store, f2, tenant=test_store.tenant)
    assert res is None


# ============================================================================
# 2. INVALIDATION CASCADE & COMPENSATION TESTS
# ============================================================================

def test_invalidation_cascade_reversible_compensation(test_store):
    """
    Quarantine cascade on a fact with downstream executed REVERSIBLE action:
    - Enqueues compensation with CompensationStatus.PENDING_COMPENSATION.
    - Bumps store.epoch.
    - Changes statuses to QUARANTINED.
    """
    initial_epoch = test_store.epoch

    f_root = GraphNode(
        id="f_bad_policy",
        kind=NodeKind.FACT,
        label="Bad policy fact",
        status=Status.HEALTHY,
        key="discount_rate",
        value="50",
    )
    c_derived = GraphNode(
        id="c_discount_decision",
        kind=NodeKind.CONCLUSION,
        label="Approved 50% discount",
        status=Status.HEALTHY,
    )
    a_exec = GraphNode(
        id="a_apply_discount",
        kind=NodeKind.ACTION,
        label="Apply 50% discount to invoice",
        status=Status.HEALTHY,
        action=ActionInfo(
            type="apply_discount",
            reversibility=Reversibility.REVERSIBLE,
            executed=True,
            gate=GateStatus.PASSED,
        ),
    )

    test_store.add_node(f_root)
    test_store.add_node(c_derived)
    test_store.add_node(a_exec)

    test_store.add_edge(GraphEdge(id="e1", source="f_bad_policy", target="c_discount_decision", type=EdgeType.LEADS_TO))
    test_store.add_edge(GraphEdge(id="e2", source="c_discount_decision", target="a_apply_discount", type=EdgeType.LEADS_TO))

    # Trigger quarantine via auditor
    affected = auditor_service.quarantine(test_store, "f_bad_policy", tenant=test_store.tenant)

    # Invalidation checks
    assert len(affected) == 3
    assert f_root.status == Status.QUARANTINED
    assert c_derived.status == Status.QUARANTINED
    assert a_exec.status == Status.QUARANTINED
    assert test_store.epoch > initial_epoch

    # Reversible compensation enqueued
    assert a_exec.action.compensation is not None
    assert a_exec.action.compensation.status == CompensationStatus.PENDING_COMPENSATION
    assert a_exec.action.compensation.attempts == 0


def test_invalidation_cascade_irreversible_damage_control(test_store):
    """
    Quarantine cascade on a fact with downstream executed IRREVERSIBLE action:
    - Generates CorrectionNotice.
    - Stores alert node (NodeKind.ALERT) with retraction text.
    - Creates ESCALATION handoff for human approval.
    """
    f_root = GraphNode(
        id="f_poison_payout",
        kind=NodeKind.FACT,
        label="Payout amount: $5000",
        status=Status.HEALTHY,
        key="payout_limit",
        value="5000",
    )
    a_wire = GraphNode(
        id="a_wire_transfer",
        kind=NodeKind.ACTION,
        label="Send wire transfer of $5000",
        status=Status.HEALTHY,
        action=ActionInfo(
            type="wire_transfer",
            reversibility=Reversibility.IRREVERSIBLE,
            executed=True,
            gate=GateStatus.PASSED,
            payload={"recipient": "cust_999", "amount": 5000, "subject": "Wire Transfer Confirmation"},
        ),
    )

    test_store.add_node(f_root)
    test_store.add_node(a_wire)
    test_store.add_edge(GraphEdge(id="e_wire", source="f_poison_payout", target="a_wire_transfer", type=EdgeType.LEADS_TO))

    affected = auditor_service.quarantine(test_store, "f_poison_payout", tenant=test_store.tenant)

    assert a_wire.status == Status.QUARANTINED
    assert a_wire.action.correction_notice is not None

    alert_id = f"alert_retraction_{a_wire.id}"
    assert alert_id in test_store.nodes
    alert_node = test_store.nodes[alert_id]
    assert alert_node.kind == NodeKind.ALERT
    assert "Retraction" in alert_node.label

    # Edge from alert to action
    assert any(e.source == alert_id and e.target == a_wire.id for e in test_store.edges)

    # Escalation handoff
    escl_handoffs = [h for h in test_store.handoffs if h.kind == HandoffKind.ESCALATION]
    assert len(escl_handoffs) > 0
    assert escl_handoffs[-1].context_root_id == a_wire.id
    assert escl_handoffs[-1].correction_notice is not None


# ============================================================================
# 3. CSV EXPORT, FORMULA INJECTION & AUDIT ENDPOINTS
# ============================================================================

def test_post_api_audit_events_endpoint(monkeypatch):
    """
    Verify POST /api/audit/events:
    The prompt specifically instructs to check POST /api/audit/events.
    Test whether the route exists, what it returns, and check GET /api/audit.
    """
    monkeypatch.setattr(settings, "ENV", "development")
    client = TestClient(app)
    tok = "token_acme_human"

    # Test POST /api/audit/events
    r_post = client.post("/api/audit/events", json={"event": "test"}, headers={"Authorization": f"Bearer {tok}"})
    # Since POST /api/audit/events does not exist in FastAPI app routes, it should return 404
    assert r_post.status_code == 404, f"Expected 404 for non-existent POST /api/audit/events, got {r_post.status_code}"

    # Test GET /api/audit (the actual audit trail endpoint)
    r_get = client.get("/api/audit", headers={"Authorization": f"Bearer {tok}"})
    assert r_get.status_code == 200
    data = r_get.json()
    assert "events" in data
    assert "summary" in data


def test_audit_csv_export_formula_injection_sanitization(monkeypatch):
    """
    Test CSV export formula injection sanitization against leading:
    = (Formula)
    + (Addition / unary plus)
    - (Subtraction / unary minus)
    @ (Excel function call)
    | (Pipe command)
    % (Percent operator)
    Tab / newline / carriage return combinations
    """
    monkeypatch.setattr(settings, "ENV", "development")
    client = TestClient(app)
    tok = "token_acme_human"

    # Add malicious calls directly into store to test CSV export sanitization
    store = get_graph_store("acme")
    malicious_payloads = [
        "=cmd|' /C calc'!A0",
        "+1+1",
        "-5+10",
        "@SUM(A1:A10)",
        "|ping -n 1 127.0.0.1",
        "%COMSPEC%",
        "\t=1+1",
        "\n=cmd|' /C calc'!A0",
        "normal text without injection",
        "=HYPERLINK(\"http://evil.com\", \"Click Here\")",
    ]

    for idx, payload in enumerate(malicious_payloads):
        store.record_call({
            "tool": f"tool_test_{idx}",
            "agent_id": f"agent_{idx}",
            "member_id": f"mem_{idx}",
            "role": "agent",
            "summary": payload,
            "scope_teams": [],
            "tokens": 42,
            "baseline": 100,
            "ms": 1.5,
            "ok": True,
            "error": None,
        })

    # Fetch CSV export
    r = client.get("/api/audit?format=csv", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    csv_content = r.text

    # Parse rows using standard csv reader
    reader = csv.reader(io.StringIO(csv_content))
    header = next(reader)
    assert header == ["time_utc", "who", "team", "client", "tool", "what", "severity", "ok", "tokens", "tokens_saved"]

    rows = list(reader)
    # Check that any cell starting with dangerous characters is escaped with a leading single quote (')
    dangerous_starts = ("=", "+", "-", "@", "|", "%")
    for row in rows:
        what_cell = row[5]  # 'what' column corresponds to text / summary
        if any(payload in what_cell for payload in ["=cmd", "+1+1", "-5+10", "@SUM", "|ping", "%COMSPEC%", "=HYPERLINK"]):
            # Must NOT start directly with unescaped formula characters
            # Either it starts with "'" or with a verb like 'tool_test_0 “'=cmd...'”'
            assert not what_cell.startswith(dangerous_starts), f"Cell directly starts with unescaped formula: {what_cell}"
            # In quoted sentences: verb “'=cmd...”
            if "“" in what_cell:
                inner = what_cell.split("“")[1].split("”")[0]
                assert inner.startswith("'") or not inner.startswith(dangerous_starts), f"Inner quoted payload not sanitized: {inner}"


def test_audit_token_tracking_and_severity_filters(monkeypatch):
    """
    Test token tracking and severity filters in /api/audit:
    - severity=read
    - severity=write
    - severity=alert
    - severity=failed
    - token calculation and savings
    """
    monkeypatch.setattr(settings, "ENV", "development")
    client = TestClient(app)
    tok = "token_acme_human"
    store = get_graph_store("acme")

    # Record distinct calls
    store.record_call({
        "tool": "retrieve_context",
        "agent_id": "scout",
        "member_id": "mem_scout",
        "role": "agent",
        "summary": "read policy",
        "scope_teams": [],
        "tokens": 50,
        "baseline": 200,
        "ms": 1.0,
        "ok": True,
        "error": None,
    })
    store.record_call({
        "tool": "remember_fact",
        "agent_id": "writer",
        "member_id": "mem_writer",
        "role": "agent",
        "summary": "new fact",
        "scope_teams": [],
        "tokens": 20,
        "baseline": 0,
        "ms": 1.0,
        "ok": True,
        "error": None,
    })
    store.record_call({
        "tool": "quarantine",
        "agent_id": "auditor",
        "member_id": "mem_auditor",
        "role": "auditor",
        "summary": "quarantine bad fact",
        "scope_teams": [],
        "tokens": 10,
        "baseline": 0,
        "ms": 1.0,
        "ok": True,
        "error": None,
    })
    store.record_call({
        "tool": "execute_action",
        "agent_id": "refunder",
        "member_id": "mem_refunder",
        "role": "agent",
        "summary": "failed execution",
        "scope_teams": [],
        "tokens": 15,
        "baseline": 0,
        "ms": 1.0,
        "ok": False,
        "error": "execute guard refused",
    })

    # Test severity filters
    for sev in ("read", "write", "alert", "failed"):
        r = client.get(f"/api/audit?severity={sev}", headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 200
        data = r.json()
        assert all(e["severity"] == sev for e in data["events"]), f"Mismatch in severity filter {sev}"

    # Test token tracking calculation
    r_all = client.get("/api/audit", headers={"Authorization": f"Bearer {tok}"})
    assert r_all.status_code == 200
    data_all = r_all.json()
    assert "tokens" in data_all["summary"]
    assert "tokens_saved" in data_all["summary"]
    assert data_all["summary"]["tokens"] >= 95


# ============================================================================
# 4. MULTI-VECTOR STRESS & BOUNDARY TESTS
# ============================================================================

def test_contradiction_storm_50_parallel_facts():
    """
    Multi-vector Stress Test:
    50 conflicting facts asserted in parallel with varying tiers (ROOT, ESTABLISHED, NEW)
    under concurrent multithreading.
    Verifies:
    1. No thread race conditions (e.g. dict changed size during iteration).
    2. Consistent resolution: all weaker conflicting assertions under ROOT are quarantined.
    3. Graph integrity invariants remain healthy.
    """
    tenant = f"tenant_storm_{int(time.time() * 1000)}"
    store = get_graph_store(tenant)

    # 1 anchor ROOT fact
    root_anchor = GraphNode(
        id="fact_root_anchor",
        kind=NodeKind.FACT,
        label="Anchor Truth: Refund is 30 days",
        key="refund_window",
        value="30",
        status=Status.HEALTHY,
        asserted_by=AssertedBy(id="agent_policy", name="Policy Bot", tier=Tier.ROOT),
    )
    store.add_node(root_anchor)

    tiers = [Tier.NEW] * 30 + [Tier.ESTABLISHED] * 15 + [Tier.ROOT] * 5
    num_facts = 50

    def assert_conflicting_fact(i):
        tier_val = tiers[i]
        fact_node = GraphNode(
            id=f"storm_fact_{i}",
            kind=NodeKind.FACT,
            label=f"Storm assertion {i}: refund is {100 + i} days",
            key="refund_window",
            value=f"{100 + i}",
            status=Status.HEALTHY,
            asserted_by=AssertedBy(id=f"storm_agent_{i}", name=f"Agent {i}", tier=tier_val),
        )
        with store._lock:
            store.add_node(fact_node)

        # Auditor contradiction check
        res = auditor_service.check_contradiction(store, fact_node, tenant=tenant)
        return (fact_node.id, tier_val, res)

    # Execute 50 assertions in parallel across 10 worker threads
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(assert_conflicting_fact, i) for i in range(num_facts)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    assert len(results) == 50

    # Verification:
    # 1. Anchor root fact MUST be either HEALTHY or involved in a tie-break with another ROOT.
    # Because all 30 NEW and 15 ESTABLISHED contradicted the ROOT anchor (or other roots),
    # all non-root facts contradicting a ROOT fact MUST be QUARANTINED.
    with store._lock:
        new_or_est_healthy = [
            n for n in store.nodes.values()
            if n.key == "refund_window" and n.status == Status.HEALTHY and n.asserted_by and n.asserted_by.tier in (Tier.NEW, Tier.ESTABLISHED)
        ]
        # None of the weaker facts contradicting the ROOT anchor should remain healthy!
        assert len(new_or_est_healthy) == 0, f"Found {len(new_or_est_healthy)} weaker facts still healthy: {[n.id for n in new_or_est_healthy]}"

    # Invariant checks
    inv = store.check_invariants()
    assert inv["ok"] is True
    for c in inv["checks"]:
        assert c["ok"] is True, f"Invariant check failed: {c['name']}"
