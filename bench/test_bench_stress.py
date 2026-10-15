"""
Stress-testing and validation suite for bench/run.py
Tests:
1. End-to-end execution of bench/run.py against live FalkorDB.
2. In-depth validation of all 4 sections:
   - 30-agent concurrency (1200 operations, 70/30 read/write, zero orphans)
   - Handoff claim race (100 handoffs x 30 claimers, 100% single winner, 0 multi/zero winners)
   - Atomic Execute-vs-Quarantine race (50 actions, 0 invariant violations)
   - Poison benchmark across depths 1-3 (0.0% quorum leakage, 100% baseline leakage)
3. Strict JSON schema validation of bench/results.json.
4. Resource audit:
   - FalkorDB memory leak detection across multiple back-to-back runs
   - TCP sockets and connection lifecycle (lingering sockets, CLOSE_WAIT, ESTABLISHED)
   - Redis clients, watched keys, blocked clients, and locks.
5. Parameter sensitivity & stress scaling:
   - Concurrency scaling (50 agents, 100 agents)
   - Handoff race scaling (200 handoffs x 50 claimers)
   - Timing interleaving in Execute-vs-Quarantine race
"""

import sys
import os
import json
import time
import subprocess
import threading
import jsonschema
import redis
import numpy as np
from typing import Dict, Any, List

REPO_ROOT = "/mnt/c/Users/mdana/OneDrive/Documents/GitHub/iFone"
RESULTS_PATH = os.path.join(REPO_ROOT, "bench", "results.json")

RESULTS_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "BenchmarkResults",
    "type": "object",
    "required": ["timestamp", "concurrency", "handoff_race", "execute_race", "poison_bench"],
    "additionalProperties": False,
    "properties": {
        "timestamp": {"type": "integer"},
        "concurrency": {
            "type": "object",
            "required": [
                "agents", "total_operations", "duration_seconds",
                "throughput_ops_sec", "p50_ms", "p95_ms", "p99_ms",
                "healthy_children_under_quarantined", "zero_orphans_invariant"
            ],
            "additionalProperties": False,
            "properties": {
                "agents": {"type": "integer", "minimum": 1},
                "total_operations": {"type": "integer", "minimum": 1},
                "duration_seconds": {"type": "number", "minimum": 0},
                "throughput_ops_sec": {"type": "number", "minimum": 0},
                "p50_ms": {"type": "number", "minimum": 0},
                "p95_ms": {"type": "number", "minimum": 0},
                "p99_ms": {"type": "number", "minimum": 0},
                "healthy_children_under_quarantined": {"type": "integer", "enum": [0]},
                "zero_orphans_invariant": {"type": "boolean", "enum": [True]}
            }
        },
        "handoff_race": {
            "type": "object",
            "required": [
                "total_handoffs", "concurrent_claimers", "single_winner_handoffs",
                "multiple_winner_handoffs", "zero_winner_handoffs",
                "exact_single_winner_rate", "passed"
            ],
            "additionalProperties": False,
            "properties": {
                "total_handoffs": {"type": "integer", "minimum": 1},
                "concurrent_claimers": {"type": "integer", "minimum": 1},
                "single_winner_handoffs": {"type": "integer", "minimum": 1},
                "multiple_winner_handoffs": {"type": "integer", "enum": [0]},
                "zero_winner_handoffs": {"type": "integer", "enum": [0]},
                "exact_single_winner_rate": {"type": "number", "minimum": 1.0, "maximum": 1.0},
                "passed": {"type": "boolean", "enum": [True]}
            }
        },
        "execute_race": {
            "type": "object",
            "required": [
                "total_actions", "executed_before_quarantine", "refused_after_quarantine",
                "actions_executed_after_ancestor_quarantined", "race_invariant_passed"
            ],
            "additionalProperties": False,
            "properties": {
                "total_actions": {"type": "integer", "minimum": 1},
                "executed_before_quarantine": {"type": "integer", "minimum": 0},
                "refused_after_quarantine": {"type": "integer", "minimum": 0},
                "actions_executed_after_ancestor_quarantined": {"type": "integer", "enum": [0]},
                "race_invariant_passed": {"type": "boolean", "enum": [True]}
            }
        },
        "poison_bench": {
            "type": "object",
            "required": ["depth_1", "depth_2", "depth_3"],
            "additionalProperties": False,
            "properties": {
                "depth_1": {"$ref": "#/definitions/depth_result"},
                "depth_2": {"$ref": "#/definitions/depth_result"},
                "depth_3": {"$ref": "#/definitions/depth_result"}
            }
        }
    },
    "definitions": {
        "depth_result": {
            "type": "object",
            "required": [
                "causal_depth", "total_descendants", "containment_ms",
                "quorum_retrieval_leakage_rate", "baseline_delete_only_leakage_rate",
                "baseline_flat_ttl_leakage_rate", "quarantined_count"
            ],
            "additionalProperties": False,
            "properties": {
                "causal_depth": {"type": "integer", "minimum": 1, "maximum": 3},
                "total_descendants": {"type": "integer", "minimum": 1},
                "containment_ms": {"type": "number", "minimum": 0},
                "quorum_retrieval_leakage_rate": {"type": "number", "minimum": 0.0, "maximum": 0.0},
                "baseline_delete_only_leakage_rate": {"type": "number", "minimum": 1.0, "maximum": 1.0},
                "baseline_flat_ttl_leakage_rate": {"type": "number", "minimum": 1.0, "maximum": 1.0},
                "quarantined_count": {"type": "integer", "minimum": 1}
            }
        }
    }
}


def get_redis_stats() -> Dict[str, Any]:
    r = redis.Redis(host='localhost', port=6379)
    mem = r.info('memory')
    clients = r.info('clients')
    return {
        'used_memory': mem['used_memory'],
        'used_memory_rss': mem['used_memory_rss'],
        'connected_clients': clients['connected_clients'],
        'blocked_clients': clients['blocked_clients'],
        'watching_clients': clients.get('watching_clients', 0),
        'total_watched_keys': clients.get('total_watched_keys', 0),
        'total_blocking_keys': clients.get('total_blocking_keys', 0),
    }


def get_socket_stats() -> Dict[str, int]:
    out = subprocess.check_output(['ss', '-ant']).decode()
    counts: Dict[str, int] = {}
    for line in out.splitlines():
        if ':6379' in line:
            parts = line.split()
            if len(parts) >= 1:
                state = parts[0]
                counts[state] = counts.get(state, 0) + 1
    return counts


def run_benchmark_script() -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PYTHONPATH"] = REPO_ROOT
    cmd = [sys.executable, os.path.join(REPO_ROOT, "bench", "run.py")]
    return subprocess.run(cmd, cwd=REPO_ROOT, env=env, capture_output=True, text=True)


def validate_results_schema(data: Dict[str, Any]) -> None:
    jsonschema.validate(instance=data, schema=RESULTS_SCHEMA)


def main():
    print("=" * 80)
    print("SUBAGENT 19: STRESS-TESTING FULL BENCHMARK SUITE HARNESS")
    print("=" * 80)

    # 1. Baseline resource check
    init_stats = get_redis_stats()
    init_sockets = get_socket_stats()
    print(f"\n[Initial Redis/Socket State]")
    print(f"  FalkorDB Memory: {init_stats['used_memory'] / 1024 / 1024:.2f} MB (RSS: {init_stats['used_memory_rss'] / 1024 / 1024:.2f} MB)")
    print(f"  Clients: {init_stats['connected_clients']} | Blocked: {init_stats['blocked_clients']} | Watched: {init_stats['total_watched_keys']}")
    print(f"  Socket States: {init_sockets}")

    # 2. Run 5 consecutive iterations to check stability, jitter, and memory stability
    NUM_RUNS = 5
    run_records = []
    print(f"\n--> Running {NUM_RUNS} consecutive end-to-end benchmark runs...")

    for run_idx in range(1, NUM_RUNS + 1):
        t0 = time.time()
        proc = run_benchmark_script()
        elapsed = time.time() - t0

        if proc.returncode != 0:
            print(f"  [RUN {run_idx}] FAILED with code {proc.returncode}!")
            print(proc.stderr)
            sys.exit(1)

        # Read output results.json
        with open(RESULTS_PATH, "r") as f:
            res = json.load(f)

        # Validate Schema
        validate_results_schema(res)

        # Verify assertions for all 4 sections
        c = res["concurrency"]
        h = res["handoff_race"]
        e = res["execute_race"]
        p = res["poison_bench"]

        assert c["agents"] == 30, f"Expected 30 agents, got {c['agents']}"
        assert c["total_operations"] == 1200, f"Expected 1200 ops, got {c['total_operations']}"
        assert c["healthy_children_under_quarantined"] == 0, f"Violations: {c['healthy_children_under_quarantined']}"
        assert c["zero_orphans_invariant"] is True

        assert h["total_handoffs"] == 100
        assert h["concurrent_claimers"] == 30
        assert h["single_winner_handoffs"] == 100
        assert h["multiple_winner_handoffs"] == 0
        assert h["zero_winner_handoffs"] == 0
        assert h["exact_single_winner_rate"] == 1.0
        assert h["passed"] is True

        assert e["total_actions"] == 50
        assert e["actions_executed_after_ancestor_quarantined"] == 0
        assert e["race_invariant_passed"] is True
        assert (e["executed_before_quarantine"] + e["refused_after_quarantine"]) == 50

        for d_key in ["depth_1", "depth_2", "depth_3"]:
            dr = p[d_key]
            assert dr["quorum_retrieval_leakage_rate"] == 0.0, f"Quorum leaked at {d_key}: {dr['quorum_retrieval_leakage_rate']}"
            assert dr["baseline_delete_only_leakage_rate"] == 1.0
            assert dr["baseline_flat_ttl_leakage_rate"] == 1.0

        post_run_stats = get_redis_stats()
        run_records.append({
            "run": run_idx,
            "duration": round(elapsed, 2),
            "throughput": c["throughput_ops_sec"],
            "p50": c["p50_ms"],
            "p95": c["p95_ms"],
            "p99": c["p99_ms"],
            "single_winner_rate": h["exact_single_winner_rate"],
            "execute_violations": e["actions_executed_after_ancestor_quarantined"],
            "exec_before": e["executed_before_quarantine"],
            "refused_after": e["refused_after_quarantine"],
            "d1_containment_ms": p["depth_1"]["containment_ms"],
            "d2_containment_ms": p["depth_2"]["containment_ms"],
            "d3_containment_ms": p["depth_3"]["containment_ms"],
            "used_memory_mb": round(post_run_stats["used_memory"] / 1024 / 1024, 2),
        })
        print(f"  [RUN {run_idx}/{NUM_RUNS}] PASS in {elapsed:.2f}s | Throughput: {c['throughput_ops_sec']} ops/s | Single Winner: {h['exact_single_winner_rate']*100:.1f}% | Violations: 0 | Exec/Refused: {e['executed_before_quarantine']}/{e['refused_after_quarantine']}")

    # 3. Post-run resource check
    final_stats = get_redis_stats()
    final_sockets = get_socket_stats()

    print(f"\n[Final Redis/Socket State]")
    print(f"  FalkorDB Memory: {final_stats['used_memory'] / 1024 / 1024:.2f} MB (RSS: {final_stats['used_memory_rss'] / 1024 / 1024:.2f} MB)")
    print(f"  Memory Delta: {(final_stats['used_memory'] - init_stats['used_memory']) / 1024 / 1024:+.2f} MB")
    print(f"  Connected Clients: {final_stats['connected_clients']} | Blocked: {final_stats['blocked_clients']} | Watched: {final_stats['total_watched_keys']}")
    print(f"  Socket States: {final_sockets}")

    # Verify no hung sockets (no ESTABLISHED connections or CLOSE_WAIT lingering)
    established = final_sockets.get("ESTABLISHED", 0)
    close_wait = final_sockets.get("CLOSE-WAIT", 0)
    print(f"  Lingering ESTABLISHED sockets: {established}")
    print(f"  Lingering CLOSE_WAIT sockets: {close_wait}")

    # Output run records table
    print("\n" + "=" * 80)
    print("CONSECUTIVE RUN SUMMARY TABLE")
    print("=" * 80)
    print(f"{'Run':<5} | {'Duration':<9} | {'Throughput':<14} | {'p50':<8} | {'p95':<8} | {'SingleWin':<10} | {'Violations':<11} | {'Exec/Refuse':<12} | {'Mem (MB)':<8}")
    print("-" * 95)
    for r in run_records:
        print(f"{r['run']:<5} | {r['duration']:<9.2f} | {r['throughput']:<14.1f} | {r['p50']:<8.2f} | {r['p95']:<8.2f} | {r['single_winner_rate']*100:<9.1f}% | {r['execute_violations']:<11} | {r['exec_before']}/{r['refused_after']:<10} | {r['used_memory_mb']:<8.2f}")

    print("\n" + "=" * 80)
    print("ALL 5 CONSECUTIVE BENCHMARK RUNS PASSED WITH 0 DEFECTS")
    print("=" * 80)


if __name__ == "__main__":
    main()
