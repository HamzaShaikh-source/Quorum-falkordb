"""
Stress-testing Quarantine Red Wave Cascading Invalidation (cascade)
Subagent 13 Test Harness
"""

import time
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any, List, Tuple
import numpy as np

from graph import queries

# Setup logging
logging.basicConfig(level=logging.WARNING)


def build_dag_cypher(prefix: str, root_id: str, num_c: int = 5, num_d: int = 5, num_a: int = 4) -> str:
    """
    Builds Cypher statements to create a DAG:
    1 root fact -> num_c conclusions -> (num_c * num_d) decisions -> (num_c * num_d * num_a) actions.
    Default: 1 root, 5 conclusions, 25 decisions, 100 actions = 131 nodes.
    """
    cypher = f"""
    CREATE (f:Fact:MemoryNode {{id: '{root_id}', status: 'HEALTHY', text: 'Root Fact {root_id}', created_at: timestamp()}})
    WITH f
    UNWIND range(1, {num_c}) AS c_idx
    CREATE (c:Conclusion:MemoryNode {{
        id: '{prefix}_c_' + toString(c_idx),
        status: 'HEALTHY',
        text: 'Conclusion ' + toString(c_idx),
        stale: false,
        created_at: timestamp()
    }})
    CREATE (f)-[:LEADS_TO]->(c)
    WITH c, c_idx
    UNWIND range(1, {num_d}) AS d_idx
    CREATE (d:Conclusion:Decision:MemoryNode {{
        id: '{prefix}_d_' + toString(c_idx) + '_' + toString(d_idx),
        status: 'HEALTHY',
        text: 'Decision ' + toString(d_idx),
        stale: false,
        owner_team: 'RiskOps',
        created_at: timestamp()
    }})
    CREATE (c)-[:LEADS_TO]->(d)
    WITH d, c_idx, d_idx
    UNWIND range(1, {num_a}) AS a_idx
    CREATE (a:Action:MemoryNode {{
        id: '{prefix}_a_' + toString(c_idx) + '_' + toString(d_idx) + '_' + toString(a_idx),
        status: 'HEALTHY',
        type: 'REFUND',
        payload: '{{"amount": 100}}',
        executed: false,
        executed_at: null,
        confirmed: false,
        gate: 'PASSED',
        reversibility: 'COMPENSABLE',
        idempotency_key: 'idem_' + '{prefix}_a_' + toString(c_idx) + '_' + toString(d_idx) + '_' + toString(a_idx),
        stale: false,
        created_at: timestamp()
    }})
    CREATE (d)-[:LEADS_TO]->(a)
    """
    return cypher


# =========================================================================
# TEST 1 & 2 & 3: SINGLE 131-NODE DAG CASCADING QUARANTINE
# =========================================================================
def run_single_dag_test(num_trials: int = 10) -> Dict[str, Any]:
    print("=" * 70)
    print("TEST 1, 2 and 3: Deep & Wide DAG Cascading Quarantine (131 Nodes)")
    print("=" * 70)
    tenant = "test_single_dag"
    trial_latencies_containment = []
    trial_latencies_roundtrip = []

    # Detailed inspection on trial 0
    g = queries.get_graph(tenant, "commons")
    try:
        g.delete()
    except Exception:
        pass
    queries.ensure_indexes(tenant)

    # 1. Construct DAG with 20 executed actions and 80 unexecuted actions
    root_id = "f_root_main"
    dag_cypher = build_dag_cypher("t0", root_id, num_c=5, num_d=5, num_a=4)
    g.query(dag_cypher)

    # Mark 20 actions as executed
    g.query("""
    MATCH (a:Action)
    WHERE a.id STARTS WITH 't0_a_1_'
    SET a.executed = true, a.executed_at = timestamp(), a.confirmed = true
    """)

    # Verify counts pre-quarantine
    pre_counts = g.query("""
    MATCH (m:MemoryNode)
    RETURN count(m) AS total,
           count(CASE WHEN 'Fact' IN labels(m) THEN 1 END) AS facts,
           count(CASE WHEN 'Conclusion' IN labels(m) AND NOT 'Decision' IN labels(m) THEN 1 END) AS conclusions,
           count(CASE WHEN 'Decision' IN labels(m) THEN 1 END) AS decisions,
           count(CASE WHEN 'Action' IN labels(m) THEN 1 END) AS actions,
           count(CASE WHEN 'Action' IN labels(m) AND m.executed = true THEN 1 END) AS executed_actions,
           count(CASE WHEN m.status = 'HEALTHY' THEN 1 END) AS healthy_nodes
    """).result_set[0]

    print(f"Pre-quarantine Graph Structure:")
    print(f"  Total nodes: {pre_counts[0]} (Facts: {pre_counts[1]}, Conclusions: {pre_counts[2]}, Decisions: {pre_counts[3]}, Actions: {pre_counts[4]})")
    print(f"  Pre-executed actions: {pre_counts[5]}, Healthy nodes: {pre_counts[6]}")

    # Execute cascade
    t0 = time.time()
    casc_res = queries.cascade(tenant, root_id)
    t_roundtrip = (time.time() - t0) * 1000

    print(f"\nCascade Result:")
    print(f"  Success: {casc_res.get('success')}")
    print(f"  Containment Latency (DB internal): {casc_res.get('ms_to_containment')} ms")
    print(f"  Total Round-Trip Latency: {t_roundtrip:.2f} ms")
    print(f"  Affected Count: {casc_res.get('affected_count')}")
    print(f"  Depth breakdown: { {k: len(v) for k, v in casc_res.get('node_ids_by_depth', {}).items()} }")
    print(f"  Identified executed actions for compensation: {len(casc_res.get('executed_actions', []))}")

    # Invariant and DB checks
    post_counts = g.query("""
    MATCH (m:MemoryNode)
    RETURN count(m) AS total,
           count(CASE WHEN m.status = 'QUARANTINED' THEN 1 END) AS quarantined_nodes,
           count(CASE WHEN m.quarantine_root = $root_id THEN 1 END) AS matching_root,
           count(CASE WHEN m.quarantined_at IS NOT NULL THEN 1 END) AS stamped_nodes
    """, {"root_id": root_id}).result_set[0]

    inv = queries.invariant_check(tenant)
    print(f"\nPost-quarantine DB State:")
    print(f"  Total nodes: {post_counts[0]}")
    print(f"  Quarantined nodes: {post_counts[1]} (Expected: 131)")
    print(f"  Matching quarantine_root: {post_counts[2]} (Expected: 131)")
    print(f"  Timestamped: {post_counts[3]} (Expected: 131)")
    print(f"  Invariant Check: {inv}")

    # Test Compensation creation for the 20 executed actions
    comps = queries.create_compensations(tenant, casc_res.get('executed_actions', []))
    print(f"  Created Compensations: {len(comps)} (Expected: 20)")

    # Test execute_guard rejection on unexecuted actions
    refusal_checks = []
    for c in range(2, 6):
        aid = f"t0_a_{c}_1_1"
        res_guard = queries.execute_guard(tenant, aid)
        refusal_checks.append(not res_guard.get("success"))
    all_refused = all(refusal_checks)
    print(f"  Execute guard on staged actions refused: {all_refused} ({len(refusal_checks)} sampled)")

    # Run remaining trials for robust latency benchmarks
    print(f"\nRunning {num_trials} benchmark trials for latency distribution...")
    for tr in range(num_trials):
        g_trial = queries.get_graph(f"trial_tenant_{tr}", "commons")
        try:
            g_trial.delete()
        except Exception:
            pass
        queries.ensure_indexes(f"trial_tenant_{tr}")
        g_trial.query(build_dag_cypher(f"tr_{tr}", f"f_root_{tr}"))

        t_tr0 = time.time()
        res_tr = queries.cascade(f"trial_tenant_{tr}", f"f_root_{tr}")
        t_tr_rt = (time.time() - t_tr0) * 1000

        trial_latencies_containment.append(res_tr["ms_to_containment"])
        trial_latencies_roundtrip.append(t_tr_rt)

        # Cleanup trial tenant
        try:
            g_trial.delete()
        except Exception:
            pass

    arr_c = np.array(trial_latencies_containment)
    arr_rt = np.array(trial_latencies_roundtrip)

    stats = {
        "trials": num_trials,
        "containment_latency": {
            "mean_ms": round(float(np.mean(arr_c)), 2),
            "median_ms": round(float(np.median(arr_c)), 2),
            "p95_ms": round(float(np.percentile(arr_c, 95)), 2),
            "min_ms": round(float(np.min(arr_c)), 2),
            "max_ms": round(float(np.max(arr_c)), 2),
            "std_ms": round(float(np.std(arr_c)), 2),
        },
        "roundtrip_latency": {
            "mean_ms": round(float(np.mean(arr_rt)), 2),
            "median_ms": round(float(np.median(arr_rt)), 2),
            "p95_ms": round(float(np.percentile(arr_rt, 95)), 2),
            "min_ms": round(float(np.min(arr_rt)), 2),
            "max_ms": round(float(np.max(arr_rt)), 2),
            "std_ms": round(float(np.std(arr_rt)), 2),
        },
        "containment_integrity": {
            "nodes_quarantined": post_counts[1],
            "expected_nodes": 131,
            "all_131_quarantined": bool(post_counts[1] == 131 and casc_res.get("affected_count") == 131),
            "invariants_passed": inv["all_passed"],
            "compensations_created": len(comps),
            "unexecuted_actions_blocked": all_refused
        }
    }
    print(f"Containment Latency Stats across {num_trials} runs: mean={stats['containment_latency']['mean_ms']}ms, p95={stats['containment_latency']['p95_ms']}ms, min={stats['containment_latency']['min_ms']}ms, max={stats['containment_latency']['max_ms']}ms")
    return stats


# =========================================================================
# TEST 4: CONTAINMENT PRECISION & PARALLEL BRANCHES
# =========================================================================
def run_containment_precision_test() -> Dict[str, Any]:
    print("\n" + "=" * 70)
    print("TEST 4: Containment Precision (Unaffected Parallel Branches)")
    print("=" * 70)
    tenant = "test_precision"
    g = queries.get_graph(tenant, "commons")
    try:
        g.delete()
    except Exception:
        pass
    queries.ensure_indexes(tenant)

    # 1. Create Tree A (Target) and Tree B (Parallel Independent) in the SAME tenant
    print("Constructing Tree A (Target, 131 nodes) and Tree B (Parallel Unaffected, 131 nodes)...")
    g.query(build_dag_cypher("treeA", "f_root_A", num_c=5, num_d=5, num_a=4))
    g.query(build_dag_cypher("treeB", "f_root_B", num_c=5, num_d=5, num_a=4))

    # Verify 262 nodes total
    total_nodes = g.query("MATCH (m:MemoryNode) RETURN count(m)").result_set[0][0]
    print(f"Total initial nodes in commons: {total_nodes} (131 in Tree A, 131 in Tree B)")

    # Invalidate Tree A only
    print("Invalidating Tree A root ('f_root_A') with cascade...")
    t0 = time.time()
    casc_res = queries.cascade(tenant, "f_root_A")
    containment_ms = (time.time() - t0) * 1000

    # Inspect statuses of Tree A vs Tree B
    treeA_status = g.query("""
    MATCH (m:MemoryNode)
    WHERE m.id STARTS WITH 'treeA' OR m.id = 'f_root_A'
    RETURN count(m) AS total,
           count(CASE WHEN m.status = 'QUARANTINED' THEN 1 END) AS quarantined,
           count(CASE WHEN m.status = 'HEALTHY' THEN 1 END) AS healthy
    """).result_set[0]

    treeB_status = g.query("""
    MATCH (m:MemoryNode)
    WHERE m.id STARTS WITH 'treeB' OR m.id = 'f_root_B'
    RETURN count(m) AS total,
           count(CASE WHEN m.status = 'QUARANTINED' THEN 1 END) AS quarantined,
           count(CASE WHEN m.status = 'HEALTHY' THEN 1 END) AS healthy
    """).result_set[0]

    tp = treeA_status[1] # True Positives: Quarantined in Tree A (131)
    fn = treeA_status[2] # False Negatives: Healthy in Tree A (0)
    fp = treeB_status[1] # False Positives: Quarantined in Tree B (0)
    tn = treeB_status[2] # True Negatives: Healthy in Tree B (131)

    precision = float(tp / (tp + fp)) if (tp + fp) > 0 else 0.0
    recall = float(tp / (tp + fn)) if (tp + fn) > 0 else 0.0
    specificity = float(tn / (tn + fp)) if (tn + fp) > 0 else 0.0

    print(f"Results for Parallel Tree Isolation:")
    print(f"  Tree A (Target): Total={treeA_status[0]}, Quarantined={treeA_status[1]}, Healthy={treeA_status[2]}")
    print(f"  Tree B (Control): Total={treeB_status[0]}, Quarantined={treeB_status[1]}, Healthy={treeB_status[2]}")
    print(f"  Confusion Matrix: TP={tp}, FP={fp}, TN={tn}, FN={fn}")
    print(f"  Precision: {precision * 100:.2f}%, Recall: {recall * 100:.2f}%, Specificity: {specificity * 100:.2f}%")

    # Scenario B: Shared Root with Forked Branches
    print("\nTesting Shared Root Fork (Root Fact -> Branch A & Branch B)...")
    tenant_fork = "test_precision_fork"
    g_fork = queries.get_graph(tenant_fork, "commons")
    try:
        g_fork.delete()
    except Exception:
        pass
    queries.ensure_indexes(tenant_fork)

    # Common root policy -> Conclusion A & Conclusion B -> Decisions -> Actions
    g_fork.query("""
    CREATE (f_shared:Fact:MemoryNode {id: 'f_shared', status: 'HEALTHY', text: 'Shared Global Policy'})
    CREATE (f_subA:Fact:MemoryNode {id: 'f_subA', status: 'HEALTHY', text: 'Subsystem A Fact'})
    CREATE (f_subB:Fact:MemoryNode {id: 'f_subB', status: 'HEALTHY', text: 'Subsystem B Fact'})
    CREATE (f_shared)-[:LEADS_TO]->(f_subA)
    CREATE (f_shared)-[:LEADS_TO]->(f_subB)
    WITH f_subA, f_subB
    UNWIND range(1, 10) AS i
    CREATE (cA:Conclusion:MemoryNode {id: 'cA_' + toString(i), status: 'HEALTHY', text: 'Conclusion A ' + toString(i)})
    CREATE (cB:Conclusion:MemoryNode {id: 'cB_' + toString(i), status: 'HEALTHY', text: 'Conclusion B ' + toString(i)})
    CREATE (f_subA)-[:LEADS_TO]->(cA)
    CREATE (f_subB)-[:LEADS_TO]->(cB)
    """)

    # Invalidate f_subA (subsystem A)
    casc_fork_res = queries.cascade(tenant_fork, "f_subA")
    print(f"  Cascade on Subsystem Fact 'f_subA': affected_count={casc_fork_res.get('affected_count')}")

    fork_audit = g_fork.query("""
    MATCH (m:MemoryNode)
    RETURN m.id, m.status, m.quarantine_root
    ORDER BY m.id
    """).result_set

    subA_quarantined = all(r[1] == 'QUARANTINED' for r in fork_audit if 'A' in r[0])
    shared_healthy = any(r[0] == 'f_shared' and r[1] == 'HEALTHY' for r in fork_audit)
    subB_healthy = all(r[1] == 'HEALTHY' for r in fork_audit if 'B' in r[0])
    print(f"  Fork Verification: Subsystem A Quarantined={subA_quarantined}, Shared Root Healthy={shared_healthy}, Subsystem B Healthy={subB_healthy}")

    inv = queries.invariant_check(tenant)
    return {
        "tree_a_quarantined": tp,
        "tree_b_healthy": tn,
        "false_positives": fp,
        "precision": precision,
        "recall": recall,
        "specificity": specificity,
        "containment_ms": round(containment_ms, 2),
        "invariants_passed": inv["all_passed"],
        "fork_subsystem_isolated": bool(subA_quarantined and shared_healthy and subB_healthy)
    }


# =========================================================================
# TEST 5: STRESS TEST WITH 50 SIMULTANEOUS CASCADES
# =========================================================================
def run_50_concurrent_cascades_test() -> Dict[str, Any]:
    print("\n" + "=" * 70)
    print("TEST 5: Stress Test with 50 Simultaneous Cascading Quarantines")
    print("=" * 70)
    tenant = "test_stress_50_runner"
    g = queries.get_graph(tenant, "commons")
    try:
        g.delete()
    except Exception:
        pass
    queries.ensure_indexes(tenant)

    num_trees = 50
    nodes_per_tree = 131
    total_expected_nodes = num_trees * nodes_per_tree # 6,550 nodes

    print(f"Generating {num_trees} distinct DAGs ({total_expected_nodes} total nodes)... ")
    t_gen0 = time.time()
    for i in range(num_trees):
        cypher = build_dag_cypher(f"tree_{i}", f"f_root_{i}", num_c=5, num_d=5, num_a=4)
        g.query(cypher)
    t_gen = time.time() - t_gen0
    print(f"All {num_trees} DAGs loaded in {t_gen:.2f}s")

    # Verify initial state
    res_init = g.query("MATCH (m:MemoryNode) RETURN count(m), count(CASE WHEN m.status = 'HEALTHY' THEN 1 END)").result_set[0]
    print(f"Database verified: {res_init[0]} nodes total, {res_init[1]} HEALTHY.")

    # Launch 50 simultaneous cascading quarantines
    print(f"Launching {num_trees} concurrent worker threads simultaneously...")
    latencies_containment_ms = []
    latencies_roundtrip_ms = []
    thread_results = []
    errors = []

    t_wall_start = time.time()

    def cascade_worker(tree_idx: int):
        rid = f"f_root_{tree_idx}"
        t0 = time.time()
        try:
            res = queries.cascade(tenant, rid)
            rt_ms = (time.time() - t0) * 1000
            return (tree_idx, res, rt_ms, None)
        except Exception as ex:
            rt_ms = (time.time() - t0) * 1000
            return (tree_idx, None, rt_ms, str(ex))

    with ThreadPoolExecutor(max_workers=50) as executor:
        futures = [executor.submit(cascade_worker, idx) for idx in range(num_trees)]
        for f in as_completed(futures):
            t_idx, res, rt_ms, err = f.result()
            latencies_roundtrip_ms.append(rt_ms)
            if err:
                errors.append((t_idx, err))
            else:
                latencies_containment_ms.append(res.get("ms_to_containment", 0))
                thread_results.append((t_idx, res))

    t_wall_total = (time.time() - t_wall_start) * 1000 # ms
    wall_sec = t_wall_total / 1000.0

    print(f"Concurrency Execution Completed:")
    print(f"  Wall-clock Duration: {t_wall_total:.2f} ms ({wall_sec:.3f} s)")
    print(f"  Success: {len(thread_results)} / {num_trees}")
    print(f"  Errors: {len(errors)}")

    arr_rt = np.array(latencies_roundtrip_ms)
    arr_c = np.array(latencies_containment_ms)

    # Check database integrity post-stress
    db_audit = g.query("""
    MATCH (m:MemoryNode)
    RETURN count(m) AS total,
           count(CASE WHEN m.status = 'QUARANTINED' THEN 1 END) AS quarantined,
           count(CASE WHEN m.status = 'HEALTHY' THEN 1 END) AS healthy,
           count(DISTINCT m.quarantine_root) AS distinct_roots
    """).result_set[0]

    inv = queries.invariant_check(tenant)

    # Check Meta epoch
    epoch_res = g.query("MATCH (m:Meta {id: 'meta'}) RETURN m.epoch").result_set[0][0]

    throughput_cascades_sec = num_trees / wall_sec
    throughput_nodes_sec = total_expected_nodes / wall_sec

    stats = {
        "concurrency": num_trees,
        "total_nodes": total_expected_nodes,
        "wall_clock_ms": round(t_wall_total, 2),
        "throughput_cascades_per_sec": round(throughput_cascades_sec, 2),
        "throughput_quarantined_nodes_per_sec": round(throughput_nodes_sec, 2),
        "errors": errors,
        "roundtrip_latency_ms": {
            "mean": round(float(np.mean(arr_rt)), 2),
            "median": round(float(np.median(arr_rt)), 2),
            "min": round(float(np.min(arr_rt)), 2),
            "max": round(float(np.max(arr_rt)), 2),
            "p50": round(float(np.percentile(arr_rt, 50)), 2),
            "p90": round(float(np.percentile(arr_rt, 90)), 2),
            "p95": round(float(np.percentile(arr_rt, 95)), 2),
            "p99": round(float(np.percentile(arr_rt, 99)), 2),
            "std": round(float(np.std(arr_rt)), 2),
        },
        "containment_internal_latency_ms": {
            "mean": round(float(np.mean(arr_c)), 2),
            "median": round(float(np.median(arr_c)), 2),
            "min": round(float(np.min(arr_c)), 2),
            "max": round(float(np.max(arr_c)), 2),
            "p50": round(float(np.percentile(arr_c, 50)), 2),
            "p95": round(float(np.percentile(arr_c, 95)), 2),
            "p99": round(float(np.percentile(arr_c, 99)), 2),
        },
        "audit": {
            "total_nodes_in_db": db_audit[0],
            "quarantined_nodes": db_audit[1],
            "healthy_nodes": db_audit[2],
            "distinct_quarantine_roots": db_audit[3],
            "all_nodes_quarantined": bool(db_audit[1] == total_expected_nodes),
            "invariants_passed": inv["all_passed"],
            "healthy_children_under_quarantined": inv["healthy_children_under_quarantined"],
            "actions_executed_after_ancestor_quarantined": inv["actions_executed_after_ancestor_quarantined"],
            "final_meta_epoch": epoch_res
        }
    }

    print(f"Latency Distribution (Round-trip ms):")
    print(f"  Mean: {stats['roundtrip_latency_ms']['mean']} | p50: {stats['roundtrip_latency_ms']['p50']} | p95: {stats['roundtrip_latency_ms']['p95']} | p99: {stats['roundtrip_latency_ms']['p99']} | Max: {stats['roundtrip_latency_ms']['max']}")
    print(f"Throughput: {stats['throughput_cascades_per_sec']} cascades/sec ({stats['throughput_quarantined_nodes_per_sec']} nodes/sec)")
    print(f"Database Integrity: Quarantined={db_audit[1]}/{total_expected_nodes}, Healthy={db_audit[2]}, Invariants Passed={inv['all_passed']}")
    return stats


# =========================================================================
# TEST 6: ARCHITECTURAL & PARAMETER VERDICT TESTS
# =========================================================================
def run_architecture_verdict_tests() -> Dict[str, Any]:
    print("\n" + "=" * 70)
    print("TEST 6: Parameter Limits & Architectural Edge Cases")
    print("=" * 70)
    tenant = "test_limits"
    g = queries.get_graph(tenant, "commons")
    try:
        g.delete()
    except Exception:
        pass
    queries.ensure_indexes(tenant)

    # 1. Depth Limit Test (*0..15)
    # Create chain of length 18: root -> c1 -> c2 -> ... -> c18
    print("1. Testing 18-hop chain against [:LEADS_TO|SUPPORTS*0..15] limit...")
    chain_cypher = "CREATE (f:Fact:MemoryNode {id: 'f_deep_root', status: 'HEALTHY'})\n"
    for hop in range(1, 19):
        prev = "f" if hop == 1 else f"c_hop_{hop-1}"
        chain_cypher += f"CREATE (c_hop_{hop}:Conclusion:MemoryNode {{id: 'c_hop_{hop}', status: 'HEALTHY'}})\n"
        chain_cypher += f"CREATE ({prev})-[:LEADS_TO]->(c_hop_{hop})\n"
    g.query(chain_cypher)

    res_deep = queries.cascade(tenant, "f_deep_root")
    # Check status of c_hop_15 vs c_hop_16..18
    hop_15_status = g.query("MATCH (c:Conclusion {id: 'c_hop_15'}) RETURN c.status").result_set[0][0]
    hop_16_status = g.query("MATCH (c:Conclusion {id: 'c_hop_16'}) RETURN c.status").result_set[0][0]
    print(f"  Deep cascade affected_count: {res_deep.get('affected_count')}")
    print(f"  Hop 15 status: {hop_15_status} (Expected QUARANTINED)")
    print(f"  Hop 16 status: {hop_16_status} (Expected HEALTHY due to *0..15 limit)")

    # 2. Diamond DAG Multi-path Multi-counting
    print("\n2. Testing Diamond DAG path deduplication...")
    g.query("""
    CREATE (f:Fact:MemoryNode {id: 'f_dia', status: 'HEALTHY'})
    CREATE (cA:Conclusion:MemoryNode {id: 'cA_dia', status: 'HEALTHY'})
    CREATE (cB:Conclusion:MemoryNode {id: 'cB_dia', status: 'HEALTHY'})
    CREATE (d:Conclusion:Decision:MemoryNode {id: 'd_dia', status: 'HEALTHY'})
    CREATE (f)-[:LEADS_TO]->(cA)
    CREATE (f)-[:LEADS_TO]->(cB)
    CREATE (cA)-[:LEADS_TO]->(d)
    CREATE (cB)-[:LEADS_TO]->(d)
    CREATE (f)-[:LEADS_TO]->(d)
    """)
    res_dia = queries.cascade(tenant, "f_dia")
    dia_db_count = g.query("MATCH (m:MemoryNode) WHERE m.id IN ['f_dia', 'cA_dia', 'cB_dia', 'd_dia'] AND m.status = 'QUARANTINED' RETURN count(m)").result_set[0][0]
    print(f"  Diamond DAG distinct physical nodes: 4")
    print(f"  Diamond cascade returned affected_count: {res_dia.get('affected_count')}")
    print(f"  Diamond actual quarantined in DB: {dia_db_count}")

    # 3. Label restriction: MATCH (root:Fact {id: $root_id})
    print("\n3. Testing non-Fact root quarantine (e.g. quarantining a Conclusion directly)...")
    g.query("CREATE (c_standalone:Conclusion:MemoryNode {id: 'c_standalone', status: 'HEALTHY'})")
    res_non_fact = queries.cascade(tenant, "c_standalone")
    print(f"  Non-Fact root cascade affected_count: {res_non_fact.get('affected_count')}")

    return {
        "depth_limit": {
            "max_supported_hops": 15,
            "hop_15_quarantined": bool(hop_15_status == "QUARANTINED"),
            "hop_16_remains_healthy": bool(hop_16_status == "HEALTHY"),
            "exceeds_15_hops_behavior": "Silently truncated at hop 15; leaves deeper descendants HEALTHY (orphaned)"
        },
        "diamond_deduplication": {
            "physical_nodes": 4,
            "reported_affected_count": res_dia.get("affected_count"),
            "actual_db_quarantined": dia_db_count,
            "has_multipath_duplication": bool(res_dia.get("affected_count", 0) > dia_db_count)
        },
        "label_restriction": {
            "root_must_be_fact": bool(res_non_fact.get("affected_count", 0) == 0)
        }
    }


if __name__ == "__main__":
    print("STARTING QUARANTINE RED WAVE STRESS-TEST SUITE")
    t_suite_start = time.time()

    single_results = run_single_dag_test(num_trials=10)
    precision_results = run_containment_precision_test()
    stress_50_results = run_50_concurrent_cascades_test()
    verdict_results = run_architecture_verdict_tests()

    total_suite_time = time.time() - t_suite_start
    print("\n" + "=" * 70)
    print(f"ALL TESTS COMPLETED IN {total_suite_time:.2f}s")
    print("=" * 70)

    # Save summary json
    summary = {
        "single_dag": single_results,
        "precision": precision_results,
        "stress_50": stress_50_results,
        "architecture_verdict": verdict_results,
        "total_suite_time_s": round(total_suite_time, 2)
    }
    with open("/mnt/c/Users/mdana/OneDrive/Documents/GitHub/iFone/bench/quarantine_stress_summary.json", "w") as f:
        json.dump(summary, f, indent=2)
    print("Summary saved to /mnt/c/Users/mdana/OneDrive/Documents/GitHub/iFone/bench/quarantine_stress_summary.json")
