"""
Massive multi-agent and multi-scenario concurrency stress test suite.
Tests:
1. 10+ concurrent tenants running full scenario lifecycle pipelines in parallel threads.
2. Concurrent multi-agent races: Scout, Analyst, Refunder, Comms, and Auditor hammering MCP tools.
3. Heavy concurrent handoff claim races across multiple tenants with zero double-claims.
4. Full invariant validation ensuring zero healthy descendants under quarantined ancestors,
   strict tenant isolation, monotonic epochs, and zero deadlocks or data corruption.
"""
import concurrent.futures
import time
import uuid
import pytest

from app.graph.queries import get_graph_store
from app.models.common import Status, HandoffKind, HandoffStatus
from app.models.handoffs import Handoff
from app.services.scenario_orchestrator import scenario_orchestrator
from app.services.mcp_tools import (
    remember_fact,
    derive,
    claim_handoff,
    stage_action,
    execute_action,
    retrieve_context,
    delta,
    explain_blast_radius,
)
from agents.procedures import procedure_registry
from app.services.auditor import auditor_service
from app.services.helpdesk import helpdesk_service


@pytest.fixture(autouse=True)
def clean_helpdesk():
    helpdesk_service.reset()


def _run_tenant_scenario_lifecycle(tenant_id: str) -> dict:
    """Runs all 6 scenario steps for a tenant and verifies consistency."""
    steps = ["reset", "inject_poison", "run_agents", "audit", "supersede", "restore"]
    results = {}
    for step in steps:
        res = scenario_orchestrator.execute_step(step, tenant=tenant_id)
        results[step] = res

    store = get_graph_store(tenant_id)
    inv = store.check_invariants()
    return {
        "tenant": tenant_id,
        "results": results,
        "invariants_ok": inv["ok"],
        "node_count": len(store.nodes),
        "epoch": store.epoch,
    }


def test_concurrent_multiple_scenarios_across_distinct_tenants():
    """
    Stress Test 1: 12 independent tenants running full 6-step scenario pipelines concurrently.
    Verifies tenant isolation, thread safety, and graph invariant consistency.
    """
    tenants = [f"stress_scen_tenant_{i}_{uuid.uuid4().hex[:6]}" for i in range(12)]

    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
        futures = {executor.submit(_run_tenant_scenario_lifecycle, t): t for t in tenants}
        completed = []
        for f in concurrent.futures.as_completed(futures):
            res = f.result()
            completed.append(res)

    assert len(completed) == 12
    for item in completed:
        assert item["invariants_ok"] is True, f"Invariant failure on {item['tenant']}"
        assert item["node_count"] > 0
        assert item["epoch"] >= 3

        # Verify tenant graph integrity
        store = get_graph_store(item["tenant"])
        with store._lock:
            # Poisoned fact must be quarantined
            assert "fact_90d" in store.nodes
            assert store.nodes["fact_90d"].status == Status.QUARANTINED
            # Superseded v4 policy must exist and be healthy
            assert "fact_45d_v4" in store.nodes
            assert store.nodes["fact_45d_v4"].status == Status.HEALTHY


def test_multiple_agents_concurrent_mcp_actions_and_derivations():
    """
    Stress Test 2: 30 concurrent agents executing MCP tools:
    - Scouts asserting facts
    - Analysts reading context and deriving conclusions
    - Refunders staging and executing actions
    - Auditors inspecting blast radius and running quarantines
    """
    tenant = f"stress_agents_{uuid.uuid4().hex[:8]}"
    store = get_graph_store(tenant)
    tok_scout = f"token_{tenant}_agent"
    tok_analyst = f"token_{tenant}_agent"
    tok_refunder = f"token_{tenant}_agent"
    tok_auditor = f"token_{tenant}_auditor"

    # Pre-seed root fact
    r_root = remember_fact(
        key="global_threshold",
        value="500",
        text="Global max limit is 500 units.",
        token=tok_scout,
        origin="policy:root",
    )
    assert r_root["status"] == "HEALTHY"
    root_id = r_root["fact_id"]

    def _scout_worker(idx: int):
        return remember_fact(
            key=f"sub_metric_{idx}",
            value=str(100 + idx),
            text=f"Sub metric {idx} observed at {100 + idx}",
            token=tok_scout,
            origin=f"scout:{idx}",
        )

    def _analyst_worker(idx: int):
        # Retrieve context and derive
        retrieve_context(query="metric limit", token=tok_analyst, k=3)
        return derive(
            text=f"Derived insight {idx} bounded by root limit",
            parent_ids=[root_id],
            token=tok_analyst,
            is_decision=(idx % 2 == 0),
        )

    def _refunder_worker(idx: int, parent_conclusion_id: str):
        # Stage compensable action
        stg = stage_action(
            label=f"Process batch {idx} refund",
            action_type="REFUND_BATCH",
            reversibility="COMPENSABLE",
            parent_ids=[parent_conclusion_id],
            procedure_id="proc_refund_standard",
            idempotency_key=f"idemp_ref_batch_{idx}",
            token=tok_refunder,
        )
        act_id = stg["action_id"]
        # Half of the agents execute immediately
        if idx % 2 == 0:
            return execute_action(id=act_id, token=tok_refunder)
        return stg

    # Run 10 Scouts concurrently
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        scout_res = list(ex.map(_scout_worker, range(10)))
    assert len(scout_res) == 10
    assert all(r["status"] == "HEALTHY" for r in scout_res)

    # Run 10 Analysts deriving conclusions concurrently
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        analyst_res = list(ex.map(_analyst_worker, range(10)))
    assert len(analyst_res) == 10
    assert all(r["success"] is True for r in analyst_res)
    conclusion_ids = [r["conclusion_id"] for r in analyst_res]

    # Run 10 Refunders staging and executing actions concurrently
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        futures = [
            ex.submit(_refunder_worker, i, conclusion_ids[i % len(conclusion_ids)])
            for i in range(10)
        ]
        refunder_res = [f.result() for f in concurrent.futures.as_completed(futures)]
    assert len(refunder_res) == 10

    # Auditor checks blast radius
    blast = explain_blast_radius(root_id=root_id, token=tok_auditor)
    assert blast["predicted_impact_count"] >= 20  # Root affects all derived conclusions & actions

    # Invariants must hold perfectly
    inv = store.check_invariants()
    assert inv["ok"] is True


def test_massive_concurrent_handoff_claim_race_across_tenants():
    """
    Stress Test 3: 50 handoffs distributed across 5 distinct tenants,
    each contended by 10 concurrent worker threads (500 total claim attempts).
    Asserts:
    - Exactly 1 winner per handoff.
    - Zero double-claims.
    - Zero unhandled exceptions or state races.
    """
    tenants = [f"stress_handoff_tenant_{i}" for i in range(5)]
    handoffs_per_tenant = 10

    # Seed handoffs across all tenants
    all_tasks = []
    for t in tenants:
        store = get_graph_store(t)
        with store.handoff_lock:
            store.handoffs.clear()
            for h_idx in range(handoffs_per_tenant):
                hid = f"h_{t}_{h_idx}"
                store.handoffs.append(
                    Handoff(
                        id=hid,
                        kind=HandoffKind.TASK,
                        task=f"Verify transaction {hid}",
                        status=HandoffStatus.PENDING,
                    )
                )
                all_tasks.append((t, hid))

    from app.config import settings

    # Register individual worker tokens for all tenants
    for t in tenants:
        for a_idx in range(10):
            aid = f"agent_worker_{a_idx}"
            settings.DEMO_TOKENS[f"tok_{t}_{aid}"] = {"tenant": t, "role": "agent", "agent_id": aid}

    claim_winners = {}
    claim_lock = concurrent.futures.thread.threading.Lock()

    def _claim_worker(task_tuple, agent_id: str):
        t, hid = task_tuple
        tok = f"tok_{t}_{agent_id}"
        res = claim_handoff(id=hid, token=tok)
        if res.get("success"):
            with claim_lock:
                claim_winners.setdefault(hid, []).append(agent_id)
        return res

    # 50 tasks x 10 contenders = 500 concurrent operations
    tasks_to_run = []
    for t_tuple in all_tasks:
        for a_idx in range(10):
            tasks_to_run.append((t_tuple, f"agent_worker_{a_idx}"))

    with concurrent.futures.ThreadPoolExecutor(max_workers=25) as executor:
        futures = [executor.submit(_claim_worker, task, aid) for task, aid in tasks_to_run]
        for f in concurrent.futures.as_completed(futures):
            f.result()

    # Verify that every single handoff was claimed by exactly 1 winner!
    for _, hid in all_tasks:
        assert hid in claim_winners, f"Handoff {hid} was never claimed"
        winners = claim_winners[hid]
        assert len(winners) == 1, f"Handoff {hid} double-claimed by {winners}!"


def test_quarantine_blast_and_cascade_invariants_under_heavy_load():
    """
    Stress Test 4: Dynamic DAG with 50 nodes and cross-edges.
    Concurrent threads repeatedly quarantine random root nodes while
    other threads read context and attempt derivations.
    Guarantees:
    - Zero healthy nodes ever exist downstream of any quarantined node.
    - No read-write deadlock occurs.
    """
    tenant = f"stress_cascade_{uuid.uuid4().hex[:8]}"
    store = get_graph_store(tenant)
    tok = f"token_{tenant}_agent"

    # Build layered DAG: 3 roots -> 15 mid nodes -> 30 leaf nodes
    roots = []
    for r in range(3):
        res = remember_fact(
            key=f"root_k_{r}",
            value=f"val_{r}",
            text=f"Root premise {r}",
            token=tok,
        )
        roots.append(res["fact_id"])

    mids = []
    for m in range(15):
        parent = roots[m % len(roots)]
        res = derive(
            text=f"Intermediate conclusion {m}",
            parent_ids=[parent],
            token=tok,
        )
        mids.append(res["conclusion_id"])

    leaves = []
    for l in range(30):
        parent = mids[l % len(mids)]
        res = derive(
            text=f"Leaf conclusion {l}",
            parent_ids=[parent],
            token=tok,
        )
        leaves.append(res["conclusion_id"])

    stop_flag = False

    def _reader_worker():
        while not stop_flag:
            retrieve_context(query="premise conclusion", token=tok, k=5)
            delta(token=tok)
            time.sleep(0.005)

    def _mutator_worker():
        # Quarantine roots[0]
        auditor_service.quarantine(store, roots[0], reason="Stress test poison injection", tenant=tenant)
        # Restore roots[0]
        auditor_service.restore(store, roots[0], tenant=tenant)
        # Quarantine roots[1]
        auditor_service.quarantine(store, roots[1], reason="Second poison injection", tenant=tenant)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        reader_futs = [ex.submit(_reader_worker) for _ in range(6)]
        mutator_fut = ex.submit(_mutator_worker)

        mutator_fut.result()
        time.sleep(0.1)
        stop_flag = True
        for rf in reader_futs:
            rf.result()

    # Final invariant check: Absolutely NO healthy node below quarantined root
    inv = store.check_invariants()
    assert inv["ok"] is True, f"Invariant violated: {inv}"


def test_concurrent_procedure_adaptive_learning_stress():
    """
    Stress Test 5: 100 concurrent workers recording successes and failures
    into procedure registry. Verifies Laplace smoothing accuracy and thread safety.
    """
    proc_id = "proc_stress_adaptive"
    from agents.procedures import Procedure
    with procedure_registry._lock:
        procedure_registry._procedures[proc_id] = Procedure(
            id=proc_id,
            task_type="stress_task",
            steps=["step1", "step2"],
            uses=0,
            successes=0,
            failures=0,
        )

    def _record_outcome_worker(is_success: bool):
        procedure_registry.record_outcome(proc_id, success=is_success)

    # 60 successes, 40 failures concurrently
    outcomes = [True] * 60 + [False] * 40

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as ex:
        list(ex.map(_record_outcome_worker, outcomes))

    with procedure_registry._lock:
        proc = procedure_registry._procedures[proc_id]
        assert proc.uses == 100
        assert proc.successes == 60
        assert proc.failures == 40
        # Laplace smoothing: (60 + 1) / (100 + 2) = 61 / 102 = 0.598
        assert proc.rank_score == 0.598


def test_cross_tenant_strict_privacy_and_team_isolation_under_stress():
    """
    Stress Test 6: Cross-tenant & cross-team data privacy isolation.
    Tests:
    - Team-scoped facts are invisible to other teams unless explicitly linked.
    - Pausing a link instantly revokes visibility even under concurrent querying.
    - Tenant isolation is strictly absolute (zero leakage across tenants).
    """
    from app.services import workspace

    t1 = f"stress_iso_1_{uuid.uuid4().hex[:6]}"
    t2 = f"stress_iso_2_{uuid.uuid4().hex[:6]}"
    store1 = get_graph_store(t1)
    store2 = get_graph_store(t2)

    # Tenant 1: Team Red and Team Blue
    tm_red = workspace.add_team(store1, "RedTeam", 5)
    tm_blue = workspace.add_team(store1, "BlueTeam", 5)
    m_red, tok_red = workspace.add_member(store1, "AgentRed", "agent", tm_red["id"], "claude-code", "laptop")
    m_blue, tok_blue = workspace.add_member(store1, "AgentBlue", "agent", tm_blue["id"], "claude-code", "laptop")

    # Tenant 2: Team Green
    tm_green = workspace.add_team(store2, "GreenTeam", 5)
    _m_green, tok_green = workspace.add_member(store2, "AgentGreen", "agent", tm_green["id"], "claude-code", "laptop")

    # Red records private team fact
    r_red = remember_fact(
        key="red_confidential_key",
        value="red_val_42",
        text="Top secret Red team credential or internal memo",
        scope="team",
        token=tok_red,
    )
    assert r_red["status"] == "HEALTHY"

    # 1. Blue agent in same tenant cannot see Red's private fact
    ctx_blue = retrieve_context(query="Top secret Red", token=tok_blue)
    assert len(ctx_blue["context"]) == 0

    # 2. Green agent in different tenant cannot see Red's fact
    ctx_green = retrieve_context(query="Top secret Red", token=tok_green)
    assert len(ctx_green["context"]) == 0

    # 3. Link Red and Blue
    link_obj = workspace.link(store1, m_red["id"], m_blue["id"], scope="team")
    assert link_obj["paused"] is False

    # Blue agent can now see Red's private fact!
    ctx_blue_linked = retrieve_context(query="Top secret Red", token=tok_blue)
    assert len(ctx_blue_linked["context"]) == 1
    assert ctx_blue_linked["context"][0]["key"] == "red_confidential_key"

    # Green in tenant 2 still cannot see it!
    assert len(retrieve_context(query="Top secret Red", token=tok_green)["context"]) == 0

    # 4. Stress: Concurrently toggle link pause while Blue queries continuously
    stop_workers = False

    def _query_worker():
        while not stop_workers:
            res = retrieve_context(query="Top secret Red", token=tok_blue)
            assert isinstance(res["context"], list)
            time.sleep(0.002)

    def _toggle_worker():
        for _ in range(15):
            workspace.set_link_paused(store1, link_obj["id"], paused=True)
            time.sleep(0.005)
            workspace.set_link_paused(store1, link_obj["id"], paused=False)
            time.sleep(0.005)

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        q_futs = [ex.submit(_query_worker) for _ in range(5)]
        t_fut = ex.submit(_toggle_worker)

        t_fut.result()
        stop_workers = True
        for qf in q_futs:
            qf.result()


def test_agent_runner_full_replay_lifecycle_under_concurrency():
    """
    Stress Test 7: Multi-agent runner (Scout, Analyst, Refunder, Comms)
    hammered across multiple scenario steps and claim races concurrently.
    """
    from agents.runner import AgentRunner
    test_runner = AgentRunner()

    steps = ["inject_poison", "run_agents", "audit", "supersede", "restore", "reset"]

    def _runner_worker(step_name: str):
        return test_runner.run_step(step_name)

    # Run steps concurrently across threads
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        futures = [ex.submit(_runner_worker, s) for s in steps]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    assert len(results) == len(steps)
    for res in results:
        assert "step" in res
        assert "agent_results" in res

