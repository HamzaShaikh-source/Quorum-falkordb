"""
tests/test_concurrency_stress.py

Automated pytest suite for T02-4: FalkorDB concurrency throughput, connection pool limits,
and live invariant consistency under continuous concurrent mutation.
"""

import pytest
from bench.stress_test_t02_4 import (
    run_mixed_workload_benchmark,
    run_connection_pool_burst_test,
    run_concurrent_mutation_invariant_stress,
)

TENANT_TEST = "test_pytest_concurrency"


@pytest.mark.timeout(30)
def test_t02_4_concurrency_mixed_workload():
    """
    Test 1 & 2: 30 concurrent agent threads executing mixed workloads (70% read, 30% write)
    for 1000 total operations with zero errors and zero orphan invariant violations.
    """
    res = run_mixed_workload_benchmark(total_ops=1000, num_agents=30)
    assert res["total_operations_executed"] == 1000
    assert res["total_errors"] == 0
    assert res["throughput_ops_sec"] > 200.0
    assert res["latencies_overall"]["p50_ms"] < 100.0
    assert res["latencies_overall"]["p99_ms"] < 500.0
    assert res["invariants"]["healthy_children_under_quarantined"] == 0
    assert res["invariants"]["actions_executed_after_ancestor_quarantined"] == 0
    assert res["invariants"]["all_passed"] is True


@pytest.mark.timeout(30)
def test_t02_4_connection_pool_resilience():
    """
    Test 3: Connection pool exhaustion and burst resilience.
    Verifies that BlockingConnectionPool handles 150 synchronized burst threads with 0 errors
    and that connection pool returns to 0 in-use connections (no socket leaks).
    """
    res = run_connection_pool_burst_test()
    exhaustion = res["exhaustion_comparison_150_burst"]
    assert exhaustion["blocking_pool"]["errors"] == 0
    assert exhaustion["blocking_pool"]["successes"] == 150
    assert res["default_pool_leaks"] == 0
    assert res["blocking_pool_leaks"] == 0


@pytest.mark.timeout(30)
def test_t02_4_concurrent_mutation_invariants():
    """
    Test 4: Invariant consistency during continuous high-speed mutation.
    Verifies 100% graph integrity (zero healthy under quarantined, zero post-quarantine executions)
    while 15 writer threads mutate the graph at >200 writes/sec.
    """
    res = run_concurrent_mutation_invariant_stress(duration_seconds=3.0, num_writers=10, num_auditors=4)
    assert res["total_mutations_executed"] > 500
    assert res["total_audits_performed"] > 100
    assert res["violations_healthy_under_quarantine"] == 0
    assert res["violations_action_executed_after_quarantine"] == 0
    assert res["graph_consistency_passed"] is True
