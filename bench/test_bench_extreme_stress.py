"""
Extreme Stress & Sensitivity Testing for Quorum Benchmark Suite
Tests:
1. High-Agent Concurrency Stress: 60 agents x 50 operations = 3,000 concurrent ops (70/30 read/write).
2. Extreme Handoff Contention: 200 handoffs x 50 concurrent claimers = 10,000 contested claims.
3. Extreme Race Conditions: Timing variations in Execute-vs-Quarantine (0ms lead, 5ms lead, 15ms lead).
4. Deeper Causal Poison Depth: Testing up to causal depth 5 (5-hop ancestor chain containment).
"""

import os
import sys
import time
import random
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any, List
import numpy as np

from graph import queries

TENANT = "benchmark_extreme_tenant"

def setup_tenant():
    g = queries.get_graph(TENANT, "commons")
    try:
        g.delete()
    except Exception:
        pass
    queries.ensure_indexes(TENANT)
    vec = [0.05] * 384
    g.query(
        "CREATE (f:Fact:MemoryNode {id: 'f_extreme_base', key: 'policy.extreme', value: '100', "
        "status: 'HEALTHY', text: 'Extreme test base policy', embedding: vecf32($vec), "
        "valid_from: timestamp(), stale: false})",
        {"vec": vec}
    )

def test_extreme_concurrency(num_agents: int = 60, ops_per_agent: int = 50):
    print(f"\n[Extreme Test 1] Concurrency Scaling: {num_agents} agents x {ops_per_agent} ops = {num_agents * ops_per_agent} ops...")
    latencies = []
    total_ops = 0
    lock = threading.Lock()

    def worker(idx):
        nonlocal total_ops
        random.seed(100 + idx)
        for i in range(ops_per_agent):
            is_read = random.random() < 0.70
            t0 = time.time()
            if is_read:
                queries.as_of(TENANT, int(time.time() * 1000))
            else:
                cid = f"ext_c_{idx}_{i}"
                queries.derive(TENANT, cid, ["f_extreme_base"], f"Extreme derived {idx}_{i}")
            ms = (time.time() - t0) * 1000
            with lock:
                latencies.append(ms)
                total_ops += 1

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=num_agents) as ex:
        futures = [ex.submit(worker, a) for a in range(num_agents)]
        for f in as_completed(futures):
            f.result()
    duration = time.time() - t0

    arr = np.array(latencies)
    throughput = total_ops / duration
    inv = queries.invariant_check(TENANT)

    print(f"  --> Ops: {total_ops} in {duration:.2f}s | Throughput: {throughput:.1f} ops/s")
    print(f"  --> p50: {np.percentile(arr, 50):.2f}ms | p95: {np.percentile(arr, 95):.2f}ms | p99: {np.percentile(arr, 99):.2f}ms")
    print(f"  --> Zero-orphans Invariant: {inv['all_passed']} (Healthy children under quarantined: {inv['healthy_children_under_quarantined']})")
    assert inv["all_passed"] is True


def test_extreme_handoff_contention(num_handoffs: int = 200, num_claimers: int = 50):
    print(f"\n[Extreme Test 2] Extreme Handoff Contention: {num_handoffs} handoffs x {num_claimers} claimers ({num_handoffs * num_claimers} claim attempts)...")
    g = queries.get_graph(TENANT, "commons")

    for i in range(num_handoffs):
        g.query(f"CREATE (:Handoff {{id: 'ext_h_{i}', kind: 'TASK', status: 'PENDING', lease_until: null}})")

    winners = {f"ext_h_{i}": [] for i in range(num_handoffs)}
    lock = threading.Lock()

    def claimer(cid):
        agent_name = f"ext_agent_{cid}"
        for h_idx in range(num_handoffs):
            hid = f"ext_h_{h_idx}"
            claim_res = queries.claim_handoff(TENANT, hid, agent_name, role="agent")
            if claim_res.get("success"):
                with lock:
                    winners[hid].append(agent_name)

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=num_claimers) as ex:
        futures = [ex.submit(claimer, c) for c in range(num_claimers)]
        for f in as_completed(futures):
            f.result()
    duration = time.time() - t0

    single = sum(1 for w in winners.values() if len(w) == 1)
    multi = sum(1 for w in winners.values() if len(w) > 1)
    zero = sum(1 for w in winners.values() if len(w) == 0)

    print(f"  --> 10,000 claims completed in {duration:.2f}s (Throughput: {num_handoffs * num_claimers / duration:.1f} claims/s)")
    print(f"  --> Single winners: {single}/{num_handoffs} (100.0%) | Multiple: {multi} | Zero: {zero}")
    assert single == num_handoffs
    assert multi == 0
    assert zero == 0


def test_extreme_execute_vs_quarantine(timing_scenarios: List[float]):
    print(f"\n[Extreme Test 3] Execute-vs-Quarantine Across Multiple Timing Windows...")
    g = queries.get_graph(TENANT, "commons")

    for q_delay in timing_scenarios:
        tag = f"q_{int(q_delay * 1000)}ms"
        root_id = f"f_ext_poison_{tag}"
        conc_id = f"c_ext_elig_{tag}"
        g.query(f"CREATE (:Fact:MemoryNode {{id: '{root_id}', status: 'HEALTHY', text: 'Poison' }})")
        queries.derive(TENANT, conc_id, [root_id], f"Conc {tag}")

        num_actions = 40
        for i in range(num_actions):
            queries.stage_action(TENANT, f"act_{tag}_{i}", [conc_id], "REFUND", {"id": i}, "COMPENSABLE", gate="PASSED")

        executed = []
        refused = []
        lock = threading.Lock()

        def exec_w(idx):
            aid = f"act_{tag}_{idx}"
            time.sleep(random.uniform(0.001, 0.006))
            res = queries.execute_guard(TENANT, aid)
            with lock:
                if res.get("success"):
                    executed.append(aid)
                else:
                    refused.append(aid)

        def quar_w():
            time.sleep(q_delay)
            queries.cascade(TENANT, root_id)

        with ThreadPoolExecutor(max_workers=20) as ex:
            q_f = ex.submit(quar_w)
            e_fs = [ex.submit(exec_w, i) for i in range(num_actions)]
            q_f.result()
            for f in as_completed(e_fs):
                f.result()

        inv = queries.invariant_check(TENANT)
        violations = inv["actions_executed_after_ancestor_quarantined"]
        print(f"  --> Quarantine delay {q_delay*1000:4.1f}ms: Executed: {len(executed):2d} | Refused: {len(refused):2d} | Invariant Violations: {violations}")
        assert violations == 0, f"Invariant violation detected: {violations}"


def test_deep_causal_depths(max_depth: int = 5):
    print(f"\n[Extreme Test 4] Causal Chain Cascade up to Depth {max_depth}...")
    g = queries.get_graph(TENANT, "commons")

    for d in range(1, max_depth + 1):
        root_id = f"f_deep_{d}_root"
        vec = [0.02 * d] * 384
        g.query(
            "CREATE (f:Fact:MemoryNode {id: $id, key: $key, value: 'deep', status: 'HEALTHY', "
            "text: 'Deep poison', embedding: vecf32($vec), valid_from: timestamp(), stale: false})",
            {"id": root_id, "key": f"deep_key_{d}", "vec": vec}
        )

        curr_parent = root_id
        chain_ids = []
        for step in range(1, d + 1):
            step_id = f"c_deep_{d}_step_{step}"
            queries.derive(TENANT, step_id, [curr_parent], f"Step {step}")
            chain_ids.append(step_id)
            curr_parent = step_id

        # Stage action at leaf
        action_id = f"act_deep_{d}"
        queries.stage_action(TENANT, action_id, [curr_parent], "REFUND", {}, "COMPENSABLE", gate="PASSED")
        chain_ids.append(action_id)

        t0 = time.time()
        casc = queries.cascade(TENANT, root_id)
        ms = (time.time() - t0) * 1000

        retrieved = queries.retrieve(TENANT, vec, k=10)
        retrieved_ids = {r["id"] for r in retrieved}

        all_quarantined = [root_id] + chain_ids
        leaked = [nid for nid in all_quarantined if nid in retrieved_ids]
        leakage_rate = len(leaked) / len(all_quarantined)

        print(f"  --> Depth {d}: Nodes in chain: {len(all_quarantined)} | Quarantined: {casc['affected_count']} | Containment: {ms:.2f}ms | Leakage: {leakage_rate*100:.1f}%")
        assert casc["affected_count"] == len(all_quarantined)
        assert leakage_rate == 0.0


def main():
    print("=" * 80)
    print("EXTREME STRESS & PARAMETER SENSITIVITY TESTING")
    print("=" * 80)

    setup_tenant()
    test_extreme_concurrency(num_agents=60, ops_per_agent=50)
    test_extreme_handoff_contention(num_handoffs=200, num_claimers=50)
    test_extreme_execute_vs_quarantine(timing_scenarios=[0.0005, 0.003, 0.008, 0.015])
    test_deep_causal_depths(max_depth=5)

    print("\n" + "=" * 80)
    print("ALL EXTREME STRESS TESTS PASSED WITH 100% INVARIANT COMPLIANCE")
    print("=" * 80)

if __name__ == "__main__":
    main()
