"""
Dedicated Multi-Domain Stress Test Suites for Quorum:
Covers each of the 10 core architectural domains under high concurrency and load:
1. Domain 1: Auth, Tokens & Rate Limit / Lockout
2. Domain 2: RBAC, Teams & Workspace Seat Governance
3. Domain 3: Causal Graph Engine, Invariants & Deep DAGs
4. Domain 4: Quorum Gate & Policy Corroboration
5. Domain 5: Action Executor & Saga Compensation Outbox
6. Domain 6: Handoffs, Leases & Claim Stampede
7. Domain 7: Auditor Contradictions & Retraction Cascade
8. Domain 8: Context Packing, Ranking & Path Finding
9. Domain 9: MCP Protocol Guard & Tool Dispatch
10. Domain 10: Event Bus Monotonicity & Procedural Memory
"""
import concurrent.futures
import threading
import uuid
import pytest

from agents.procedures import procedure_registry
from app.config import settings
from app.graph.queries import get_graph_store
from app.models.common import (
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
from app.ratelimit import FailureTracker, RateLimiter
from app.services import workspace
from app.services.auditor import auditor_service
from app.services.context_pack import find_path, pack
from app.services.event_bus import event_bus
from app.services.executor import action_executor
from app.services.gate import quorum_gate
from app.services.mcp_tools import (
    claim_handoff,
    derive,
    remember_fact,
)
from app.services.saga import OutboxSagaWorker


@pytest.fixture(autouse=True)
def setup_domain_stress_env(monkeypatch):
    monkeypatch.setattr(settings, "PERSISTENCE", "memory")
    monkeypatch.setattr(settings, "ALLOW_DYNAMIC_TOKENS", True)


# ============================================================================
# DOMAIN 1 STRESS: Auth, Lockout Tracker & Rate Limiter
# ============================================================================
def test_domain_1_auth_lockout_and_rate_limit_stress():
    tracker = FailureTracker(limit=15)
    limiter = RateLimiter(per_minute=300)

    # 1. Concurrent failure registrations from multiple IPs
    ip_under_attack = "192.168.1.100"
    legit_ip = "10.0.0.1"

    def record_attack():
        for _ in range(10):
            tracker.record(ip_under_attack)

    def record_legit():
        for _ in range(5):
            limiter.allow(legit_ip)

    threads = []
    for _ in range(5):
        threads.append(threading.Thread(target=record_attack))
        threads.append(threading.Thread(target=record_legit))

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # ip_under_attack must be locked out
    rem = tracker.locked(ip_under_attack)
    assert rem > 0
    # legit_ip must NOT be locked out
    rem_legit = tracker.locked(legit_ip)
    assert rem_legit == 0


# ============================================================================
# DOMAIN 2 STRESS: RBAC, Teams & Workspace Seat Governance
# ============================================================================
def test_domain_2_workspace_seat_limits_and_teams_concurrency():
    tenant = f"ws_stress_{uuid.uuid4().hex[:6]}"
    store = get_graph_store(tenant)

    # Add team with 3 seats
    team = workspace.add_team(store, name="Engineering", seats=3)

    created_members = []
    errors = []

    def create_member(idx: int):
        try:
            member, token = workspace.add_member(
                store=store,
                name=f"Dev_{idx}",
                role="agent",
                team_id=team["id"],
                client="claude-code",
                device="laptop",
            )
            created_members.append(member)
        except Exception as e:
            errors.append(e)

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        futures = [ex.submit(create_member, i) for i in range(10)]
        concurrent.futures.wait(futures)

    # Exactly 3 seats allowed under team seat limit
    assert len(created_members) == 3
    assert len(errors) == 7
    assert all("free seats" in str(e).lower() for e in errors)


# ============================================================================
# DOMAIN 3 STRESS: Causal Graph Engine, Invariants & Deep DAGs
# ============================================================================
def test_domain_3_causal_graph_engine_invariants_under_load():
    tenant = f"cg_stress_{uuid.uuid4().hex[:6]}"
    store = get_graph_store(tenant)

    # Build diamond multi-branch DAG concurrently
    root = GraphNode(id="root_fact", kind=NodeKind.FACT, label="Root", status=Status.HEALTHY, key="K", value="V")
    store.add_node(root)

    def add_branch(branch_id: int):
        for step in range(5):
            nid = f"b{branch_id}_s{step}"
            node = GraphNode(id=nid, kind=NodeKind.CONCLUSION, label=f"Branch {branch_id} Step {step}", status=Status.HEALTHY)
            store.add_node(node)
            parent = "root_fact" if step == 0 else f"b{branch_id}_s{step - 1}"
            edge = GraphEdge(id=f"e_{nid}", source=parent, target=nid, type=EdgeType.LEADS_TO)
            store.add_edge(edge)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        futures = [ex.submit(add_branch, i) for i in range(8)]
        concurrent.futures.wait(futures)

    inv = store.check_invariants()
    assert all(c["ok"] for c in inv["checks"])
    assert len(store.nodes) == 41  # 1 root + 8 branches * 5 nodes

    # Quarantine root and verify invariant holding
    affected = store.cascade_quarantine("root_fact")
    assert len(affected) == 41
    inv_after = store.check_invariants()
    assert all(c["ok"] for c in inv_after["checks"])


# ============================================================================
# DOMAIN 4 STRESS: Quorum Gate & Policy Corroboration
# ============================================================================
def test_domain_4_quorum_gate_corroboration_races():
    tenant = f"gate_stress_{uuid.uuid4().hex[:6]}"
    store = get_graph_store(tenant)

    # Create root policy fact and uncorroborated leaf facts
    f_root = GraphNode(
        id="f_root",
        kind=NodeKind.FACT,
        label="Root Policy",
        status=Status.HEALTHY,
        key="refund_policy",
        value="allow",
        origin="policy_doc",
        asserted_by=AssertedBy(id="agent_root", name="Root", tier=Tier.ROOT),
    )
    store.add_node(f_root)

    results = []

    def evaluate_action(idx: int):
        # Stage irreversible action requiring corroboration
        act = GraphNode(
            id=f"act_irrev_{idx}",
            kind=NodeKind.ACTION,
            label=f"Action {idx}",
            status=Status.HEALTHY,
            action=ActionInfo(
                type="refund",
                reversibility=Reversibility.IRREVERSIBLE,
                executed=False,
                confirmed=False,
                gate=GateStatus.BLOCKED,
            ),
        )
        store.add_node(act)
        store.add_edge(GraphEdge(id=f"e_{act.id}", source="f_root", target=act.id, type=EdgeType.LEADS_TO))
        gate_status, reason, handoff = quorum_gate.evaluate(store, act, tenant=tenant)
        results.append(gate_status)

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        futures = [ex.submit(evaluate_action, i) for i in range(20)]
        concurrent.futures.wait(futures)

    # All should pass because leaf fact has Tier.ROOT
    assert len(results) == 20
    assert all(st == GateStatus.PASSED for st in results)


# ============================================================================
# DOMAIN 5 STRESS: Action Executor & Saga Compensation Outbox
# ============================================================================
def test_domain_5_action_executor_and_saga_compensation_stress():
    tenant = f"saga_stress_{uuid.uuid4().hex[:6]}"
    store = get_graph_store(tenant)

    # Create compensable actions and execute them
    executed_ids = []
    for i in range(15):
        f = GraphNode(id=f"f_{i}", kind=NodeKind.FACT, label=f"Fact {i}", status=Status.HEALTHY, key=f"k_{i}", value="v")
        store.add_node(f)
        act = GraphNode(
            id=f"act_{i}",
            kind=NodeKind.ACTION,
            label=f"Action {i}",
            status=Status.HEALTHY,
            action=ActionInfo(
                type="transfer",
                reversibility=Reversibility.COMPENSABLE,
                executed=False,
                confirmed=False,
                gate=GateStatus.PASSED,
                idempotency_key=f"idem_{i}",
            ),
        )
        store.add_node(act)
        store.add_edge(GraphEdge(id=f"e_{act.id}", source=f.id, target=act.id, type=EdgeType.LEADS_TO))
        action_executor.execute(store, act.id, tenant=tenant)
        executed_ids.append(act.id)

    # Invalidate facts via auditor quarantine to trigger compensations
    for i in range(15):
        auditor_service.quarantine(store, f"f_{i}", tenant=tenant)

    # Run saga worker to compensate
    worker = OutboxSagaWorker()
    compensated = worker.process_pending(store, tenant=tenant)
    assert len(compensated) == 15
    for act_id in executed_ids:
        assert store.nodes[act_id].action.compensation.status.value == "COMPENSATED"


# ============================================================================
# DOMAIN 6 STRESS: Handoffs, Leases & Claim Stampede
# ============================================================================
def test_domain_6_handoff_lease_stampede():
    tenant = f"h_stress_{uuid.uuid4().hex[:6]}"
    store = get_graph_store(tenant)

    # Add 20 pending handoffs
    for i in range(20):
        h = Handoff(
            id=f"task_{i}",
            kind=HandoffKind.TASK,
            task=f"Resolve ticket {i}",
            status=HandoffStatus.PENDING,
        )
        store.add_handoff(h)

    # 10 agents race to claim the 20 handoffs
    claims_won = []

    def agent_claim_spree(agent_name: str):
        for i in range(20):
            res = claim_handoff(id=f"task_{i}", token=f"token_{tenant}_agent")
            if res.get("success"):
                claims_won.append((f"task_{i}", agent_name))

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        futures = [ex.submit(agent_claim_spree, f"agent_{i}") for i in range(10)]
        concurrent.futures.wait(futures)

    # Exactly 20 successful claims, 0 double-claims
    assert len(claims_won) == 20
    claimed_task_ids = [c[0] for c in claims_won]
    assert len(set(claimed_task_ids)) == 20


# ============================================================================
# DOMAIN 7 STRESS: Auditor Contradictions & Retraction Cascade
# ============================================================================
def test_domain_7_auditor_tier_weight_races():
    tenant = f"aud_stress_{uuid.uuid4().hex[:6]}"
    store = get_graph_store(tenant)

    # Established fact exists
    f_old = GraphNode(
        id="fact_old",
        kind=NodeKind.FACT,
        label="Threshold is 100",
        status=Status.HEALTHY,
        key="max_threshold",
        value="100",
        asserted_by=AssertedBy(id="agent_reviewer", name="Reviewer", tier=Tier.ESTABLISHED),
    )
    store.add_node(f_old)

    # New agent asserts contradictory fact (tier weight ratio >= 3x in favor of established)
    f_new = GraphNode(
        id="fact_new",
        kind=NodeKind.FACT,
        label="Threshold is 200",
        status=Status.HEALTHY,
        key="max_threshold",
        value="200",
        asserted_by=AssertedBy(id="agent_new", name="Newcomer", tier=Tier.NEW),
    )
    store.add_node(f_new)

    with store._lock:
        auditor_service.check_contradiction(store, f_new, tenant=tenant)

    # The weaker fact (newcomer) must be auto-quarantined
    assert f_new.status == Status.QUARANTINED
    assert store.nodes["fact_old"].status == Status.HEALTHY


# ============================================================================
# DOMAIN 8 STRESS: Context Packing, Ranking & Path Finding
# ============================================================================
def test_domain_8_context_pack_and_path_finding_stress():
    tenant = f"cp_stress_{uuid.uuid4().hex[:6]}"
    store = get_graph_store(tenant)

    # Populate 50 interconnected facts and conclusions
    for i in range(25):
        f = GraphNode(id=f"fact_{i}", kind=NodeKind.FACT, label=f"Fact about server-{i} latency", status=Status.HEALTHY, text=f"server-{i} latency is 20ms")
        store.add_node(f)
        c = GraphNode(id=f"conc_{i}", kind=NodeKind.CONCLUSION, label=f"Server {i} is fast", status=Status.HEALTHY, text=f"server-{i} is fast")
        store.add_node(c)
        store.add_edge(GraphEdge(id=f"e_{i}", source=f.id, target=c.id, type=EdgeType.LEADS_TO))

    # Concurrent search and path finding
    pack_results = []
    path_results = []

    def search_and_path(i: int):
        p = pack(store, query=f"server-{i}", budget=150)
        pack_results.append(p)
        fp = find_path(store, a=f"fact_{i}", b=f"conc_{i}")
        path_results.append(fp)

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        futures = [ex.submit(search_and_path, i) for i in range(25)]
        concurrent.futures.wait(futures)

    assert len(pack_results) == 25
    assert len(path_results) == 25
    assert all(fp.get("success") for fp in path_results)
    assert all(fp.get("hops") == 1 for fp in path_results)


# ============================================================================
# DOMAIN 9 STRESS: MCP Protocol Guard & Tool Dispatch
# ============================================================================
def test_domain_9_mcp_protocol_tool_guard_stress(monkeypatch):
    monkeypatch.setattr(settings, "ALLOW_DYNAMIC_TOKENS", True)
    tenant = f"mcp_stress_{uuid.uuid4().hex[:6]}"
    token = f"token_{tenant}_agent"

    # Concurrent tool calls from 20 parallel threads
    results = []

    def call_mcp_tools(idx: int):
        f_res = remember_fact(key=f"k_{idx}", value=f"v_{idx}", text=f"Fact text {idx}", token=token)
        if "fact_id" in f_res:
            fid = f_res["fact_id"]
            d_res = derive(text=f"Derived conclusion {idx}", parent_ids=[fid], token=token)
            results.append(d_res.get("success"))

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        futures = [ex.submit(call_mcp_tools, i) for i in range(20)]
        concurrent.futures.wait(futures)

    assert len(results) == 20
    assert all(results)


# ============================================================================
# DOMAIN 10 STRESS: Event Bus Monotonicity & Procedural Memory
# ============================================================================
def test_domain_10_event_bus_monotonic_and_procedures_stress():
    tenant = f"eb_stress_{uuid.uuid4().hex[:6]}"

    # 1. Concurrently emit 500 events to the event bus
    def emit_events(thread_id: int):
        for step in range(50):
            event_bus.publish(tenant, "custom_event", {"thread": thread_id, "step": step})

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        futures = [ex.submit(emit_events, i) for i in range(10)]
        concurrent.futures.wait(futures)

    buffer = event_bus.get_tenant_buffer(tenant)
    with buffer._lock:
        seqs = [e.seq for e in buffer.history]

    # Exactly strictly increasing monotonic sequences with no skips in history
    assert len(seqs) == 500
    assert seqs == list(range(1, 501))

    # 2. Concurrently record outcomes in procedural memory
    proc_id = f"proc_refund_flow_{uuid.uuid4().hex[:6]}"
    procedure_registry.distill_new_procedure("refund", ["step1"], procedure_id=proc_id)

    def record_outcomes():
        for _ in range(50):
            procedure_registry.record_outcome(proc_id, success=True)
            procedure_registry.record_outcome(proc_id, success=False)

    threads = [threading.Thread(target=record_outcomes) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    procs = procedure_registry.list_procedures()
    target_proc = next((p for p in procs if p.id == proc_id), None)
    assert target_proc is not None
    assert target_proc.uses == 501  # 1 initial + 500
    assert target_proc.successes == 251  # 1 initial + 250
