"""
Stress Testing Suite for T03-2: Who Owns What & Who Knows What (who_knows)
Subagent 07 - Quorum Project

Tests:
1. Multi-topic query, scoring, ranking & ownership verification ('billing', 'kubernetes', 'auth')
2. Complex graph topologies (shared ownership, multi-team membership, orphan topics, circular chains)
3. Concurrency (100 who_knows queries across 10 threads) with dynamic graph mutations & KNOWS edges
4. Boundary conditions (non-existent topic, empty graph, 50+ contributors, Cypher injection)
"""

import os
import sys
import time
import json
import threading
import statistics
from typing import Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed

_current_dir = os.path.dirname(os.path.abspath(__file__))
_root_dir = os.path.abspath(os.path.join(_current_dir, ".."))
if _root_dir not in sys.path:
    sys.path.insert(0, _root_dir)

from graph import queries

TENANT_BASE = "stress_t03_2"


def clean_tenant(tenant: str):
    """Deletes all nodes/edges in tenant commons graph."""
    g = queries.get_graph(tenant, "commons")
    try:
        g.delete()
    except Exception:
        pass
    try:
        queries.ensure_indexes(tenant)
    except Exception:
        pass


# =========================================================================
# TEST 1: MULTIPLE TOPICS & RANKING VERIFICATION
# =========================================================================
def test_multiple_topics_scoring_ranking() -> Dict[str, Any]:
    print("\n" + "=" * 70)
    print("TEST 1: MULTIPLE TOPICS SCORING, RANKING & OWNERSHIP ('billing', 'kubernetes', 'auth')")
    print("=" * 70)

    tenant = f"{TENANT_BASE}_multi_topics"
    clean_tenant(tenant)
    g = queries.get_graph(tenant, "commons")

    # 1. Create Teams
    teams = [
        ("team_billing", "Billing & Payments Team"),
        ("team_infra", "Cloud Infrastructure Team"),
        ("team_sec", "Identity & Security Team"),
    ]
    for tid, tname in teams:
        g.query("CREATE (:Team {id: $id, name: $name})", {"id": tid, "name": tname})

    # 2. Create People & Team Membership
    people = [
        ("p_alice", "Alice Walker", "team_billing"),
        ("p_bob", "Bob Chen", "team_billing"),
        ("p_charlie", "Charlie Miller", "team_billing"),
        ("p_dana", "Dana Vance", "team_billing"),  # 0 sources
        ("p_evan", "Evan Wright", "team_infra"),
        ("p_frank", "Frank Gomez", "team_infra"),
        ("p_grace", "Grace Hopper", "team_sec"),
        ("p_henry", "Henry Ford", "team_sec"),
    ]
    for pid, pname, tid in people:
        g.query(
            "CREATE (p:Person {id: $id, name: $name}) "
            "WITH p MATCH (t:Team {id: $tid}) CREATE (p)-[:MEMBER_OF]->(t)",
            {"id": pid, "name": pname, "tid": tid}
        )

    # 3. Create Topics
    topics = [
        ("billing", "Billing & Invoicing Architecture"),
        ("kubernetes", "Kubernetes Cluster Management"),
        ("auth", "Authentication & Zero-Trust Access"),
    ]
    for top_id, top_name in topics:
        g.query("CREATE (:Topic {id: $id, name: $name})", {"id": top_id, "name": top_name})

    # 4. Populate Sources & Relations
    # --- Billing:
    # Alice: 3 authored, 2 resolved -> total 5
    # Bob: 3 authored -> total 3
    # Charlie: 1 resolved -> total 1
    # Dana: 0 sources
    for i in range(1, 4):
        sid = f"src_bill_auth_alice_{i}"
        g.query("""
        CREATE (s:Source {id: $sid, kind: 'DOC', title: $sid, version: '1.0'})
        WITH s MATCH (p:Person {id: 'p_alice'}), (t:Team {id: 'team_billing'}), (top:Topic {id: 'billing'})
        CREATE (p)-[:AUTHORED]->(s), (t)-[:OWNS]->(s), (s)-[:ABOUT]->(top)
        """, {"sid": sid})

    for i in range(1, 3):
        sid = f"src_bill_res_alice_{i}"
        g.query("""
        CREATE (s:Source {id: $sid, kind: 'TICKET', title: $sid, version: '1.0'})
        WITH s MATCH (p:Person {id: 'p_alice'}), (t:Team {id: 'team_billing'}), (top:Topic {id: 'billing'})
        CREATE (p)-[:RESOLVED]->(s), (t)-[:OWNS]->(s), (s)-[:ABOUT]->(top)
        """, {"sid": sid})

    for i in range(1, 4):
        sid = f"src_bill_auth_bob_{i}"
        g.query("""
        CREATE (s:Source {id: $sid, kind: 'DOC', title: $sid, version: '1.0'})
        WITH s MATCH (p:Person {id: 'p_bob'}), (t:Team {id: 'team_billing'}), (top:Topic {id: 'billing'})
        CREATE (p)-[:AUTHORED]->(s), (t)-[:OWNS]->(s), (s)-[:ABOUT]->(top)
        """, {"sid": sid})

    g.query("""
    CREATE (s:Source {id: 'src_bill_res_charlie', kind: 'TICKET', title: 'src_bill_res_charlie', version: '1.0'})
    WITH s MATCH (p:Person {id: 'p_charlie'}), (t:Team {id: 'team_billing'}), (top:Topic {id: 'billing'})
    CREATE (p)-[:RESOLVED]->(s), (t)-[:OWNS]->(s), (s)-[:ABOUT]->(top)
    """)

    # --- Kubernetes:
    # Evan: 4 authored, 4 resolved -> total 8
    # Frank: 2 authored -> total 2
    # Alice: 1 resolved -> total 1 (cross-team contributor!)
    for i in range(1, 5):
        sid = f"src_k8s_auth_evan_{i}"
        g.query("""
        CREATE (s:Source {id: $sid, kind: 'DOC', title: $sid, version: '1.0'})
        WITH s MATCH (p:Person {id: 'p_evan'}), (t:Team {id: 'team_infra'}), (top:Topic {id: 'kubernetes'})
        CREATE (p)-[:AUTHORED]->(s), (t)-[:OWNS]->(s), (s)-[:ABOUT]->(top)
        """, {"sid": sid})

    for i in range(1, 5):
        sid = f"src_k8s_res_evan_{i}"
        g.query("""
        CREATE (s:Source {id: $sid, kind: 'TICKET', title: $sid, version: '1.0'})
        WITH s MATCH (p:Person {id: 'p_evan'}), (t:Team {id: 'team_infra'}), (top:Topic {id: 'kubernetes'})
        CREATE (p)-[:RESOLVED]->(s), (t)-[:OWNS]->(s), (s)-[:ABOUT]->(top)
        """, {"sid": sid})

    for i in range(1, 3):
        sid = f"src_k8s_auth_frank_{i}"
        g.query("""
        CREATE (s:Source {id: $sid, kind: 'CHAT', title: $sid, version: '1.0'})
        WITH s MATCH (p:Person {id: 'p_frank'}), (t:Team {id: 'team_infra'}), (top:Topic {id: 'kubernetes'})
        CREATE (p)-[:AUTHORED]->(s), (t)-[:OWNS]->(s), (s)-[:ABOUT]->(top)
        """, {"sid": sid})

    g.query("""
    CREATE (s:Source {id: 'src_k8s_res_alice', kind: 'TICKET', title: 'src_k8s_res_alice', version: '1.0'})
    WITH s MATCH (p:Person {id: 'p_alice'}), (t:Team {id: 'team_infra'}), (top:Topic {id: 'kubernetes'})
    CREATE (p)-[:RESOLVED]->(s), (t)-[:OWNS]->(s), (s)-[:ABOUT]->(top)
    """)

    # --- Auth:
    # Grace: 4 authored -> total 4
    # Frank: 4 resolved -> total 4 (TIE with Grace!)
    # Henry: 2 authored -> total 2
    for i in range(1, 5):
        sid = f"src_auth_auth_grace_{i}"
        g.query("""
        CREATE (s:Source {id: $sid, kind: 'DOC', title: $sid, version: '1.0'})
        WITH s MATCH (p:Person {id: 'p_grace'}), (t:Team {id: 'team_sec'}), (top:Topic {id: 'auth'})
        CREATE (p)-[:AUTHORED]->(s), (t)-[:OWNS]->(s), (s)-[:ABOUT]->(top)
        """, {"sid": sid})

    for i in range(1, 5):
        sid = f"src_auth_res_frank_{i}"
        g.query("""
        CREATE (s:Source {id: $sid, kind: 'TICKET', title: $sid, version: '1.0'})
        WITH s MATCH (p:Person {id: 'p_frank'}), (t:Team {id: 'team_sec'}), (top:Topic {id: 'auth'})
        CREATE (p)-[:RESOLVED]->(s), (t)-[:OWNS]->(s), (s)-[:ABOUT]->(top)
        """, {"sid": sid})

    for i in range(1, 3):
        sid = f"src_auth_auth_henry_{i}"
        g.query("""
        CREATE (s:Source {id: $sid, kind: 'CHAT', title: $sid, version: '1.0'})
        WITH s MATCH (p:Person {id: 'p_henry'}), (t:Team {id: 'team_sec'}), (top:Topic {id: 'auth'})
        CREATE (p)-[:AUTHORED]->(s), (t)-[:OWNS]->(s), (s)-[:ABOUT]->(top)
        """, {"sid": sid})

    # Also test: Multi-role edge on SAME source!
    # If a person both AUTHORED and RESOLVED the same source:
    g.query("""
    CREATE (s:Source {id: 'src_dual_edge_test', kind: 'TICKET', title: 'Dual Edge', version: '1.0'})
    WITH s MATCH (p:Person {id: 'p_bob'}), (top:Topic {id: 'billing'})
    CREATE (p)-[:AUTHORED]->(s), (p)-[:RESOLVED]->(s), (s)-[:ABOUT]->(top)
    """)

    # Run who_knows queries
    res_billing = queries.who_knows(tenant, "billing")
    res_k8s = queries.who_knows(tenant, "kubernetes")
    res_auth = queries.who_knows(tenant, "auth")

    print(f"Results for 'billing': {res_billing}")
    print(f"Results for 'kubernetes': {res_k8s}")
    print(f"Results for 'auth': {res_auth}")

    # Assertions & Verification
    errors = []

    # Check Billing:
    # Note: Bob had 3 authored + 1 dual-edge source where Bob has AUTHORED and RESOLVED!
    # Does Bob get 4 or 5 evidence points?
    # Alice had 5 sources (3 auth, 2 res).
    bill_dict = {r["person_id"]: r["evidence_count"] for r in res_billing}
    if bill_dict.get("p_alice") != 5:
        errors.append(f"Billing: Alice expected 5 evidence, got {bill_dict.get('p_alice')}")
    if "p_dana" in bill_dict:
        errors.append("Billing: Dana (0 sources) should not appear in who_knows")
    if bill_dict.get("p_charlie") != 1:
        errors.append(f"Billing: Charlie expected 1 evidence, got {bill_dict.get('p_charlie')}")

    # Dual-edge analysis on Bob:
    bob_score = bill_dict.get("p_bob", 0)
    dual_edge_counted_twice = (bob_score == 5)  # 3 authored + 2 (from dual edges on 1 source)
    dual_edge_counted_once = (bob_score == 4)   # 3 authored + 1 unique source

    # Check Kubernetes:
    k8s_dict = {r["person_id"]: r["evidence_count"] for r in res_k8s}
    if k8s_dict.get("p_evan") != 8:
        errors.append(f"Kubernetes: Evan expected 8 evidence, got {k8s_dict.get('p_evan')}")
    if k8s_dict.get("p_frank") != 2:
        errors.append(f"Kubernetes: Frank expected 2 evidence, got {k8s_dict.get('p_frank')}")
    if k8s_dict.get("p_alice") != 1:
        errors.append(f"Kubernetes: Alice (cross-team) expected 1 evidence, got {k8s_dict.get('p_alice')}")

    # Check ranking order
    k8s_counts = [r["evidence_count"] for r in res_k8s]
    if k8s_counts != sorted(k8s_counts, reverse=True):
        errors.append(f"Kubernetes: Result not properly ranked descending: {k8s_counts}")

    # Check Auth (tie condition):
    auth_dict = {r["person_id"]: r["evidence_count"] for r in res_auth}
    if auth_dict.get("p_grace") != 4 or auth_dict.get("p_frank") != 4:
        errors.append(f"Auth: Grace and Frank should both have 4 evidence, got Grace={auth_dict.get('p_grace')}, Frank={auth_dict.get('p_frank')}")
    if auth_dict.get("p_henry") != 2:
        errors.append(f"Auth: Henry expected 2 evidence, got {auth_dict.get('p_henry')}")

    # Check Ownership Query verification
    # Does who_knows return team/owner information?
    has_owner_field = any("team" in r or "owner" in r or "source_ids" in r for r in res_billing)

    return {
        "success": len(errors) == 0,
        "errors": errors,
        "billing_results": res_billing,
        "k8s_results": res_k8s,
        "auth_results": res_auth,
        "dual_edge_duplicate_count": dual_edge_counted_twice,
        "dual_edge_bob_score": bob_score,
        "has_owner_field_in_payload": has_owner_field,
    }


# =========================================================================
# TEST 2: COMPLEX GRAPH TOPOLOGIES
# =========================================================================
def test_complex_graph_topologies() -> Dict[str, Any]:
    print("\n" + "=" * 70)
    print("TEST 2: COMPLEX GRAPH TOPOLOGIES")
    print("=" * 70)

    tenant = f"{TENANT_BASE}_complex_topologies"
    clean_tenant(tenant)
    g = queries.get_graph(tenant, "commons")

    errors = []

    # --- Scenario A: Shared Ownership & Multi-Author Co-Ownership
    print("-> Subtest 2A: Shared Ownership (Multiple teams owning 1 source, Co-authors)")
    g.query("""
    CREATE (t1:Team {id: 'team_alpha', name: 'Alpha Ops'})
    CREATE (t2:Team {id: 'team_beta', name: 'Beta Sec'})
    CREATE (p1:Person {id: 'p_coauth_1', name: 'CoAuthor One'})
    CREATE (p2:Person {id: 'p_coauth_2', name: 'CoAuthor Two'})
    CREATE (top:Topic {id: 'top_shared', name: 'Shared Infra'})
    CREATE (s:Source {id: 'src_shared_arch', kind: 'DOC', title: 'Shared Architecture'})
    CREATE (t1)-[:OWNS]->(s), (t2)-[:OWNS]->(s)
    CREATE (p1)-[:AUTHORED]->(s), (p2)-[:AUTHORED]->(s)
    CREATE (s)-[:ABOUT]->(top)
    """)

    res_shared = queries.who_knows(tenant, "top_shared")
    print(f"Shared ownership results: {res_shared}")
    shared_dict = {r["person_id"]: r["evidence_count"] for r in res_shared}
    if shared_dict.get("p_coauth_1") != 1 or shared_dict.get("p_coauth_2") != 1:
        errors.append(f"Shared ownership: expected both coauthors to have 1 evidence, got {shared_dict}")

    # --- Scenario B: Multi-Team Membership
    print("-> Subtest 2B: Multi-Team Membership")
    g.query("""
    CREATE (p_multi:Person {id: 'p_multi_team', name: 'Matrix Contributor'})
    WITH p_multi
    MATCH (t1:Team {id: 'team_alpha'}), (t2:Team {id: 'team_beta'}), (top:Topic {id: 'top_shared'})
    CREATE (p_multi)-[:MEMBER_OF]->(t1), (p_multi)-[:MEMBER_OF]->(t2)
    CREATE (s:Source {id: 'src_multi_team', kind: 'DOC', title: 'Multi Team Source'})
    CREATE (p_multi)-[:AUTHORED]->(s), (s)-[:ABOUT]->(top)
    """)
    res_multi = queries.who_knows(tenant, "top_shared")
    multi_dict = {r["person_id"]: r["evidence_count"] for r in res_multi}
    if multi_dict.get("p_multi_team") != 1:
        errors.append(f"Multi-team membership: expected 1 evidence, got {multi_dict.get('p_multi_team')}")

    # --- Scenario C: Orphan Topics with No Owners / No Authors
    print("-> Subtest 2C: Orphan Topics (No Sources / No Teams / No Authors)")
    g.query("CREATE (:Topic {id: 'top_orphan_empty', name: 'Completely Empty Orphan Topic'})")
    g.query("""
    CREATE (top:Topic {id: 'top_orphan_unowned_src', name: 'Topic with Unowned Source'})
    CREATE (s:Source {id: 'src_unowned', kind: 'DOC', title: 'Unowned Source'})
    CREATE (p:Person {id: 'p_solo', name: 'Solo Rogue Author'})
    CREATE (p)-[:AUTHORED]->(s), (s)-[:ABOUT]->(top)
    """)
    g.query("""
    CREATE (top:Topic {id: 'top_orphan_bot_feed', name: 'Bot Ingested Topic'})
    CREATE (t:Team {id: 'team_bots', name: 'Automated Bot Team'})
    CREATE (s:Source {id: 'src_bot', kind: 'DOC', title: 'Automated Feed'})
    CREATE (t)-[:OWNS]->(s), (s)-[:ABOUT]->(top)
    """)

    res_orphan_empty = queries.who_knows(tenant, "top_orphan_empty")
    res_unowned_src = queries.who_knows(tenant, "top_orphan_unowned_src")
    res_bot_feed = queries.who_knows(tenant, "top_orphan_bot_feed")

    print(f"Empty orphan: {res_orphan_empty}")
    print(f"Unowned source (has author): {res_unowned_src}")
    print(f"Bot feed (no person author): {res_bot_feed}")

    if res_orphan_empty != []:
        errors.append(f"Empty orphan topic should return [], got {res_orphan_empty}")
    if len(res_unowned_src) != 1 or res_unowned_src[0]["person_id"] != "p_solo":
        errors.append(f"Unowned source should return p_solo, got {res_unowned_src}")
    if res_bot_feed != []:
        errors.append(f"Bot feed with no Person authors should return [], got {res_bot_feed}")

    # --- Scenario D: Circular Contribution Chains
    print("-> Subtest 2D: Circular Contribution Chains & Self-referential edges")
    g.query("""
    CREATE (top:Topic {id: 'top_cycles', name: 'Circular Topic'})
    CREATE (pa:Person {id: 'p_cycle_a', name: 'Cycle Person A'})
    CREATE (pb:Person {id: 'p_cycle_b', name: 'Cycle Person B'})
    CREATE (s1:Source {id: 'src_cycle_1', kind: 'DOC', title: 'Cycle 1'})
    CREATE (s2:Source {id: 'src_cycle_2', kind: 'DOC', title: 'Cycle 2'})
    CREATE (pa)-[:AUTHORED]->(s1), (pb)-[:RESOLVED]->(s1), (s1)-[:ABOUT]->(top)
    CREATE (pb)-[:AUTHORED]->(s2), (pa)-[:RESOLVED]->(s2), (s2)-[:ABOUT]->(top)
    CREATE (s1)-[:REFERENCES]->(s2), (s2)-[:REFERENCES]->(s1)
    CREATE (s1)-[:SUPERSEDES]->(s1)
    CREATE (pa)-[:REPORTS_TO]->(pb), (pb)-[:REPORTS_TO]->(pa)
    """)

    t0 = time.time()
    res_cycle = queries.who_knows(tenant, "top_cycles")
    elapsed_ms = (time.time() - t0) * 1000
    print(f"Cycle results in {elapsed_ms:.2f}ms: {res_cycle}")

    cycle_dict = {r["person_id"]: r["evidence_count"] for r in res_cycle}
    # pa authored s1, resolved s2 -> 2 evidence
    # pb authored s2, resolved s1 -> 2 evidence
    if cycle_dict.get("p_cycle_a") != 2 or cycle_dict.get("p_cycle_b") != 2:
        errors.append(f"Circular chain: expected both to have 2 evidence, got {cycle_dict}")
    if elapsed_ms > 200:
        errors.append(f"Circular query took too long: {elapsed_ms:.2f}ms (potential infinite recursion)")

    return {
        "success": len(errors) == 0,
        "errors": errors,
        "shared_results": res_shared,
        "orphan_empty": res_orphan_empty,
        "orphan_unowned_src": res_unowned_src,
        "orphan_bot_feed": res_bot_feed,
        "cycle_results": res_cycle,
        "cycle_elapsed_ms": elapsed_ms,
    }


# =========================================================================
# TEST 3: CONCURRENCY & DYNAMIC GRAPH UPDATES
# =========================================================================
def test_concurrency_and_dynamic_writes() -> Dict[str, Any]:
    print("\n" + "=" * 70)
    print("TEST 3: CONCURRENCY (100 who_knows QUERIES ACROSS 10 THREADS + DYNAMIC WRITES)")
    print("=" * 70)

    tenant = f"{TENANT_BASE}_concurrency"
    clean_tenant(tenant)
    g = queries.get_graph(tenant, "commons")

    # Initial setup: topic 'concurrency_topic' with 5 base sources & 2 contributors
    g.query("CREATE (:Topic {id: 'conc_topic', name: 'Concurrency Topic'})")
    g.query("CREATE (:Person {id: 'p_base_1', name: 'Base Worker 1'})")
    g.query("CREATE (:Person {id: 'p_base_2', name: 'Base Worker 2'})")
    for i in range(5):
        g.query("""
        CREATE (s:Source {id: $sid, kind: 'DOC', title: $sid})
        WITH s MATCH (p:Person {id: 'p_base_1'}), (top:Topic {id: 'conc_topic'})
        CREATE (p)-[:AUTHORED]->(s), (s)-[:ABOUT]->(top)
        """, {"sid": f"src_base_{i}"})

    stop_writers = threading.Event()
    writer_stats = {"knows_edges_added": 0, "authored_edges_added": 0, "errors": []}

    # Writer 1: Dynamically adds Person-[:KNOWS]->Topic edges (to test prompt condition #3)
    def writer_knows_worker():
        worker_g = queries.get_graph(tenant, "commons")
        count = 0
        while not stop_writers.is_set():
            try:
                pid = f"p_dyn_knows_{count}"
                worker_g.query("""
                CREATE (p:Person {id: $pid, name: $pid})
                WITH p MATCH (top:Topic {id: 'conc_topic'})
                CREATE (p)-[:KNOWS]->(top)
                """, {"pid": pid})
                count += 1
                time.sleep(0.01)
            except Exception as e:
                writer_stats["errors"].append(f"Writer KNOWS error: {str(e)}")
        writer_stats["knows_edges_added"] = count

    # Writer 2: Dynamically adds Person-[:AUTHORED]->Source-[:ABOUT]->Topic
    def writer_authored_worker():
        worker_g = queries.get_graph(tenant, "commons")
        count = 0
        while not stop_writers.is_set():
            try:
                pid = f"p_dyn_auth_{count}"
                sid = f"src_dyn_auth_{count}"
                worker_g.query("""
                CREATE (p:Person {id: $pid, name: $pid})
                CREATE (s:Source {id: $sid, kind: 'DOC', title: $sid})
                WITH p, s MATCH (top:Topic {id: 'conc_topic'})
                CREATE (p)-[:AUTHORED]->(s), (s)-[:ABOUT]->(top)
                """, {"pid": pid, "sid": sid})
                count += 1
                time.sleep(0.015)
            except Exception as e:
                writer_stats["errors"].append(f"Writer AUTHORED error: {str(e)}")
        writer_stats["authored_edges_added"] = count

    t_writer1 = threading.Thread(target=writer_knows_worker)
    t_writer2 = threading.Thread(target=writer_authored_worker)
    t_writer1.start()
    t_writer2.start()

    # Readers: 10 threads executing 100 queries in total (10 queries per thread)
    num_threads = 10
    queries_per_thread = 10
    total_queries = num_threads * queries_per_thread

    latencies_ms = []
    read_results = []
    reader_errors = []

    def reader_worker(worker_id: int):
        thread_latencies = []
        for i in range(queries_per_thread):
            t0 = time.time()
            try:
                res = queries.who_knows(tenant, "conc_topic")
                elapsed = (time.time() - t0) * 1000
                thread_latencies.append(elapsed)
                read_results.append((worker_id, len(res), res[0]["evidence_count"] if res else 0))
            except Exception as e:
                reader_errors.append(f"Reader worker {worker_id} iter {i} failed: {str(e)}")
            time.sleep(0.005)
        return thread_latencies

    print(f"Launching {num_threads} concurrent reader threads ({total_queries} queries total)...")
    wall_start = time.time()
    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(reader_worker, tid) for tid in range(num_threads)]
        for f in as_completed(futures):
            latencies_ms.extend(f.result())
    wall_elapsed = time.time() - wall_start

    # Stop writers
    stop_writers.set()
    t_writer1.join()
    t_writer2.join()

    # Check whether KNOWS edges were captured by who_knows
    final_res = queries.who_knows(tenant, "conc_topic")
    final_ids = {r["person_id"] for r in final_res}
    knows_ids_captured = any("dyn_knows" in pid for pid in final_ids)
    authored_ids_captured = any("dyn_auth" in pid for pid in final_ids)

    latencies_sorted = sorted(latencies_ms) if latencies_ms else [0]
    p50 = statistics.median(latencies_sorted) if latencies_sorted else 0
    p95 = latencies_sorted[int(len(latencies_sorted) * 0.95)] if latencies_sorted else 0
    p99 = latencies_sorted[int(len(latencies_sorted) * 0.99)] if latencies_sorted else 0
    throughput = len(latencies_ms) / wall_elapsed if wall_elapsed > 0 else 0

    print(f"Concurrent reads completed in {wall_elapsed:.2f}s:")
    print(f"  Total queries: {len(latencies_ms)} / {total_queries}")
    print(f"  Reader errors: {len(reader_errors)}")
    print(f"  Writer errors: {len(writer_stats['errors'])}")
    print(f"  Throughput: {throughput:.1f} queries/sec")
    print(f"  p50 latency: {p50:.2f}ms | p95 latency: {p95:.2f}ms | p99 latency: {p99:.2f}ms")
    print(f"  KNOWS edges added by writer: {writer_stats['knows_edges_added']}")
    print(f"  AUTHORED edges added by writer: {writer_stats['authored_edges_added']}")
    print(f"  Were Person->KNOWS edges captured by who_knows query?: {knows_ids_captured}")
    print(f"  Were Person->AUTHORED edges captured by who_knows query?: {authored_ids_captured}")

    return {
        "success": len(reader_errors) == 0 and len(writer_stats["errors"]) == 0 and len(latencies_ms) == total_queries,
        "total_queries": len(latencies_ms),
        "expected_queries": total_queries,
        "reader_errors": reader_errors,
        "writer_errors": writer_stats["errors"],
        "throughput_ops_per_s": round(throughput, 1),
        "latencies_ms": {
            "min": round(min(latencies_sorted), 2) if latencies_sorted else 0,
            "mean": round(statistics.mean(latencies_sorted), 2) if latencies_sorted else 0,
            "p50": round(p50, 2),
            "p95": round(p95, 2),
            "p99": round(p99, 2),
            "max": round(max(latencies_sorted), 2) if latencies_sorted else 0,
        },
        "knows_edges_added": writer_stats["knows_edges_added"],
        "authored_edges_added": writer_stats["authored_edges_added"],
        "knows_edges_captured_by_who_knows": knows_ids_captured,
        "authored_edges_captured_by_who_knows": authored_ids_captured,
    }


# =========================================================================
# TEST 4: BOUNDARY CONDITIONS & EXTREME VALUES
# =========================================================================
def test_boundary_conditions() -> Dict[str, Any]:
    print("\n" + "=" * 70)
    print("TEST 4: BOUNDARY CONDITIONS & 50+ CONTRIBUTORS")
    print("=" * 70)

    errors = []

    # --- Boundary 1: Non-existent topic
    tenant = f"{TENANT_BASE}_boundary"
    clean_tenant(tenant)

    print("-> Subtest 4A: Non-existent topic")
    res_non_existent = queries.who_knows(tenant, "topic_that_does_not_exist_404")
    if res_non_existent != []:
        errors.append(f"Non-existent topic expected [], got {res_non_existent}")

    # --- Boundary 2: Empty graph (zero nodes)
    print("-> Subtest 4B: Completely empty graph")
    tenant_empty = f"{TENANT_BASE}_empty"
    clean_tenant(tenant_empty)
    res_empty = queries.who_knows(tenant_empty, "any_topic")
    if res_empty != []:
        errors.append(f"Empty graph expected [], got {res_empty}")

    # --- Boundary 3: Special characters & Cypher injection strings
    print("-> Subtest 4C: Special characters & injection strings in topic_id")
    injection_strings = [
        "",
        "billing' OR 1=1 --",
        'billing" OR 1=1 --',
        "billing/*comment*/",
        "topic:with:colons",
        "topic/with/slashes",
        "topic with spaces",
        "🔥_unicode_topic_🚀",
        "a" * 500,  # very long string
    ]
    injection_results = {}
    for inj in injection_strings:
        try:
            r = queries.who_knows(tenant, inj)
            injection_results[inj[:30]] = {"status": "ok", "rows": len(r)}
        except Exception as e:
            injection_results[inj[:30]] = {"status": "error", "error": str(e)}
            errors.append(f"Injection test '{inj[:30]}' raised exception: {str(e)}")

    # --- Boundary 4: Topic with 60 Contributors (50+ requirement)
    print("-> Subtest 4D: Topic with 60 Contributors and 1,830 sources")
    g = queries.get_graph(tenant, "commons")
    g.query("CREATE (:Topic {id: 'top_open_source_60', name: 'Massive Topic with 60 contributors'})")

    num_contributors = 60
    # Contributor i has (num_contributors - i + 1) sources
    # p_1 has 60 sources, p_2 has 59, ..., p_60 has 1 source.
    print("Generating 60 contributors with varying source weights...")
    t0_populate = time.time()
    for i in range(1, num_contributors + 1):
        pid = f"p_contrib_{i:03d}"
        pname = f"Contributor {i:03d}"
        count = num_contributors - i + 1
        # Create person and count sources in single query to keep test fast
        g.query(
            "CREATE (p:Person {id: $pid, name: $pname}) "
            "WITH p MATCH (t:Topic {id: 'top_open_source_60'}) "
            "UNWIND range(1, $count) AS idx "
            "CREATE (s:Source {id: $pid + '_src_' + toString(idx), kind: 'DOC', title: 'Doc'}) "
            "CREATE (p)-[:AUTHORED]->(s), (s)-[:ABOUT]->(t)",
            {"pid": pid, "pname": pname, "count": count}
        )
    pop_time = time.time() - t0_populate
    print(f"Populated 60 contributors and 1,830 sources in {pop_time:.2f}s")

    t0_query = time.time()
    res_large = queries.who_knows(tenant, "top_open_source_60")
    large_query_ms = (time.time() - t0_query) * 1000
    print(f"who_knows query for 60 contributors returned {len(res_large)} rows in {large_query_ms:.2f}ms")

    # Assertions on 60 contributors
    if len(res_large) != num_contributors:
        errors.append(f"Large topic expected {num_contributors} contributors, got {len(res_large)}")
    else:
        # Check rank 1
        if res_large[0]["person_id"] != "p_contrib_001" or res_large[0]["evidence_count"] != 60:
            errors.append(f"Rank 1 contributor mismatch: expected p_contrib_001 with 60, got {res_large[0]}")
        # Check rank 60
        if res_large[-1]["person_id"] != f"p_contrib_{num_contributors:03d}" or res_large[-1]["evidence_count"] != 1:
            errors.append(f"Last rank contributor mismatch: expected p_contrib_{num_contributors:03d} with 1, got {res_large[-1]}")
        # Check strict monotonicity
        counts = [r["evidence_count"] for r in res_large]
        is_strictly_desc = all(counts[k] >= counts[k+1] for k in range(len(counts)-1))
        if not is_strictly_desc:
            errors.append(f"Large topic ranking is not monotonic descending: {counts[:10]}...")

    return {
        "success": len(errors) == 0,
        "errors": errors,
        "non_existent_topic_result": res_non_existent,
        "empty_graph_result": res_empty,
        "injection_tests": injection_results,
        "large_topic_contributors_count": len(res_large),
        "large_topic_query_ms": round(large_query_ms, 2),
        "top_contributor": res_large[0] if res_large else None,
        "last_contributor": res_large[-1] if res_large else None,
    }


# =========================================================================
# MAIN RUNNER & REPORT AGGREGATOR
# =========================================================================
def run_all_stress_tests() -> Dict[str, Any]:
    print("=" * 70)
    print("SUBAGENT 07: STRESS-TESTING T03-2 (who_knows)")
    print("=" * 70)

    t_start = time.time()
    results = {}

    try:
        results["test_1_multiple_topics"] = test_multiple_topics_scoring_ranking()
    except Exception as e:
        results["test_1_multiple_topics"] = {"success": False, "error": str(e)}

    try:
        results["test_2_complex_topologies"] = test_complex_graph_topologies()
    except Exception as e:
        results["test_2_complex_topologies"] = {"success": False, "error": str(e)}

    try:
        results["test_3_concurrency"] = test_concurrency_and_dynamic_writes()
    except Exception as e:
        results["test_3_concurrency"] = {"success": False, "error": str(e)}

    try:
        results["test_4_boundary_conditions"] = test_boundary_conditions()
    except Exception as e:
        results["test_4_boundary_conditions"] = {"success": False, "error": str(e)}

    total_time = time.time() - t_start
    results["total_time_seconds"] = round(total_time, 2)
    results["all_tests_passed"] = all(
        res.get("success", False) for res in [
            results["test_1_multiple_topics"],
            results["test_2_complex_topologies"],
            results["test_3_concurrency"],
            results["test_4_boundary_conditions"],
        ]
    )

    out_path = os.path.join(_root_dir, "bench", "t03_2_who_knows_stress_results.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)

    print("\n" + "=" * 70)
    print("SUMMARY VERDICT:")
    print(f"Overall Passed: {results['all_tests_passed']}")
    print(f"Total Test Time: {total_time:.2f}s")
    print(f"Full results written to: {out_path}")
    print("=" * 70)

    return results


if __name__ == "__main__":
    run_all_stress_tests()
