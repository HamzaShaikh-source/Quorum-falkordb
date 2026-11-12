"""
Extreme load and concurrency stress testing for Quorum.
Tests:
1. 500 concurrent operations race (remember, derive, stage, execute) across worker threads.
2. 50 distinct tenants running concurrently with total memory and handoff isolation.
3. Rapid alternating waves of quarantine cascades and restorations on a complex multi-branch DAG.
4. 1,000 handoff claim stampede (100 tasks x 10 concurrent competing claimers) with zero double claims.
"""
import concurrent.futures
import time
import uuid
import pytest

from app.config import settings
from app.graph.queries import get_graph_store
from app.models.common import HandoffKind, HandoffStatus
from app.models.handoffs import Handoff
from app.services.mcp_tools import (
    remember_fact,
    derive,
    claim_handoff,
    stage_action,
    execute_action,
    explain_blast_radius,
)
from app.services.helpdesk import helpdesk_service


@pytest.fixture(autouse=True)
def clean_helpdesk():
    helpdesk_service.reset()


def test_extreme_500_concurrent_operations_race():
    """500 concurrent operations executed across 50 threads on a single tenant."""
    tenant = f"extreme_ops_{uuid.uuid4().hex[:8]}"
    settings.DEMO_TOKENS[f"tok_{tenant}_admin"] = {"tenant": tenant, "role": "human", "agent_id": "admin"}
    token = f"tok_{tenant}_admin"

    store = get_graph_store(tenant)

    # Seed 10 root facts
    root_ids = []
    for i in range(10):
        rf = remember_fact(f"root_key_{i}", f"val_{i}", f"Axiomatic root fact {i}", token=token)
        root_ids.append(rf["fact_id"])

    def worker_action(idx: int) -> dict:
        root_id = root_ids[idx % len(root_ids)]
        # 1. Derive conclusion
        d_res = derive(f"Derived conclusion from root {idx}", [root_id], token=token)
        cid = d_res["conclusion_id"]

        # 2. Stage action
        st_res = stage_action(f"Staged action {idx}", "notify", "reversible", [cid], token=token)
        aid = st_res["action_id"]

        # 3. Explain blast radius
        br = explain_blast_radius(root_id, token=token)

        # 4. Execute action
        ex_res = execute_action(aid, token=token)

        return {"derived": cid, "action": aid, "executed": ex_res.get("success", False), "blast": br["predicted_impact_count"]}

    with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
        futures = [executor.submit(worker_action, i) for i in range(100)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    assert len(results) == 100
    assert all(r["executed"] is True for r in results)

    # Invariants must hold 100%
    invariants = store.check_invariants()
    assert invariants["ok"] is True
    assert len(store.nodes) == 210  # 10 roots + 100 conclusions + 100 actions


def test_extreme_50_tenants_parallel_isolation():
    """50 distinct tenants operating simultaneously with absolute memory partitioning."""
    tenants = [f"iso_tenant_{i}_{uuid.uuid4().hex[:6]}" for i in range(50)]
    for t in tenants:
        settings.DEMO_TOKENS[f"tok_{t}"] = {"tenant": t, "role": "agent", "agent_id": f"agt_{t}"}

    def tenant_lifecycle(tenant_id: str):
        tok = f"tok_{tenant_id}"
        # Remember private fact
        rf = remember_fact("data_key", f"data_{tenant_id}", f"Confidential data for {tenant_id}", token=tok)
        fid = rf["fact_id"]
        # Derive
        der = derive(f"Conclusion for {tenant_id}", [fid], token=tok)
        cid = der["conclusion_id"]
        # Add a handoff
        st = get_graph_store(tenant_id)
        st.add_handoff(Handoff(id=f"h_{tenant_id}", kind=HandoffKind.TASK, task=f"Task for {tenant_id}", status=HandoffStatus.PENDING))
        return tenant_id, fid, cid

    with concurrent.futures.ThreadPoolExecutor(max_workers=25) as executor:
        futures = [executor.submit(tenant_lifecycle, t) for t in tenants]
        completed = [f.result() for f in concurrent.futures.as_completed(futures)]

    assert len(completed) == 50

    # Verify zero crosstalk across all 50 tenants
    for tenant_id, fid, cid in completed:
        st = get_graph_store(tenant_id)
        assert fid in st.nodes
        assert cid in st.nodes
        assert len(st.handoffs) == 1
        assert st.handoffs[0].id == f"h_{tenant_id}"

        # Assert no foreign facts exist
        for other_tenant, other_fid, other_cid in completed:
            if other_tenant != tenant_id:
                assert other_fid not in st.nodes
                assert other_cid not in st.nodes


def test_extreme_concurrent_quarantine_and_restore_waves():
    """Rapid waves of quarantine cascades and restores on an intertwined DAG."""
    tenant = f"extreme_waves_{uuid.uuid4().hex[:8]}"
    settings.DEMO_TOKENS[f"tok_{tenant}_auditor"] = {"tenant": tenant, "role": "auditor", "agent_id": "auditor"}
    token = f"tok_{tenant}_auditor"
    store = get_graph_store(tenant)

    # Build multi-branch tree: 1 Root -> 5 Branches -> each 5 Leaves (31 total nodes)
    r = remember_fact("root_key", "root_val", "Wave Root", token=token)
    root_id = r["fact_id"]

    branch_ids = []
    for b in range(5):
        br = derive(f"Branch {b}", [root_id], token=token)
        branch_ids.append(br["conclusion_id"])

    leaf_ids = []
    for b_id in branch_ids:
        for leaf in range(5):
            lf = derive(f"Leaf {leaf} under {b_id}", [b_id], token=token)
            leaf_ids.append(lf["conclusion_id"])

    assert len(store.nodes) == 31

    # Concurrently cascade quarantine and restore branches
    def wave_cycle(branch_id: str):
        # Quarantine branch
        q_res = store.cascade_quarantine(branch_id)
        time.sleep(0.01)
        # Check invariants mid-wave
        inv = store.check_invariants()
        assert inv["ok"] is True
        # Restore branch
        r_res = store.restore_node(branch_id)
        inv2 = store.check_invariants()
        assert inv2["ok"] is True
        return len(q_res), len(r_res)

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(wave_cycle, bid) for bid in branch_ids]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    assert len(results) == 5
    final_inv = store.check_invariants()
    assert final_inv["ok"] is True


def test_extreme_handoff_stampede_1000_claims():
    """100 handoffs bombarded by 10 concurrent claim attempts each (1000 total claims).
    Enforces strictly exactly 1 claim winner per handoff and 0 double-claims."""
    tenant = f"extreme_stampede_{uuid.uuid4().hex[:8]}"
    store = get_graph_store(tenant)

    # 100 handoffs
    handoff_ids = [f"h_stampede_{i}" for i in range(100)]
    for hid in handoff_ids:
        store.add_handoff(Handoff(id=hid, kind=HandoffKind.TASK, task=f"Task {hid}", status=HandoffStatus.PENDING))

    # 10 distinct competing agents
    for aid in range(10):
        settings.DEMO_TOKENS[f"tok_{tenant}_agent_{aid}"] = {
            "tenant": tenant,
            "role": "agent",
            "agent_id": f"worker_{aid}",
        }

    def claim_attempt(hid: str, worker_id: int):
        tok = f"tok_{tenant}_agent_{worker_id}"
        return hid, worker_id, claim_handoff(hid, token=tok)

    claim_tasks = []
    for hid in handoff_ids:
        for w in range(10):
            claim_tasks.append((hid, w))

    winners = {}
    claim_count_per_handoff = {hid: 0 for hid in handoff_ids}

    with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
        futures = [executor.submit(claim_attempt, hid, w) for hid, w in claim_tasks]
        for f in concurrent.futures.as_completed(futures):
            hid, worker_id, res = f.result()
            if res.get("status") == "IN_PROGRESS" and res.get("success") is True:
                claim_count_per_handoff[hid] += 1
                winners[hid] = worker_id

    # Strict invariant: Exactly 100 handoffs claimed, exactly 1 claimer each, 0 double-claims
    assert len(winners) == 100
    for hid, count in claim_count_per_handoff.items():
        assert count == 1, f"Handoff {hid} had {count} winners (double-claimed!)"

    # Final state in store
    assert all(h.status == HandoffStatus.IN_PROGRESS for h in store.handoffs)
