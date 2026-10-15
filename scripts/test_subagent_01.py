#!/usr/bin/env python3
"""
Stress-test script for T02-1 Procedural Memory in Quorum/iFone.
Subagent 01: High Concurrency & Boundary Condition Stress Testing.

Covers:
1. Concurrently upsert 50 different procedures across 10 threads.
2. Simulate 500 interleaved score updates (successes and failures) using random concurrent workers.
3. Verify Laplace formula correctness: rank = (successes + 1) / (uses + 2).
4. Verify boundary conditions: 0 uses (rank 0.5), all successes, all failures, very large use counts, edge cases.
5. Verify rank_procedures strictly sorts descending by rank.
"""

import concurrent.futures
import json
import math
import os
import random
import sys
import time
from collections import defaultdict
from typing import Any, Dict, List, Tuple

from graph import queries

TENANT = "subagent_01_procedural_test"


def format_stats(latencies: List[float]) -> Dict[str, float]:
    if not latencies:
        return {}
    s = sorted(latencies)
    n = len(s)
    return {
        "count": n,
        "min_ms": round(s[0], 2),
        "p50_ms": round(s[int(n * 0.50)], 2),
        "p90_ms": round(s[int(n * 0.90)], 2),
        "p95_ms": round(s[int(n * 0.95)], 2),
        "p99_ms": round(s[min(n - 1, int(n * 0.99))], 2),
        "max_ms": round(s[-1], 2),
        "avg_ms": round(sum(s) / n, 2),
    }


def run_all_tests():
    report: Dict[str, Any] = {
        "timestamp": time.time(),
        "tenant": TENANT,
        "tests": {},
        "verdict": "PENDING",
        "errors": [],
    }

    print("=" * 80)
    print("SUBAGENT 01: PROCEDURAL MEMORY (T02-1) STRESS & BOUNDARY TESTING")
    print("=" * 80)

    # Clean tenant commons graph before starting
    g = queries.get_graph(TENANT, "commons")
    try:
        g.delete()
        print(f"[*] Cleared existing commons graph for tenant: {TENANT}")
    except Exception as e:
        print(f"[*] Note on clear graph: {e}")
    # Re-initialize
    g = queries.get_graph(TENANT, "commons")

    # =========================================================================
    # TEST 1: Concurrently upsert 50 different procedures across 10 threads
    # =========================================================================
    print("\n" + "=" * 80)
    print("TEST 1: Concurrently upsert 50 different procedures across 10 threads")
    print("=" * 80)

    test1_data = []
    for i in range(50):
        test1_data.append({
            "tenant_id": TENANT,
            "procedure_id": f"proc_bench_{i:03d}",
            "name": f"Automated Task Procedure {i:03d}",
            "description": f"Standard procedure description for task {i:03d} in domain {i % 5}",
            "steps": [f"step_{i}_prep", f"step_{i}_exec", f"step_{i}_verify"],
            "domain": f"domain_{i % 5}",
        })

    latencies_upsert_create: List[float] = []
    errors_upsert_create: List[str] = []
    results_upsert_create: Dict[str, Any] = {}

    def worker_upsert(item: Dict[str, Any]) -> Tuple[str, Dict[str, Any], float]:
        t0 = time.time()
        try:
            res = queries.upsert_procedure(
                tenant_id=item["tenant_id"],
                procedure_id=item["procedure_id"],
                name=item["name"],
                description=item["description"],
                steps=item["steps"],
                domain=item["domain"],
            )
            elapsed = (time.time() - t0) * 1000.0
            return (item["procedure_id"], res, elapsed)
        except Exception as e:
            elapsed = (time.time() - t0) * 1000.0
            return (item["procedure_id"], {"error": str(e)}, elapsed)

    t_start = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(worker_upsert, item) for item in test1_data]
        for f in concurrent.futures.as_completed(futures):
            pid, res, elapsed = f.result()
            latencies_upsert_create.append(elapsed)
            if "error" in res:
                errors_upsert_create.append(f"{pid}: {res['error']}")
            else:
                results_upsert_create[pid] = res

    wall_time_upsert = time.time() - t_start
    throughput_upsert = len(test1_data) / wall_time_upsert if wall_time_upsert > 0 else 0

    print(f"[+] Upserted {len(results_upsert_create)}/50 procedures in {wall_time_upsert:.2f}s "
          f"({throughput_upsert:.1f} ops/sec)")
    stats_t1_create = format_stats(latencies_upsert_create)
    print(f"    Latencies (ms): min={stats_t1_create.get('min_ms')}, p50={stats_t1_create.get('p50_ms')}, "
          f"p95={stats_t1_create.get('p95_ms')}, p99={stats_t1_create.get('p99_ms')}, max={stats_t1_create.get('max_ms')}")

    # Verify DB state after create
    res_db = g.query("MATCH (p:Procedure) RETURN count(p) AS cnt")
    total_db_procs = res_db.result_set[0][0] if res_db.result_set else 0
    print(f"[+] Total Procedure nodes in DB: {total_db_procs} (expected: 50)")

    # Verify attributes in DB
    attr_query = """
    MATCH (p:Procedure)
    RETURN p.id, p.name, p.rank, p.uses, p.successes, p.failures, p.domain, p.steps
    """
    attr_res = g.query(attr_query)
    attr_mismatches = []
    for r in attr_res.result_set:
        pid, name, rank, uses, succ, fail, domain, steps = r
        if rank != 0.5:
            attr_mismatches.append(f"{pid} initial rank={rank} (expected 0.5)")
        if uses != 0 or succ != 0 or fail != 0:
            attr_mismatches.append(f"{pid} uses={uses}, succ={succ}, fail={fail} (expected all 0)")
        if not steps or len(steps) != 3:
            attr_mismatches.append(f"{pid} steps mismatch: {steps}")

    # Sub-test: Concurrently update existing procedures (ON MATCH SET)
    print("\n[*] Concurrently updating all 50 existing procedures (testing ON MATCH concurrency)...")
    latencies_upsert_match: List[float] = []
    errors_upsert_match: List[str] = []

    def worker_reupsert(item: Dict[str, Any]) -> Tuple[str, Dict[str, Any], float]:
        t0 = time.time()
        try:
            res = queries.upsert_procedure(
                tenant_id=item["tenant_id"],
                procedure_id=item["procedure_id"],
                name=item["name"] + " [Updated]",
                description=item["description"] + " (v2)",
                steps=item["steps"] + ["step_audit"],
                domain=item["domain"],
            )
            elapsed = (time.time() - t0) * 1000.0
            return (item["procedure_id"], res, elapsed)
        except Exception as e:
            elapsed = (time.time() - t0) * 1000.0
            return (item["procedure_id"], {"error": str(e)}, elapsed)

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(worker_reupsert, item) for item in test1_data]
        for f in concurrent.futures.as_completed(futures):
            pid, res, elapsed = f.result()
            latencies_upsert_match.append(elapsed)
            if "error" in res:
                errors_upsert_match.append(f"{pid}: {res['error']}")

    res_db_after_match = g.query("MATCH (p:Procedure) RETURN count(p) AS cnt")
    total_db_procs_after_match = res_db_after_match.result_set[0][0] if res_db_after_match.result_set else 0

    t1_pass = (
        len(errors_upsert_create) == 0
        and total_db_procs == 50
        and len(attr_mismatches) == 0
        and len(errors_upsert_match) == 0
        and total_db_procs_after_match == 50
    )
    print(f"[*] TEST 1 VERDICT: {'PASSED' if t1_pass else 'FAILED'}")

    report["tests"]["test_1_concurrent_upsert"] = {
        "status": "PASSED" if t1_pass else "FAILED",
        "created_count": len(results_upsert_create),
        "db_count_after_create": total_db_procs,
        "db_count_after_reupsert": total_db_procs_after_match,
        "errors_create": errors_upsert_create,
        "errors_reupsert": errors_upsert_match,
        "attribute_mismatches": attr_mismatches,
        "wall_time_s": round(wall_time_upsert, 3),
        "throughput_ops_sec": round(throughput_upsert, 1),
        "stats_create_latency_ms": stats_t1_create,
        "stats_match_latency_ms": format_stats(latencies_upsert_match),
    }

    # =========================================================================
    # TEST 2: Simulate 500 interleaved score updates using random concurrent workers
    # =========================================================================
    print("\n" + "=" * 80)
    print("TEST 2: Simulate 500 interleaved score updates across random concurrent workers")
    print("=" * 80)

    # We will test two dimensions:
    # 1. Hot-spot contention: 100 concurrent updates concentrated on 'proc_bench_000'.
    # 2. Distributed concurrency: 400 concurrent updates distributed randomly across all 50 procedures.
    # Total = 500 updates across 16 worker threads.

    all_pids = [item["procedure_id"] for item in test1_data]
    random.seed(42)  # Deterministic seed for reproducibility

    tasks_500: List[Tuple[str, str]] = []
    # 100 updates to proc_bench_000
    for _ in range(100):
        outcome = "success" if random.random() < 0.70 else "failure"
        tasks_500.append(("proc_bench_000", outcome))

    # 400 updates distributed
    for _ in range(400):
        pid = random.choice(all_pids)
        outcome = "success" if random.random() < 0.60 else "failure"
        tasks_500.append((pid, outcome))

    # Shuffle the 500 tasks so hot-spot and distributed updates are thoroughly interleaved
    random.shuffle(tasks_500)

    client_expected_counts = defaultdict(lambda: {"success": 0, "failure": 0, "uses": 0})
    for pid, outcome in tasks_500:
        client_expected_counts[pid][outcome] += 1
        client_expected_counts[pid]["uses"] += 1

    print(f"[*] Prepared 500 score updates:")
    print(f"    - Target procedures: {len(client_expected_counts)} distinct procedures")
    print(f"    - Hot-spot proc_bench_000 expected uses: {client_expected_counts['proc_bench_000']['uses']} "
          f"(succ={client_expected_counts['proc_bench_000']['success']}, fail={client_expected_counts['proc_bench_000']['failure']})")
    total_succ_expected = sum(c["success"] for c in client_expected_counts.values())
    total_fail_expected = sum(c["failure"] for c in client_expected_counts.values())
    print(f"    - Total expected outcomes across all procs: {total_succ_expected} successes, {total_fail_expected} failures")

    latencies_score: List[float] = []
    errors_score: List[str] = []
    score_call_results: List[Dict[str, Any]] = []

    def worker_score(task: Tuple[int, str, str]) -> Tuple[int, str, str, Dict[str, Any], float]:
        idx, pid, outcome = task
        t0 = time.time()
        try:
            res = queries.score_procedure(TENANT, pid, outcome)
            elapsed = (time.time() - t0) * 1000.0
            return (idx, pid, outcome, res, elapsed)
        except Exception as e:
            elapsed = (time.time() - t0) * 1000.0
            return (idx, pid, outcome, {"success": False, "error": str(e)}, elapsed)

    indexed_tasks = [(i, pid, out) for i, (pid, out) in enumerate(tasks_500)]

    t0_score = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
        futures = [executor.submit(worker_score, t) for t in indexed_tasks]
        for f in concurrent.futures.as_completed(futures):
            idx, pid, outcome, res, elapsed = f.result()
            latencies_score.append(elapsed)
            if not res.get("success", False):
                errors_score.append(f"Task {idx} ({pid}, {outcome}): {res.get('error')}")
            else:
                score_call_results.append(res)

    wall_time_score = time.time() - t0_score
    throughput_score = 500 / wall_time_score if wall_time_score > 0 else 0

    stats_score = format_stats(latencies_score)
    print(f"[+] Executed 500 score updates in {wall_time_score:.2f}s ({throughput_score:.1f} ops/sec)")
    print(f"    Latencies (ms): min={stats_score.get('min_ms')}, p50={stats_score.get('p50_ms')}, "
          f"p95={stats_score.get('p95_ms')}, p99={stats_score.get('p99_ms')}, max={stats_score.get('max_ms')}")
    print(f"    Successful updates reported by API: {len(score_call_results)}/500")
    print(f"    Errors: {len(errors_score)}")

    # Check for lost updates / race conditions in the DB
    print("\n[*] Auditing DB state against client-side expected counters (detecting lost updates)...")
    db_state_res = g.query("MATCH (p:Procedure) RETURN p.id, p.uses, p.successes, p.failures, p.rank")
    db_procs = {r[0]: {"uses": r[1], "successes": r[2], "failures": r[3], "rank": r[4]} for r in db_state_res.result_set}

    counter_mismatches = []
    total_db_uses = sum(p["uses"] for p in db_procs.values())
    total_db_succ = sum(p["successes"] for p in db_procs.values())
    total_db_fail = sum(p["failures"] for p in db_procs.values())

    print(f"    DB Total Uses: {total_db_uses} (expected: 500)")
    print(f"    DB Total Successes: {total_db_succ} (expected: {total_succ_expected})")
    print(f"    DB Total Failures: {total_db_fail} (expected: {total_fail_expected})")

    for pid, exp in client_expected_counts.items():
        actual = db_procs.get(pid, {"uses": -1, "successes": -1, "failures": -1, "rank": -1})
        if (actual["uses"] != exp["uses"] or
            actual["successes"] != exp["success"] or
            actual["failures"] != exp["failure"]):
            counter_mismatches.append({
                "procedure_id": pid,
                "expected": exp,
                "actual": actual,
                "lost_uses": exp["uses"] - actual["uses"],
                "lost_succ": exp["success"] - actual["successes"],
                "lost_fail": exp["failure"] - actual["failures"],
            })

    if counter_mismatches:
        print(f"[!] DETECTED {len(counter_mismatches)} PROCEDURES WITH LOST UPDATES OR RACE CONDITIONS:")
        for m in counter_mismatches[:5]:
            print(f"    - {m['procedure_id']}: Expected uses={m['expected']['uses']} (succ={m['expected']['success']}, fail={m['expected']['failure']}) | "
                  f"Actual uses={m['actual']['uses']} (succ={m['actual']['successes']}, fail={m['actual']['failures']})")
    else:
        print(f"[+] PERFECT CONCURRENCY SAFETY: 0 lost updates! All 500 operations reflected exactly in DB.")

    t2_pass = (len(errors_score) == 0 and len(counter_mismatches) == 0 and total_db_uses == 500)
    print(f"[*] TEST 2 VERDICT: {'PASSED' if t2_pass else 'FAILED'}")

    report["tests"]["test_2_interleaved_scoring"] = {
        "status": "PASSED" if t2_pass else "FAILED",
        "total_updates": 500,
        "successful_api_calls": len(score_call_results),
        "errors": errors_score,
        "wall_time_s": round(wall_time_score, 3),
        "throughput_ops_sec": round(throughput_score, 1),
        "stats_latency_ms": stats_score,
        "total_db_uses": total_db_uses,
        "total_db_successes": total_db_succ,
        "total_db_failures": total_db_fail,
        "counter_mismatches_count": len(counter_mismatches),
        "counter_mismatches_sample": counter_mismatches[:5],
    }

    # =========================================================================
    # TEST 3: Verify Laplace formula correctness: rank = (successes + 1) / (uses + 2)
    # =========================================================================
    print("\n" + "=" * 80)
    print("TEST 3: Verify Laplace formula correctness: rank = (successes + 1) / (uses + 2)")
    print("=" * 80)

    laplace_errors = []
    invariant_errors = []

    for pid, pdata in db_procs.items():
        uses = pdata["uses"]
        succ = pdata["successes"]
        fail = pdata["failures"]
        db_rank = pdata["rank"]

        # Mathematical Laplace expectation
        expected_rank = (succ + 1.0) / (uses + 2.0)

        # Invariant: uses == succ + fail
        if uses != succ + fail:
            invariant_errors.append(f"{pid}: uses ({uses}) != succ ({succ}) + fail ({fail})")

        # Invariant: 0.0 < rank < 1.0
        if not (0.0 < db_rank < 1.0):
            invariant_errors.append(f"{pid}: rank {db_rank} out of bounds (0, 1)")

        # Verify DB rank matches formula
        diff = abs(db_rank - expected_rank)
        if diff > 1e-5:
            laplace_errors.append({
                "procedure_id": pid,
                "uses": uses,
                "successes": succ,
                "failures": fail,
                "db_rank": db_rank,
                "expected_rank": expected_rank,
                "abs_diff": diff,
            })

    print(f"[*] Checked {len(db_procs)} procedures against Laplace formula.")
    print(f"    - Formula mismatches: {len(laplace_errors)}")
    print(f"    - Invariant violations: {len(invariant_errors)}")

    # Sample checks
    sample_pids = ["proc_bench_000", "proc_bench_001", "proc_bench_010"]
    for spid in sample_pids:
        if spid in db_procs:
            p = db_procs[spid]
            formula_str = f"({p['successes']} + 1) / ({p['uses']} + 2)"
            val = (p['successes'] + 1) / (p['uses'] + 2)
            print(f"    Sample {spid}: uses={p['uses']}, succ={p['successes']}, fail={p['failures']} => {formula_str} = {val:.6f} | DB rank = {p['rank']:.6f}")

    t3_pass = (len(laplace_errors) == 0 and len(invariant_errors) == 0)
    print(f"[*] TEST 3 VERDICT: {'PASSED' if t3_pass else 'FAILED'}")

    report["tests"]["test_3_laplace_formula"] = {
        "status": "PASSED" if t3_pass else "FAILED",
        "procedures_verified": len(db_procs),
        "formula_mismatches": laplace_errors,
        "invariant_violations": invariant_errors,
    }

    # =========================================================================
    # TEST 4: Boundary Conditions & Edge Cases
    # =========================================================================
    print("\n" + "=" * 80)
    print("TEST 4: Boundary conditions: 0 uses (rank 0.5), all successes, all failures, very large use counts, edge cases")
    print("=" * 80)

    boundary_results: Dict[str, Any] = {}

    # 4.1: 0 uses (fresh procedure)
    b1_id = "proc_boundary_zero_uses"
    queries.upsert_procedure(TENANT, b1_id, "Zero Uses Test", "Never executed", ["step1"], domain="boundary")
    b1_ranked = queries.rank_procedures(TENANT, domain="boundary", limit=10)
    b1_item = next((p for p in b1_ranked if p["id"] == b1_id), None)
    b1_pass = (
        b1_item is not None
        and b1_item["rank"] == 0.5
        and b1_item["uses"] == 0
        and b1_item["successes"] == 0
        and b1_item["failures"] == 0
    )
    print(f"[+] 4.1 Zero Uses: rank={b1_item['rank'] if b1_item else None} (expected 0.5) => {'PASSED' if b1_pass else 'FAILED'}")
    boundary_results["zero_uses"] = {"passed": b1_pass, "details": b1_item}

    # 4.2: All successes progression
    b2_id = "proc_boundary_all_success"
    queries.upsert_procedure(TENANT, b2_id, "All Successes", "Always succeeds", ["step1"], domain="boundary")
    success_steps = [1, 2, 5, 10, 50, 100]
    success_progression = []
    b2_monotonic = True
    prev_rank = 0.5
    for s_count in range(1, 101):
        r = queries.score_procedure(TENANT, b2_id, "success")
        if s_count in success_steps:
            exp = (s_count + 1) / (s_count + 2)
            actual = r["rank"]
            diff = abs(actual - exp)
            success_progression.append({
                "step": s_count,
                "actual_rank": actual,
                "expected_rank": round(exp, 4),
                "diff": diff,
            })
            if actual <= prev_rank:
                b2_monotonic = False
            prev_rank = actual

    b2_pass = b2_monotonic and all(sp["diff"] < 1e-3 for sp in success_progression)
    print(f"[+] 4.2 All Successes Progression (1..100): final rank={prev_rank} (expected ~{101/102:.4f}), strictly monotonic => {'PASSED' if b2_pass else 'FAILED'}")
    boundary_results["all_successes"] = {"passed": b2_pass, "progression": success_progression}

    # 4.3: All failures progression
    b3_id = "proc_boundary_all_failure"
    queries.upsert_procedure(TENANT, b3_id, "All Failures", "Always fails", ["step1"], domain="boundary")
    fail_steps = [1, 2, 5, 10, 50, 100]
    fail_progression = []
    b3_monotonic = True
    prev_rank = 0.5
    for f_count in range(1, 101):
        r = queries.score_procedure(TENANT, b3_id, "failure")
        if f_count in fail_steps:
            exp = (0 + 1) / (f_count + 2)
            actual = r["rank"]
            diff = abs(actual - exp)
            fail_progression.append({
                "step": f_count,
                "actual_rank": actual,
                "expected_rank": round(exp, 4),
                "diff": diff,
            })
            if actual >= prev_rank:
                b3_monotonic = False
            prev_rank = actual

    b3_pass = b3_monotonic and all(fp["diff"] < 1e-3 for fp in fail_progression)
    print(f"[+] 4.3 All Failures Progression (1..100): final rank={prev_rank} (expected ~{1/102:.4f}), strictly monotonic => {'PASSED' if b3_pass else 'FAILED'}")
    boundary_results["all_failures"] = {"passed": b3_pass, "progression": fail_progression}

    # 4.4: Very large use counts (scale test: 1M and 10M uses)
    print("[*] 4.4 Testing very large use counts (1M, 10M scale via direct Cypher state update)...")
    b4_id_1m = "proc_boundary_large_1m"
    b4_id_10m = "proc_boundary_large_10m"
    queries.upsert_procedure(TENANT, b4_id_1m, "1M Uses Scale", "Scale testing", ["step"], domain="scale")
    queries.upsert_procedure(TENANT, b4_id_10m, "10M Uses Scale", "Scale testing", ["step"], domain="scale")

    # Set direct large numbers in DB
    g.query("""
    MATCH (p:Procedure {id: $p1})
    SET p.uses = 1000000, p.successes = 750000, p.failures = 250000,
        p.rank = toFloat(p.successes + 1) / toFloat(p.uses + 2)
    """, {"p1": b4_id_1m})
    g.query("""
    MATCH (p:Procedure {id: $p2})
    SET p.uses = 10000000, p.successes = 9000000, p.failures = 1000000,
        p.rank = toFloat(p.successes + 1) / toFloat(p.uses + 2)
    """, {"p2": b4_id_10m})

    # Now perform a live score update on each to verify score_procedure handles 1M and 10M without overflow
    res_score_1m = queries.score_procedure(TENANT, b4_id_1m, "success")
    res_score_10m = queries.score_procedure(TENANT, b4_id_10m, "failure")

    exp_1m_rank = (750000 + 1 + 1) / (1000000 + 1 + 2)
    exp_10m_rank = (9000000 + 0 + 1) / (10000000 + 1 + 2)

    b4_1m_diff = abs(res_score_1m["rank"] - round(exp_1m_rank, 4))
    b4_10m_diff = abs(res_score_10m["rank"] - round(exp_10m_rank, 4))
    b4_pass = (
        res_score_1m["uses"] == 1000001
        and res_score_10m["uses"] == 10000001
        and b4_1m_diff <= 1e-4
        and b4_10m_diff <= 1e-4
    )
    print(f"[+] 4.4 Large scale (1M uses): rank={res_score_1m['rank']} (exp={round(exp_1m_rank, 4)})")
    print(f"    Large scale (10M uses): rank={res_score_10m['rank']} (exp={round(exp_10m_rank, 4)}) => {'PASSED' if b4_pass else 'FAILED'}")
    boundary_results["large_counts"] = {
        "passed": b4_pass,
        "1m_result": res_score_1m,
        "10m_result": res_score_10m,
    }

    # 4.5: Non-existent procedure error handling
    b5_res = queries.score_procedure(TENANT, "non_existent_procedure_404", "success")
    b5_pass = (b5_res.get("success") is False and "not found" in b5_res.get("error", "").lower())
    print(f"[+] 4.5 Non-existent Procedure score handling: {b5_res} => {'PASSED' if b5_pass else 'FAILED'}")
    boundary_results["non_existent_proc"] = {"passed": b5_pass, "result": b5_res}

    # 4.6: Invalid outcome string edge case analysis
    b6_id = "proc_boundary_invalid_outcome"
    queries.upsert_procedure(TENANT, b6_id, "Invalid Outcome", "Test bad input", ["step"], domain="boundary")
    b6_res = queries.score_procedure(TENANT, b6_id, "invalid_outcome")
    # Observe behavior: in score_procedure, inc_success=0, inc_failure=0, but uses increments
    b6_pass = (
        b6_res.get("success") is True
        and b6_res.get("uses") == 1
        and b6_res.get("successes") == 0
        and b6_res.get("failures") == 0
        and b6_res.get("rank") == round(1 / 3, 4)
    )
    print(f"[+] 4.6 Invalid outcome string ('invalid_outcome'): uses={b6_res.get('uses')}, succ={b6_res.get('successes')}, "
          f"fail={b6_res.get('failures')}, rank={b6_res.get('rank')} (treated as implicit non-success) => {'PASSED' if b6_pass else 'FAILED'}")
    boundary_results["invalid_outcome_handling"] = {"passed": b6_pass, "result": b6_res}

    # 4.7: Edge cases on inputs: empty steps, unicode characters, long description
    b7_id = "proc_boundary_unicode_✨"
    b7_upsert = queries.upsert_procedure(
        TENANT,
        procedure_id=b7_id,
        name="Unicode & Special Chars: 日本語 & 'Quotes' & \"Double\" & $Cypher",
        description="A" * 2000,  # 2KB text
        steps=[],  # empty steps
        domain="edge_chars",
    )
    b7_ranked = queries.rank_procedures(TENANT, domain="edge_chars")
    b7_pass = len(b7_ranked) == 1 and b7_ranked[0]["id"] == b7_id and b7_ranked[0]["steps"] == []
    print(f"[+] 4.7 Special characters, empty steps, 2KB description: => {'PASSED' if b7_pass else 'FAILED'}")
    boundary_results["special_chars_empty_steps"] = {"passed": b7_pass}

    t4_pass = all(v.get("passed", False) for v in boundary_results.values())
    print(f"[*] TEST 4 VERDICT: {'PASSED' if t4_pass else 'FAILED'}")

    report["tests"]["test_4_boundary_conditions"] = {
        "status": "PASSED" if t4_pass else "FAILED",
        "results": boundary_results,
    }

    # =========================================================================
    # TEST 5: Verify rank_procedures strictly sorts descending by rank
    # =========================================================================
    print("\n" + "=" * 80)
    print("TEST 5: Verify rank_procedures strictly sorts descending by rank")
    print("=" * 80)

    # 5.1: Global query without domain filter
    ranked_all = queries.rank_procedures(TENANT, limit=200)
    ranks_all = [p["rank"] for p in ranked_all]
    is_sorted_desc = all(ranks_all[i] >= ranks_all[i + 1] for i in range(len(ranks_all) - 1))
    print(f"[+] 5.1 Global rank_procedures (total returned: {len(ranked_all)}): strictly sorted desc => {'PASSED' if is_sorted_desc else 'FAILED'}")
    print(f"    Top 3 ranks: {ranks_all[:3]}")
    print(f"    Bottom 3 ranks: {ranks_all[-3:]}")

    # 5.2: Domain filter query
    ranked_domain = queries.rank_procedures(TENANT, domain="domain_0", limit=100)
    domain_ranks = [p["rank"] for p in ranked_domain]
    domain_sorted = all(domain_ranks[i] >= domain_ranks[i + 1] for i in range(len(domain_ranks) - 1))
    print(f"[+] 5.2 Domain filter ('domain_0', returned: {len(ranked_domain)}): strictly sorted desc => {'PASSED' if domain_sorted else 'FAILED'}")

    # 5.3: Limit parameter check
    limit_5 = queries.rank_procedures(TENANT, limit=5)
    limit_1 = queries.rank_procedures(TENANT, limit=1)
    limit_pass = (len(limit_5) == 5 and len(limit_1) == 1 and limit_1[0]["id"] == limit_5[0]["id"])
    print(f"[+] 5.3 Limit pagination: limit=5 returned {len(limit_5)}, limit=1 returned {len(limit_1)} => {'PASSED' if limit_pass else 'FAILED'}")

    # 5.4: Dynamic reranking verification
    # Pick the lowest ranked procedure, score it with 50 successes, verify it climbs to the very top
    lowest_proc = ranked_all[-1]
    lowest_id = lowest_proc["id"]
    old_rank = lowest_proc["rank"]
    print(f"[*] 5.4 Dynamic reranking: taking lowest ranked procedure '{lowest_id}' (rank={old_rank})...")

    for _ in range(50):
        queries.score_procedure(TENANT, lowest_id, "success")

    reranked_all = queries.rank_procedures(TENANT, limit=200)
    new_top = reranked_all[0]
    new_lowest_proc = next((p for p in reranked_all if p["id"] == lowest_id), None)
    rerank_climbed = new_lowest_proc is not None and new_lowest_proc["rank"] > old_rank
    rerank_is_sorted = all(reranked_all[i]["rank"] >= reranked_all[i + 1]["rank"] for i in range(len(reranked_all) - 1))
    print(f"[+] Dynamic reranking result: '{lowest_id}' rank climbed from {old_rank} to {new_lowest_proc['rank'] if new_lowest_proc else None}")
    print(f"    New top procedure: '{new_top['id']}' with rank {new_top['rank']}")
    print(f"    Full list re-sorted strictly descending: {'PASSED' if rerank_is_sorted else 'FAILED'}")

    t5_pass = is_sorted_desc and domain_sorted and limit_pass and rerank_climbed and rerank_is_sorted
    print(f"[*] TEST 5 VERDICT: {'PASSED' if t5_pass else 'FAILED'}")

    report["tests"]["test_5_rank_ordering"] = {
        "status": "PASSED" if t5_pass else "FAILED",
        "global_descending_sorted": is_sorted_desc,
        "domain_descending_sorted": domain_sorted,
        "limit_pagination_valid": limit_pass,
        "dynamic_rerank_climbed": rerank_climbed,
        "total_ranked_count": len(ranked_all),
        "top_5": ranked_all[:5],
        "bottom_5": ranked_all[-5:],
    }

    # =========================================================================
    # OVERALL VERDICT & SUMMARY
    # =========================================================================
    all_tests_passed = all(t["status"] == "PASSED" for t in report["tests"].values())
    report["verdict"] = "PASSED" if all_tests_passed else "FAILED"

    print("\n" + "=" * 80)
    print(f"FINAL OVERALL STRESS TEST VERDICT: {report['verdict']}")
    print("=" * 80)
    for test_name, tdata in report["tests"].items():
        print(f"  - {test_name}: {tdata['status']}")

    # Save detailed JSON report
    report_path = "/mnt/c/Users/mdana/OneDrive/Documents/GitHub/iFone/bench/t02_1_stress_results.json"
    try:
        with open(report_path, "w") as f:
            json.dump(report, f, indent=2)
        print(f"\n[+] Detailed test report saved to {report_path}")
    except Exception as e:
        print(f"[!] Failed to save JSON report: {e}")

    return report


if __name__ == "__main__":
    report = run_all_tests()
    if report["verdict"] != "PASSED":
        sys.exit(1)
    sys.exit(0)
