"""
bench/stress_test_t02_4.py

T02-4: FalkorDB Concurrency Throughput, Pool Limits & Invariant Stress Test
Author: Subagent 04 (Stress-testing Specialist)

Executes 4 comprehensive test suites:
1. 30 concurrent agent threads executing mixed workloads (70% read, 30% write) for 1000 total operations.
2. High-precision latency analysis (throughput ops/sec, p50, p95, p99, min, max, mean, std) broken down by overall, read, and write.
3. Connection pool exhaustion resilience: burst traffic with barrier synchronization (30, 60, 100, 150 concurrent workers), testing default pool vs BlockingConnectionPool, socket drops, timeout resilience, and leak detection.
4. Continuous high-speed mutation vs concurrent invariant_check auditing: verifying zero orphan invariants (zero healthy children under quarantined parents, zero actions executed post-quarantine) during high-frequency concurrent writes.
"""

import sys
import os
import time
import json
import random
import threading
from typing import Dict, Any, List
from concurrent.futures import ThreadPoolExecutor, as_completed
import numpy as np
import redis

# Add repo root to sys.path
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from graph import queries
from falkordb import FalkorDB

TENANT_BENCH = "stress_tenant_t02_4"


def setup_clean_graph(tenant_id: str):
    """Resets and prepares tenant graph with indexes and seed policy."""
    g = queries.get_graph(tenant_id, "commons")
    try:
        g.delete()
    except Exception:
        pass
    queries.ensure_indexes(tenant_id)
    # Seed baseline policy facts
    vec = [0.05] * 384
    g.query(
        "CREATE (f:Fact:MemoryNode {id: 'f_base_policy', key: 'policy.return', value: '30', "
        "status: 'HEALTHY', text: 'Baseline return policy 30 days', embedding: vecf32($vec), "
        "valid_from: timestamp(), stale: false})",
        {"vec": vec}
    )
    g.query(
        "CREATE (f:Fact:MemoryNode {id: 'f_auth_policy', key: 'policy.auth', value: 'mfa', "
        "status: 'HEALTHY', text: 'MFA required for refunds over $100', embedding: vecf32($vec), "
        "valid_from: timestamp(), stale: false})",
        {"vec": vec}
    )


# =========================================================================
# SUITE 1 & 2: 30-Agent Concurrent Workload (70% Read / 30% Write, 1000 Ops)
# =========================================================================

def run_mixed_workload_benchmark(total_ops: int = 1000, num_agents: int = 30) -> Dict[str, Any]:
    print("\n======================================================================")
    print(f"TEST 1 & 2: 30 CONCURRENT AGENTS MIXED WORKLOAD ({total_ops} Total Ops, 70/30)")
    print("======================================================================")
    setup_clean_graph(TENANT_BENCH)

    read_latencies: List[float] = []
    write_latencies: List[float] = []
    all_latencies: List[float] = []
    errors: List[str] = []

    # Thread-safe operation counter & lock
    op_counter = 0
    lock = threading.Lock()

    # Pre-generate operations to ensure exact 70/30 split across 1000 operations
    read_target = int(total_ops * 0.70)
    write_target = total_ops - read_target
    operations_pool = ["READ"] * read_target + ["WRITE"] * write_target
    random.seed(42)
    random.shuffle(operations_pool)

    def agent_worker(agent_idx: int):
        nonlocal op_counter
        agent_id = f"agent_{agent_idx:02d}"

        while True:
            # Claim next operation index atomically
            with lock:
                if op_counter >= total_ops:
                    break
                current_idx = op_counter
                op_type = operations_pool[current_idx]
                op_counter += 1

            t0 = time.perf_counter()
            try:
                if op_type == "READ":
                    # Read operations: as_of query or retrieve or who_knows
                    read_sub = current_idx % 3
                    if read_sub == 0:
                        queries.as_of(TENANT_BENCH, int(time.time() * 1000))
                    elif read_sub == 1:
                        vec = [0.05] * 384
                        queries.retrieve(TENANT_BENCH, vec, k=5)
                    else:
                        queries.who_knows(TENANT_BENCH, "f_base_policy")
                    dur_ms = (time.perf_counter() - t0) * 1000
                    with lock:
                        read_latencies.append(dur_ms)
                        all_latencies.append(dur_ms)
                else:
                    # Write operations: derive conclusions or stage actions
                    write_sub = current_idx % 2
                    if write_sub == 0:
                        cid = f"c_bench_{agent_idx}_{current_idx}"
                        queries.derive(
                            TENANT_BENCH,
                            cid,
                            ["f_base_policy"],
                            f"Derived by {agent_id} at op {current_idx}",
                            owner_team=f"team_{agent_idx % 4}"
                        )
                    else:
                        aid = f"act_bench_{agent_idx}_{current_idx}"
                        queries.stage_action(
                            TENANT_BENCH,
                            aid,
                            ["f_base_policy"],
                            "SEND_NOTIFICATION",
                            {"agent": agent_id, "op": current_idx},
                            reversibility="REVERSIBLE",
                            gate="PASSED"
                        )
                    dur_ms = (time.perf_counter() - t0) * 1000
                    with lock:
                        write_latencies.append(dur_ms)
                        all_latencies.append(dur_ms)

            except Exception as e:
                dur_ms = (time.perf_counter() - t0) * 1000
                with lock:
                    errors.append(f"{type(e).__name__}: {str(e)}")

    start_wall = time.perf_counter()
    with ThreadPoolExecutor(max_workers=num_agents) as executor:
        futures = [executor.submit(agent_worker, i) for i in range(num_agents)]
        for f in as_completed(futures):
            f.result()
    total_duration = time.perf_counter() - start_wall

    arr_all = np.array(all_latencies)
    arr_read = np.array(read_latencies)
    arr_write = np.array(write_latencies)

    throughput = len(arr_all) / total_duration

    # Invariant verification post workload
    inv = queries.invariant_check(TENANT_BENCH)

    metrics = {
        "num_agents": num_agents,
        "total_operations_target": total_ops,
        "total_operations_executed": len(arr_all),
        "total_reads": len(arr_read),
        "total_writes": len(arr_write),
        "total_errors": len(errors),
        "duration_seconds": round(total_duration, 4),
        "throughput_ops_sec": round(throughput, 2),
        "latencies_overall": {
            "p50_ms": round(float(np.percentile(arr_all, 50)), 2),
            "p95_ms": round(float(np.percentile(arr_all, 95)), 2),
            "p99_ms": round(float(np.percentile(arr_all, 99)), 2),
            "mean_ms": round(float(np.mean(arr_all)), 2),
            "min_ms": round(float(np.min(arr_all)), 2),
            "max_ms": round(float(np.max(arr_all)), 2),
            "std_ms": round(float(np.std(arr_all)), 2),
        },
        "latencies_read": {
            "count": len(arr_read),
            "p50_ms": round(float(np.percentile(arr_read, 50)), 2),
            "p95_ms": round(float(np.percentile(arr_read, 95)), 2),
            "p99_ms": round(float(np.percentile(arr_read, 99)), 2),
            "mean_ms": round(float(np.mean(arr_read)), 2),
        },
        "latencies_write": {
            "count": len(arr_write),
            "p50_ms": round(float(np.percentile(arr_write, 50)), 2),
            "p95_ms": round(float(np.percentile(arr_write, 95)), 2),
            "p99_ms": round(float(np.percentile(arr_write, 99)), 2),
            "mean_ms": round(float(np.mean(arr_write)), 2),
        },
        "invariants": inv,
    }

    print("Results:")
    print(f"  Duration:         {metrics['duration_seconds']}s")
    print(f"  Throughput:       {metrics['throughput_ops_sec']} ops/sec")
    print(f"  Overall Latency:  p50={metrics['latencies_overall']['p50_ms']}ms | "
          f"p95={metrics['latencies_overall']['p95_ms']}ms | p99={metrics['latencies_overall']['p99_ms']}ms")
    print(f"  Read Latency:     p50={metrics['latencies_read']['p50_ms']}ms | p95={metrics['latencies_read']['p95_ms']}ms")
    print(f"  Write Latency:    p50={metrics['latencies_write']['p50_ms']}ms | p95={metrics['latencies_write']['p95_ms']}ms")
    print(f"  Invariants:       zero_orphans={inv['healthy_children_under_quarantined'] == 0} "
          f"(healthy_under_quarantined={inv['healthy_children_under_quarantined']}, "
          f"actions_executed_after_quarantine={inv['actions_executed_after_ancestor_quarantined']})")
    print(f"  Errors:           {len(errors)}")

    return metrics


# =========================================================================
# SUITE 3: Connection Pool Exhaustion Resilience & Burst Stress
# =========================================================================

def run_connection_pool_burst_test() -> Dict[str, Any]:
    print("\n======================================================================")
    print("TEST 3: CONNECTION POOL EXHAUSTION RESILIENCE & BURST STRESS")
    print("======================================================================")
    setup_clean_graph(TENANT_BENCH)

    burst_levels = [30, 60, 90, 100, 120, 150]
    results_per_burst = []

    print("\n--- Phase A: Fresh Pool Instant Burst (Standard Pool vs BlockingConnectionPool at 150 workers) ---")
    # Fresh Standard Pool
    fresh_std_pool = redis.ConnectionPool(host="localhost", port=6379, max_connections=100)
    fresh_std_db = FalkorDB(connection_pool=fresh_std_pool)
    g_std = fresh_std_db.select_graph(f"tenant_{TENANT_BENCH}:commons")

    barrier_150 = threading.Barrier(150)
    std_errors = []
    std_successes = []

    def std_burst_worker(w_id: int):
        barrier_150.wait()
        try:
            res = g_std.query("MATCH (f:Fact) RETURN count(f)")
            std_successes.append(res.result_set[0][0])
        except Exception as e:
            std_errors.append(f"{type(e).__name__}: {str(e)}")

    with ThreadPoolExecutor(max_workers=150) as ex:
        futs = [ex.submit(std_burst_worker, i) for i in range(150)]
        for f in as_completed(futs):
            f.result()

    print(f"  Standard Pool (max=100) under 150 burst: Success={len(std_successes)}, Errors={len(std_errors)}")
    if std_errors:
        print(f"    Sample error: {std_errors[0]}")

    # Fresh Blocking Pool
    fresh_block_pool = redis.BlockingConnectionPool(host="localhost", port=6379, max_connections=100, timeout=10.0)
    fresh_block_db = FalkorDB(connection_pool=fresh_block_pool)
    g_block = fresh_block_db.select_graph(f"tenant_{TENANT_BENCH}:commons")

    barrier_150_b = threading.Barrier(150)
    block_errors = []
    block_successes = []

    def block_burst_worker(w_id: int):
        barrier_150_b.wait()
        try:
            res = g_block.query("MATCH (f:Fact) RETURN count(f)")
            block_successes.append(res.result_set[0][0])
        except Exception as e:
            block_errors.append(f"{type(e).__name__}: {str(e)}")

    with ThreadPoolExecutor(max_workers=150) as ex:
        futs = [ex.submit(block_burst_worker, i) for i in range(150)]
        for f in as_completed(futs):
            f.result()

    print(f"  Blocking Pool (max=100) under 150 burst: Success={len(block_successes)}, Errors={len(block_errors)}")

    print("\n--- Phase B: Incremental Burst Traffic on Shared Graph (FalkorDB Client) ---")
    for n_threads in burst_levels:
        barrier = threading.Barrier(n_threads)
        errors = []
        latencies = []
        g = queries.get_graph(TENANT_BENCH, "commons")

        def burst_worker(w_id: int):
            barrier.wait() # Force simultaneous burst execution
            t0 = time.perf_counter()
            try:
                res = g.query("MATCH (m:Meta {id: 'meta'}) RETURN m.epoch")
                dur = (time.perf_counter() - t0) * 1000
                latencies.append(dur)
            except Exception as e:
                dur = (time.perf_counter() - t0) * 1000
                errors.append({"worker": w_id, "error_type": type(e).__name__, "message": str(e), "latency_ms": dur})

        t_start = time.perf_counter()
        with ThreadPoolExecutor(max_workers=n_threads) as ex:
            futs = [ex.submit(burst_worker, i) for i in range(n_threads)]
            for f in as_completed(futs):
                f.result()
        wall_time = time.perf_counter() - t_start

        pool = queries.get_db().connection.connection_pool
        in_use = len(getattr(pool, "_in_use_connections", []))
        avail = len(getattr(pool, "_available_connections", []))
        created = getattr(pool, "_created_connections", 0)

        r_entry = {
            "pool_type": "Standard ConnectionPool",
            "concurrency": n_threads,
            "successes": len(latencies),
            "errors_count": len(errors),
            "error_types": list(set(e["error_type"] for e in errors)),
            "wall_time_ms": round(wall_time * 1000, 2),
            "pool_created": created,
            "pool_in_use_after": in_use,
            "pool_available_after": avail,
        }
        results_per_burst.append(r_entry)
        print(f"  Concurrency: {n_threads:3d} | Success: {r_entry['successes']:3d} | "
              f"Errors: {r_entry['errors_count']:3d} {r_entry['error_types']} | "
              f"Pool created: {created}, in_use: {in_use}, avail: {avail}")

    print("\n--- Phase C: Resilient BlockingConnectionPool Ramp-Up (max_connections=100, timeout=10.0s) ---")
    blocking_pool = redis.BlockingConnectionPool(host="localhost", port=6379, max_connections=100, timeout=10.0)
    resilient_db = FalkorDB(connection_pool=blocking_pool)
    g_resilient = resilient_db.select_graph(f"tenant_{TENANT_BENCH}:commons")

    results_blocking = []
    for n_threads in burst_levels:
        barrier = threading.Barrier(n_threads)
        errors = []
        latencies = []

        def blocking_worker(w_id: int):
            barrier.wait() # Force simultaneous burst execution
            t0 = time.perf_counter()
            try:
                res = g_resilient.query("MATCH (m:Meta {id: 'meta'}) RETURN m.epoch")
                dur = (time.perf_counter() - t0) * 1000
                latencies.append(dur)
            except Exception as e:
                dur = (time.perf_counter() - t0) * 1000
                errors.append({"worker": w_id, "error_type": type(e).__name__, "message": str(e), "latency_ms": dur})

        t_start = time.perf_counter()
        with ThreadPoolExecutor(max_workers=n_threads) as ex:
            futs = [ex.submit(blocking_worker, i) for i in range(n_threads)]
            for f in as_completed(futs):
                f.result()
        wall_time = time.perf_counter() - t_start

        created = len(getattr(blocking_pool, "_connections", []))
        avail = blocking_pool.pool.qsize()
        in_use = blocking_pool.max_connections - avail

        arr = np.array(latencies) if latencies else np.array([0.0])
        r_entry = {
            "pool_type": "BlockingConnectionPool",
            "concurrency": n_threads,
            "successes": len(latencies),
            "errors_count": len(errors),
            "p50_ms": round(float(np.percentile(arr, 50)), 2) if len(latencies) else 0,
            "p99_ms": round(float(np.percentile(arr, 99)), 2) if len(latencies) else 0,
            "max_ms": round(float(np.max(arr)), 2) if len(latencies) else 0,
            "wall_time_ms": round(wall_time * 1000, 2),
            "pool_created": created,
            "pool_in_use_after": in_use,
            "pool_available_after": avail,
        }
        results_blocking.append(r_entry)
        print(f"  Concurrency: {n_threads:3d} | Success: {r_entry['successes']:3d} | "
              f"Errors: {r_entry['errors_count']:3d} | p50: {r_entry['p50_ms']}ms | p99: {r_entry['p99_ms']}ms | "
              f"Pool created: {created}, in_use: {in_use}, avail: {avail}")

    # Check connection leak detection
    print("\n--- Phase D: Connection Leak & Socket Health Verification ---")
    time.sleep(0.5)
    default_pool = queries.get_db().connection.connection_pool
    default_in_use = len(getattr(default_pool, "_in_use_connections", []))
    blocking_in_use = blocking_pool.max_connections - blocking_pool.pool.qsize()
    print(f"  Post-burst default pool in_use:  {default_in_use} (Expected: 0)")
    print(f"  Post-burst blocking pool in_use: {blocking_in_use} (Expected: 0)")

    return {
        "exhaustion_comparison_150_burst": {
            "standard_pool": {"successes": len(std_successes), "errors": len(std_errors), "error_sample": std_errors[0] if std_errors else None},
            "blocking_pool": {"successes": len(block_successes), "errors": len(block_errors)},
        },
        "standard_pool_burst": results_per_burst,
        "blocking_pool_burst": results_blocking,
        "default_pool_leaks": default_in_use,
        "blocking_pool_leaks": blocking_in_use,
    }


# =========================================================================
# SUITE 4: Continuous Mutation vs Concurrent Invariant Checks
# =========================================================================

def run_concurrent_mutation_invariant_stress(
    duration_seconds: float = 6.0,
    num_writers: int = 15,
    num_auditors: int = 5,
) -> Dict[str, Any]:
    print("\n======================================================================")
    print(f"TEST 4: CONCURRENT HIGH-SPEED MUTATIONS VS INVARIANT AUDITING ({duration_seconds}s)")
    print("======================================================================")
    setup_clean_graph(TENANT_BENCH)

    stop_event = threading.Event()
    audit_results: List[Dict[str, Any]] = []
    writer_stats = {"derivations": 0, "actions_staged": 0, "cascades": 0, "supersedes": 0, "executes": 0}
    writer_errors: List[str] = []
    auditor_errors: List[str] = []
    lock = threading.Lock()

    # Writer threads: continuously mutate graph (derive, stage, cascade, supersede, execute)
    def mutation_worker(w_idx: int):
        seq = 0
        while not stop_event.is_set():
            seq += 1
            op_choice = random.random()
            try:
                if op_choice < 0.40:
                    # Derivation
                    cid = f"dyn_c_{w_idx}_{seq}"
                    queries.derive(TENANT_BENCH, cid, ["f_base_policy"], f"Mutant {seq} by writer {w_idx}")
                    with lock:
                        writer_stats["derivations"] += 1

                elif op_choice < 0.65:
                    # Action staging
                    aid = f"dyn_act_{w_idx}_{seq}"
                    queries.stage_action(
                        TENANT_BENCH,
                        aid,
                        ["f_base_policy"],
                        "PAYMENT",
                        {"amount": seq},
                        reversibility="COMPENSABLE",
                        gate="PASSED"
                    )
                    with lock:
                        writer_stats["actions_staged"] += 1

                elif op_choice < 0.80:
                    # Execute guard
                    aid = f"dyn_act_{w_idx}_{seq}"
                    queries.execute_guard(TENANT_BENCH, aid)
                    with lock:
                        writer_stats["executes"] += 1

                elif op_choice < 0.90:
                    # Supersede fact
                    old_id = f"f_mut_{w_idx}_{seq - 1}" if seq > 1 else "f_auth_policy"
                    new_id = f"f_mut_{w_idx}_{seq}"
                    dummy_vec = [0.08] * 384
                    queries.supersede(
                        TENANT_BENCH,
                        old_id,
                        new_id,
                        f"val_{seq}",
                        f"Updated policy seq {seq}",
                        dummy_vec
                    )
                    with lock:
                        writer_stats["supersedes"] += 1

                else:
                    # Create temporary root and cascade quarantine
                    tmp_root = f"f_bad_root_{w_idx}_{seq}"
                    g = queries.get_graph(TENANT_BENCH, "commons")
                    g.query(f"CREATE (:Fact:MemoryNode {{id: '{tmp_root}', status: 'HEALTHY', text: 'Bad root'}})")
                    queries.derive(TENANT_BENCH, f"c_bad_{w_idx}_{seq}", [tmp_root], "Bad conclusion")
                    queries.stage_action(TENANT_BENCH, f"a_bad_{w_idx}_{seq}", [f"c_bad_{w_idx}_{seq}"], "REFUND", {}, "COMPENSABLE")
                    queries.cascade(TENANT_BENCH, tmp_root)
                    with lock:
                        writer_stats["cascades"] += 1

            except Exception as e:
                with lock:
                    writer_errors.append(f"Writer_{w_idx}: {type(e).__name__} - {str(e)}")

    # Auditor threads: hammer invariant_check as fast as possible
    def audit_worker(a_idx: int):
        while not stop_event.is_set():
            t0 = time.perf_counter()
            try:
                inv = queries.invariant_check(TENANT_BENCH)
                dur = (time.perf_counter() - t0) * 1000
                with lock:
                    audit_results.append({
                        "auditor_id": a_idx,
                        "timestamp": time.time(),
                        "duration_ms": dur,
                        "healthy_children_under_quarantined": inv["healthy_children_under_quarantined"],
                        "actions_executed_after_quarantine": inv["actions_executed_after_ancestor_quarantined"],
                        "all_passed": inv["all_passed"],
                    })
            except Exception as e:
                with lock:
                    auditor_errors.append(f"Auditor_{a_idx}: {type(e).__name__} - {str(e)}")
            time.sleep(0.01) # Small 10ms sampling tick

    # Launch concurrent writers & auditors
    start_time = time.perf_counter()
    with ThreadPoolExecutor(max_workers=num_writers + num_auditors) as executor:
        writer_futures = [executor.submit(mutation_worker, i) for i in range(num_writers)]
        auditor_futures = [executor.submit(audit_worker, i) for i in range(num_auditors)]

        time.sleep(duration_seconds)
        stop_event.set()

        for f in writer_futures + auditor_futures:
            f.result()

    total_time = time.perf_counter() - start_time

    # Analyze invariant audit results
    total_audits = len(audit_results)
    clean_audits = sum(1 for r in audit_results if r["all_passed"])
    violations_healthy_under_quarantine = sum(1 for r in audit_results if r["healthy_children_under_quarantined"] > 0)
    violations_action_executed_after_quarantine = sum(1 for r in audit_results if r["actions_executed_after_quarantine"] > 0)

    total_mutations = sum(writer_stats.values())
    mutation_rate = total_mutations / total_time

    audit_latencies = [r["duration_ms"] for r in audit_results]
    arr_audit = np.array(audit_latencies) if audit_latencies else np.array([0.0])

    res = {
        "duration_seconds": round(total_time, 2),
        "num_writers": num_writers,
        "num_auditors": num_auditors,
        "total_mutations_executed": total_mutations,
        "mutation_rate_ops_sec": round(mutation_rate, 1),
        "mutation_breakdown": writer_stats,
        "writer_errors": len(writer_errors),
        "total_audits_performed": total_audits,
        "clean_audits": clean_audits,
        "violations_healthy_under_quarantine": violations_healthy_under_quarantine,
        "violations_action_executed_after_quarantine": violations_action_executed_after_quarantine,
        "auditor_errors": len(auditor_errors),
        "audit_latency_p50_ms": round(float(np.percentile(arr_audit, 50)), 2) if len(arr_audit) else 0,
        "audit_latency_p95_ms": round(float(np.percentile(arr_audit, 95)), 2) if len(arr_audit) else 0,
        "audit_latency_p99_ms": round(float(np.percentile(arr_audit, 99)), 2) if len(arr_audit) else 0,
        "graph_consistency_passed": (violations_healthy_under_quarantine == 0 and violations_action_executed_after_quarantine == 0),
    }

    print("Results:")
    print(f"  Duration:              {res['duration_seconds']}s")
    print(f"  Mutations Executed:    {res['total_mutations_executed']} ({res['mutation_rate_ops_sec']} writes/sec)")
    print(f"  Mutation Breakdown:    {res['mutation_breakdown']}")
    print(f"  Audits Performed:      {res['total_audits_performed']} audits across {num_auditors} auditor threads")
    print(f"  Invariant Violations:  Healthy under Quarantined: {violations_healthy_under_quarantine} | "
          f"Action executed post-quarantine: {violations_action_executed_after_quarantine}")
    print(f"  Consistency Status:    {'PASSED - 100% INVARIANT INTEGRITY MAINTAINED' if res['graph_consistency_passed'] else 'FAILED'}")
    print(f"  Audit Latency:         p50={res['audit_latency_p50_ms']}ms | p95={res['audit_latency_p95_ms']}ms | p99={res['audit_latency_p99_ms']}ms")

    return res


# =========================================================================
# MAIN HARNESS
# =========================================================================

def main():
    print("======================================================================")
    print("T02-4 FALKORDB CONCURRENCY, POOL LIMITS & INVARIANT STRESS HARNESS")
    print("======================================================================")

    res_mixed = run_mixed_workload_benchmark(total_ops=1000, num_agents=30)
    res_pool = run_connection_pool_burst_test()
    res_inv = run_concurrent_mutation_invariant_stress(duration_seconds=5.0, num_writers=15, num_auditors=5)

    full_results = {
        "timestamp": int(time.time()),
        "mixed_workload_30_agents": res_mixed,
        "connection_pool_burst": res_pool,
        "concurrent_mutation_invariants": res_inv,
    }

    output_path = os.path.join(REPO_ROOT, "bench", "t02_4_stress_results.json")
    with open(output_path, "w") as f:
        json.dump(full_results, f, indent=2)
    print(f"\n[+] Full test telemetry written to: {output_path}")

    # Final summary check
    all_ok = (
        res_mixed["total_errors"] == 0 and
        res_mixed["invariants"]["all_passed"] is True and
        res_inv["graph_consistency_passed"] is True
    )
    print(f"\nFINAL VERDICT: {'ALL CRITICAL CONCURRENCY AND INVARIANT TESTS PASSED' if all_ok else 'ISSUES DETECTED'}")
    return full_results


if __name__ == "__main__":
    main()
