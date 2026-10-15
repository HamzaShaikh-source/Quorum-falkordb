"""
Subagent 08: Stress-testing T03-3 Causal provenance tracing (provenance)
"""

import json
import time
import threading
import statistics
from typing import Any, Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from graph import queries

TENANT = "subagent_08_stress"

def setup_tenant_graph(tenant: str):
    g = queries.get_graph(tenant, "commons")
    try:
        g.delete()
    except Exception:
        pass
    queries.ensure_indexes(tenant)
    return queries.get_graph(tenant, "commons")


def test_1_and_2_deep_causal_chains():
    print("\n" + "=" * 70)
    print("TEST 1 & 2: DEEP CAUSAL CHAINS (Depth 1, 5, 10, 20) & GRAPH INTEGRITY")
    print("=" * 70)

    g = setup_tenant_graph(TENANT)
    results = {}

    # Setup base team and source
    g.query("CREATE (t:Team {id: 'team_ops', name: 'Operations'})")
    g.query("CREATE (s:Source {id: 'src_root_policy', title: 'Core Policy Manual', url: 'https://kb/policy', version: '2.1'})")
    g.query("MATCH (t:Team {id: 'team_ops'}), (s:Source {id: 'src_root_policy'}) CREATE (t)-[:OWNS]->(s)")

    depths = [1, 5, 10, 20]
    depth_results = {}

    for d in depths:
        print(f"\n--- Building causal chain of depth {d} ---")
        # Structure:
        # Fact -> (d-1) Conclusions -> Decision -> Action
        # Total hops from Fact to Decision:
        # If d=1: Fact -> Decision (1 hop) -> Action (1 hop)
        # If d=5: Fact -> C1 -> C2 -> C3 -> C4 -> Decision (5 hops) -> Action (1 hop)
        # If d=10: Fact -> C1 .. C9 -> Decision (10 hops) -> Action
        # If d=20: Fact -> C1 .. C19 -> Decision (20 hops) -> Action

        fact_id = f"fact_d{d}"
        dec_id = f"dec_d{d}"
        act_id = f"act_d{d}"

        # Create fact connected to source
        g.query("""
            CREATE (f:Fact:MemoryNode {
                id: $fid, text: $claim, quote: $quote, status: 'HEALTHY', stale: false, valid_from: timestamp()
            })
            WITH f
            MATCH (s:Source {id: 'src_root_policy'})
            CREATE (f)-[:FROM_SOURCE]->(s)
        """, {
            "fid": fact_id,
            "claim": f"Root policy fact for depth {d}",
            "quote": f"Quote for depth {d}"
        })

        curr_id = fact_id
        conc_ids = []
        for hop in range(1, d):
            conc_id = f"conc_d{d}_h{hop}"
            conc_ids.append(conc_id)
            g.query("""
                MATCH (p:MemoryNode {id: $prev})
                CREATE (c:Conclusion:MemoryNode {
                    id: $cid, text: $txt, status: 'HEALTHY', stale: false, created_at: timestamp()
                })
                CREATE (p)-[:LEADS_TO]->(c)
            """, {
                "prev": curr_id,
                "cid": conc_id,
                "txt": f"Intermediate conclusion at hop {hop} of depth {d}"
            })
            curr_id = conc_id

        # Create Decision
        g.query("""
            MATCH (p:MemoryNode {id: $prev})
            CREATE (dec:Decision:Conclusion:MemoryNode {
                id: $did, text: $txt, status: 'HEALTHY', stale: false, owner_team: 'Operations', created_at: timestamp()
            })
            CREATE (p)-[:LEADS_TO]->(dec)
        """, {
            "prev": curr_id,
            "did": dec_id,
            "txt": f"Target Decision at depth {d}"
        })

        # Create Action connected to Decision
        g.query("""
            MATCH (dec:Decision {id: $did})
            CREATE (act:Action:MemoryNode {
                id: $aid, text: $txt, status: 'HEALTHY', stale: false, gate: 'PASSED', executed: false
            })
            CREATE (dec)-[:LEADS_TO]->(act)
        """, {
            "did": dec_id,
            "aid": act_id,
            "txt": f"Terminal Action derived from decision at depth {d}"
        })

        # Measure provenance on Decision
        t0 = time.time()
        prov_dec = queries.provenance(TENANT, dec_id)
        dur_dec_ms = (time.time() - t0) * 1000

        # Measure provenance on Action
        t0 = time.time()
        prov_act = queries.provenance(TENANT, act_id)
        dur_act_ms = (time.time() - t0) * 1000

        # Measure provenance on first Conclusion if exists
        first_conc_res = None
        if conc_ids:
            prov_conc = queries.provenance(TENANT, conc_ids[0])
            first_conc_res = {
                "conc_id": conc_ids[0],
                "found": len(prov_conc) > 0,
                "count": len(prov_conc),
                "data": prov_conc
            }

        # Check path existence in raw graph to see if path exists regardless of provenance() limitations
        raw_path_query = g.query("""
            MATCH p = (f:Fact {id: $fid})-[:LEADS_TO*]->(dec:Decision {id: $did})
            RETURN length(p) AS hops, [n in nodes(p) | n.id] AS path_nodes
        """, {"fid": fact_id, "did": dec_id})
        raw_path = raw_path_query.result_set[0] if raw_path_query.result_set else None

        depth_results[f"depth_{d}"] = {
            "depth": d,
            "hops_to_decision": raw_path[0] if raw_path else None,
            "path_node_ids": raw_path[1] if raw_path else None,
            "prov_decision": {
                "decision_id": dec_id,
                "found_count": len(prov_dec),
                "latency_ms": round(dur_dec_ms, 3),
                "records": prov_dec,
                "success": len(prov_dec) > 0
            },
            "prov_action": {
                "action_id": act_id,
                "found_count": len(prov_act),
                "latency_ms": round(dur_act_ms, 3),
                "records": prov_act,
                "success": len(prov_act) > 0
            },
            "prov_first_conclusion": first_conc_res
        }

        print(f"Depth {d} Hops to Decision: {raw_path[0] if raw_path else 'None'}")
        print(f"provenance('{dec_id}') -> Found {len(prov_dec)} records in {dur_dec_ms:.2f}ms")
        if len(prov_dec) == 0:
            print(f"  [FAILURE/LIMITATION] provenance() returned 0 records for depth {d}!")
        else:
            print(f"  [SUCCESS] Found claim: '{prov_dec[0]['claim']}', source: '{prov_dec[0]['source']}'")
        print(f"provenance('{act_id}') on Action node -> Found {len(prov_act)} records")

    # Cycle and Diamond testing in Test 2
    print("\n--- TEST 2B: Testing Causal Graph with Diamonds and Cycles ---")
    
    # Diamond graph: f_diamond -> c_dia_1 -> dec_diamond
    #                         -> c_dia_2 -> dec_diamond
    g.query("""
        CREATE (f:Fact:MemoryNode {id: 'f_diamond', text: 'Diamond Root Fact', quote: 'diamond quote', status: 'HEALTHY'})
        WITH f
        MATCH (s:Source {id: 'src_root_policy'})
        CREATE (f)-[:FROM_SOURCE]->(s)
        CREATE (c1:Conclusion:MemoryNode {id: 'c_dia_1', text: 'Diamond Branch 1', status: 'HEALTHY'})
        CREATE (c2:Conclusion:MemoryNode {id: 'c_dia_2', text: 'Diamond Branch 2', status: 'HEALTHY'})
        CREATE (dec:Decision:Conclusion:MemoryNode {id: 'dec_diamond', text: 'Diamond Decision', status: 'HEALTHY'})
        CREATE (f)-[:LEADS_TO]->(c1)
        CREATE (f)-[:LEADS_TO]->(c2)
        CREATE (c1)-[:LEADS_TO]->(dec)
        CREATE (c2)-[:LEADS_TO]->(dec)
    """)
    prov_diamond = queries.provenance(TENANT, "dec_diamond")
    print(f"Diamond graph provenance count: {len(prov_diamond)}")
    # Note if duplicate rows are returned because 2 paths exist from f_diamond to dec_diamond!
    diamond_has_duplicates = len(prov_diamond) > 1

    # Cycle test: f_cycle -> c_cycle_1 <-> c_cycle_2 -> dec_cycle
    g.query("""
        CREATE (f:Fact:MemoryNode {id: 'f_cycle', text: 'Cycle Root Fact', quote: 'cycle quote', status: 'HEALTHY'})
        WITH f
        MATCH (s:Source {id: 'src_root_policy'})
        CREATE (f)-[:FROM_SOURCE]->(s)
        CREATE (c1:Conclusion:MemoryNode {id: 'c_cyc_1', text: 'Cycle Node 1', status: 'HEALTHY'})
        CREATE (c2:Conclusion:MemoryNode {id: 'c_cyc_2', text: 'Cycle Node 2', status: 'HEALTHY'})
        CREATE (dec:Decision:Conclusion:MemoryNode {id: 'dec_cycle', text: 'Cycle Decision', status: 'HEALTHY'})
        CREATE (f)-[:LEADS_TO]->(c1)
        CREATE (c1)-[:LEADS_TO]->(c2)
        CREATE (c2)-[:LEADS_TO]->(c1)
        CREATE (c2)-[:LEADS_TO]->(dec)
    """)
    t0 = time.time()
    try:
        prov_cycle = queries.provenance(TENANT, "dec_cycle")
        cycle_error = None
    except Exception as e:
        prov_cycle = []
        cycle_error = str(e)
    dur_cycle_ms = (time.time() - t0) * 1000
    print(f"Cycle graph provenance count: {len(prov_cycle)}, latency: {dur_cycle_ms:.2f}ms, error: {cycle_error}")

    results["depth_tests"] = depth_results
    results["diamond_test"] = {
        "record_count": len(prov_diamond),
        "has_duplicates": diamond_has_duplicates,
        "records": prov_diamond
    }
    results["cycle_test"] = {
        "record_count": len(prov_cycle),
        "latency_ms": round(dur_cycle_ms, 3),
        "error": cycle_error,
        "records": prov_cycle
    }

    return results


def test_3_branching_provenance():
    print("\n" + "=" * 70)
    print("TEST 3: BRANCHING PROVENANCE (Fan-in: 5 Facts -> 1 Decision; Fan-out: 1 Fact -> 10 Decisions)")
    print("=" * 70)

    TENANT_BRANCH = "subagent_08_branching"
    g = setup_tenant_graph(TENANT_BRANCH)
    results = {}

    g.query("CREATE (t:Team {id: 'team_audit', name: 'Audit Team'})")
    g.query("CREATE (s1:Source {id: 'src_sec_audit', title: 'Security Audit v1', url: 'https://sec/1', version: '1.0'})")
    g.query("CREATE (s2:Source {id: 'src_fin_audit', title: 'Financial Audit v2', url: 'https://fin/2', version: '2.0'})")
    g.query("MATCH (t:Team {id: 'team_audit'}), (s:Source) CREATE (t)-[:OWNS]->(s)")

    # -------------------------------------------------------------------------
    # Case 3A: Fan-in: 5 Facts -> 1 Decision
    # -------------------------------------------------------------------------
    print("\n--- 3A: Fan-in (5 Facts -> 1 Decision) ---")
    g.query("""
        CREATE (dec:Decision:Conclusion:MemoryNode {
            id: 'dec_fanin', text: 'Consolidated Compliance Decision', status: 'HEALTHY', stale: false
        })
    """)

    for i in range(1, 6):
        src_id = "src_sec_audit" if i <= 3 else "src_fin_audit"
        status = "HEALTHY"
        g.query("""
            MATCH (dec:Decision {id: 'dec_fanin'}), (s:Source {id: $sid})
            CREATE (f:Fact:MemoryNode {
                id: $fid, text: $txt, quote: $q, status: $status, stale: false
            })
            CREATE (f)-[:FROM_SOURCE]->(s)
            CREATE (f)-[:LEADS_TO]->(dec)
        """, {
            "fid": f"fact_fanin_{i}",
            "txt": f"Compliance rule component #{i}",
            "q": f"Quote component #{i}",
            "sid": src_id,
            "status": status
        })

    prov_fanin = queries.provenance(TENANT_BRANCH, "dec_fanin")
    print(f"Fan-in Decision Provenance: Retrieved {len(prov_fanin)} facts (Expected 5)")
    claims_retrieved = [r["claim"] for r in prov_fanin]
    sources_retrieved = [r["source"] for r in prov_fanin]
    print(f"  Claims found: {claims_retrieved}")
    print(f"  Sources found: {sources_retrieved}")

    # Test what happens when 1 parent fact is quarantined and 1 parent fact is stale
    print("\n--- 3A.1: Testing Fan-in with Quarantined and Stale Facts ---")
    g.query("MATCH (f:Fact {id: 'fact_fanin_1'}) SET f.status = 'QUARANTINED'")
    g.query("MATCH (f:Fact {id: 'fact_fanin_2'}) SET f.stale = true")
    prov_fanin_dirty = queries.provenance(TENANT_BRANCH, "dec_fanin")
    print(f"Fan-in with 1 QUARANTINED + 1 STALE fact: Retrieved {len(prov_fanin_dirty)} facts")
    dirty_statuses_retrieved = []
    for r in prov_fanin_dirty:
        claim = r["claim"]
        dirty_statuses_retrieved.append(claim)

    # -------------------------------------------------------------------------
    # Case 3B: Fan-out: 1 Fact -> 10 Decisions
    # -------------------------------------------------------------------------
    print("\n--- 3B: Fan-out (1 Fact -> 10 Decisions) ---")
    g.query("""
        MATCH (s:Source {id: 'src_sec_audit'})
        CREATE (f:Fact:MemoryNode {
            id: 'fact_hub_root', text: 'Universal Cryptographic Key Standard', quote: 'AES-256 mandatory',
            status: 'HEALTHY', stale: false
        })
        CREATE (f)-[:FROM_SOURCE]->(s)
    """)

    for i in range(1, 11):
        g.query("""
            MATCH (f:Fact {id: 'fact_hub_root'})
            CREATE (dec:Decision:Conclusion:MemoryNode {
                id: $did, text: $txt, status: 'HEALTHY', stale: false
            })
            CREATE (f)-[:LEADS_TO]->(dec)
        """, {
            "did": f"dec_spoke_{i}",
            "txt": f"System #{i} cryptographic adoption decision"
        })

    fanout_results = []
    fanout_latencies = []
    for i in range(1, 11):
        did = f"dec_spoke_{i}"
        t0 = time.time()
        prov_spoke = queries.provenance(TENANT_BRANCH, did)
        dur = (time.time() - t0) * 1000
        fanout_latencies.append(dur)
        fanout_results.append({
            "decision_id": did,
            "count": len(prov_spoke),
            "claim": prov_spoke[0]["claim"] if prov_spoke else None,
            "latency_ms": round(dur, 3)
        })

    print(f"Fan-out 10 Decisions: All retrieved? {all(r['count'] == 1 for r in fanout_results)}")
    print(f"Fan-out latencies (ms): min={min(fanout_latencies):.2f}, mean={statistics.mean(fanout_latencies):.2f}, max={max(fanout_latencies):.2f}")

    # Reverse lookup check: what if we query provenance on the hub fact itself?
    prov_hub_fact = queries.provenance(TENANT_BRANCH, "fact_hub_root")
    print(f"Reverse lookup provenance('fact_hub_root'): {prov_hub_fact}")

    results["fan_in_5_to_1"] = {
        "expected_facts": 5,
        "retrieved_count": len(prov_fanin),
        "claims": claims_retrieved,
        "sources": sources_retrieved,
        "dirty_retrieved_count": len(prov_fanin_dirty),
        "filters_quarantined_or_stale": (len(prov_fanin_dirty) < len(prov_fanin))
    }
    results["fan_out_1_to_10"] = {
        "expected_decisions": 10,
        "all_resolved": all(r["count"] == 1 for r in fanout_results),
        "mean_latency_ms": round(statistics.mean(fanout_latencies), 3),
        "details": fanout_results,
        "fact_self_query": prov_hub_fact
    }

    return results


def test_4_concurrent_stress():
    print("\n" + "=" * 70)
    print("TEST 4: CONCURRENT STRESS TRACING (50 Nodes across 10 Threads with Live Updates)")
    print("=" * 70)

    TENANT_CONC = "subagent_08_concurrent"
    g = setup_tenant_graph(TENANT_CONC)

    # 1. Setup 50 Decision trees: Fact -> C1 -> Decision
    print("Seeding 50 Fact -> Conclusion -> Decision paths...")
    g.query("CREATE (t:Team {id: 'team_conc', name: 'Concurrent Ops'})")
    g.query("CREATE (s:Source {id: 'src_conc', title: 'Live Dynamic Policy', url: 'https://live/stream', version: 'live-1.0'})")
    g.query("MATCH (t:Team {id: 'team_conc'}), (s:Source {id: 'src_conc'}) CREATE (t)-[:OWNS]->(s)")

    for i in range(50):
        g.query("""
            MATCH (s:Source {id: 'src_conc'})
            CREATE (f:Fact:MemoryNode {
                id: $fid, text: $ftxt, quote: $fquote, status: 'HEALTHY', stale: false, version: 1
            })
            CREATE (f)-[:FROM_SOURCE]->(s)
            CREATE (c:Conclusion:MemoryNode {
                id: $cid, text: $ctxt, status: 'HEALTHY', stale: false
            })
            CREATE (dec:Decision:Conclusion:MemoryNode {
                id: $did, text: $dtxt, status: 'HEALTHY', stale: false
            })
            CREATE (f)-[:LEADS_TO]->(c)
            CREATE (c)-[:LEADS_TO]->(dec)
        """, {
            "fid": f"f_conc_{i}",
            "ftxt": f"Original Fact claim #{i} initial version",
            "fquote": f"Original Quote #{i}",
            "cid": f"c_conc_{i}",
            "ctxt": f"Conclusion derived from #{i}",
            "did": f"dec_conc_{i}",
            "dtxt": f"Decision #{i}"
        })

    decision_ids = [f"dec_conc_{i}" for i in range(50)]
    fact_ids = [f"f_conc_{i}" for i in range(50)]

    read_results = []
    write_results = []
    stop_event = threading.Event()
    stats_lock = threading.Lock()

    def reader_worker(thread_idx: int, iterations: int):
        latencies = []
        errors = []
        success_count = 0
        for it in range(iterations):
            did = decision_ids[(thread_idx * 5 + it) % 50]
            t0 = time.time()
            try:
                res = queries.provenance(TENANT_CONC, did)
                elapsed = (time.time() - t0) * 1000
                latencies.append(elapsed)
                if len(res) >= 1:
                    success_count += 1
                else:
                    errors.append(f"Empty provenance result for {did}")
            except Exception as e:
                errors.append(f"Exception on {did}: {str(e)}")
        with stats_lock:
            read_results.append({
                "thread_idx": thread_idx,
                "iterations": iterations,
                "success_count": success_count,
                "latencies": latencies,
                "errors": errors
            })

    def writer_worker(thread_idx: int):
        write_count = 0
        write_errors = []
        step = 0
        while not stop_event.is_set():
            target_fid = fact_ids[(thread_idx * 13 + step) % 50]
            new_text = f"Updated Fact claim for {target_fid} at step {step} ts {time.time()}"
            try:
                # Update fact text directly
                gw = queries.get_graph(TENANT_CONC, "commons")
                gw.query("""
                    MATCH (f:Fact {id: $fid})
                    SET f.text = $txt, f.version = coalesce(f.version, 1) + 1
                """, {"fid": target_fid, "txt": new_text})
                write_count += 1
            except Exception as e:
                write_errors.append(f"Write error on {target_fid}: {str(e)}")
            step += 1
            time.sleep(0.01) # 10ms throttle
        with stats_lock:
            write_results.append({
                "thread_idx": thread_idx,
                "write_count": write_count,
                "errors": write_errors
            })

    print("Launching 10 threads: 8 reader threads + 2 writer threads concurrently...")
    reader_threads = []
    writer_threads = []

    # 2 writer threads
    for w in range(2):
        t = threading.Thread(target=writer_worker, args=(w,))
        writer_threads.append(t)
        t.start()

    # 8 reader threads (each runs 50 iterations -> 400 total reads)
    for r in range(8):
        t = threading.Thread(target=reader_worker, args=(r, 50))
        reader_threads.append(t)
        t.start()

    # Wait for readers to finish
    for t in reader_threads:
        t.join()

    # Stop writers
    stop_event.set()
    for t in writer_threads:
        t.join()

    # Aggregate stats
    all_read_latencies = []
    total_reads = 0
    total_read_success = 0
    all_read_errors = []

    for rr in read_results:
        all_read_latencies.extend(rr["latencies"])
        total_reads += rr["iterations"]
        total_read_success += rr["success_count"]
        all_read_errors.extend(rr["errors"])

    total_writes = sum(wr["write_count"] for wr in write_results)
    all_write_errors = []
    for wr in write_results:
        all_write_errors.extend(wr["errors"])

    all_read_latencies.sort()
    p50 = statistics.median(all_read_latencies) if all_read_latencies else 0
    p95 = all_read_latencies[int(len(all_read_latencies) * 0.95)] if all_read_latencies else 0
    p99 = all_read_latencies[int(len(all_read_latencies) * 0.99)] if all_read_latencies else 0
    mean_lat = statistics.mean(all_read_latencies) if all_read_latencies else 0

    print(f"\n--- Concurrent Stress Results ---")
    print(f"Total concurrent reads: {total_reads}")
    print(f"Successful reads (found >= 1 fact): {total_read_success} ({total_read_success/total_reads*100:.1f}%)")
    print(f"Read Errors: {len(all_read_errors)}")
    print(f"Total concurrent writes executed: {total_writes}")
    print(f"Write Errors: {len(all_write_errors)}")
    print(f"Latency (ms): min={min(all_read_latencies):.2f}, mean={mean_lat:.2f}, p50={p50:.2f}, p95={p95:.2f}, p99={p99:.2f}, max={max(all_read_latencies):.2f}")

    return {
        "total_reads": total_reads,
        "successful_reads": total_read_success,
        "read_error_count": len(all_read_errors),
        "read_errors_sample": all_read_errors[:5],
        "total_writes": total_writes,
        "write_error_count": len(all_write_errors),
        "latency_stats_ms": {
            "min": round(min(all_read_latencies), 2),
            "mean": round(mean_lat, 2),
            "p50": round(p50, 2),
            "p95": round(p95, 2),
            "p99": round(p99, 2),
            "max": round(max(all_read_latencies), 2)
        }
    }


def test_5_boundary_conditions():
    print("\n" + "=" * 70)
    print("TEST 5: BOUNDARY CONDITIONS")
    print("=" * 70)

    TENANT_BOUND = "subagent_08_boundary"
    g = setup_tenant_graph(TENANT_BOUND)
    results = {}

    g.query("CREATE (t:Team {id: 'team_b', name: 'Boundary Team'})")
    g.query("CREATE (s:Source {id: 'src_b', title: 'Boundary Source', url: 'https://b/1', version: '1.0'})")
    g.query("MATCH (t:Team {id: 'team_b'}), (s:Source {id: 'src_b'}) CREATE (t)-[:OWNS]->(s)")

    # 5.1 Root Fact (depth 0)
    g.query("""
        MATCH (s:Source {id: 'src_b'})
        CREATE (f:Fact:MemoryNode {
            id: 'fact_root_zero', text: 'Root Fact Depth 0', quote: 'depth 0 quote', status: 'HEALTHY'
        })
        CREATE (f)-[:FROM_SOURCE]->(s)
    """)
    res_root = queries.provenance(TENANT_BOUND, "fact_root_zero")
    print(f"5.1 Root Fact (depth 0): provenance('fact_root_zero') -> {res_root}")

    # 5.2 Orphan Decision (No incoming LEADS_TO)
    g.query("""
        CREATE (dec:Decision:Conclusion:MemoryNode {
            id: 'dec_orphan', text: 'Orphan Decision with no parents', status: 'HEALTHY'
        })
    """)
    res_orphan_dec = queries.provenance(TENANT_BOUND, "dec_orphan")
    print(f"5.2 Orphan Decision: provenance('dec_orphan') -> {res_orphan_dec}")

    # 5.3 Orphan Fact (No FROM_SOURCE)
    g.query("""
        CREATE (f:Fact:MemoryNode {
            id: 'fact_no_source', text: 'Fact without source', quote: 'no source quote', status: 'HEALTHY'
        })
        CREATE (dec:Decision:Conclusion:MemoryNode {
            id: 'dec_from_no_source', text: 'Decision from sourceless fact', status: 'HEALTHY'
        })
        CREATE (f)-[:LEADS_TO]->(dec)
    """)
    res_no_source = queries.provenance(TENANT_BOUND, "dec_from_no_source")
    print(f"5.3 Decision from Sourceless Fact: provenance('dec_from_no_source') -> {res_no_source}")

    # 5.4 Non-existent node
    res_nonexistent = queries.provenance(TENANT_BOUND, "node_does_not_exist_xyz")
    print(f"5.4 Non-existent Node: provenance('node_does_not_exist_xyz') -> {res_nonexistent}")

    # 5.5 Action Node (terminal leaf, not labeled Decision)
    g.query("""
        MATCH (s:Source {id: 'src_b'})
        CREATE (f:Fact:MemoryNode {id: 'f_for_act', text: 'Fact for Action', quote: 'q', status: 'HEALTHY'})
        CREATE (f)-[:FROM_SOURCE]->(s)
        CREATE (act:Action:MemoryNode {id: 'act_terminal', text: 'Terminal Action', status: 'HEALTHY'})
        CREATE (f)-[:LEADS_TO]->(act)
    """)
    res_action = queries.provenance(TENANT_BOUND, "act_terminal")
    print(f"5.5 Action Node: provenance('act_terminal') -> {res_action}")

    # 5.6 Source without Team Owner (OPTIONAL MATCH test)
    g.query("""
        CREATE (s_no_team:Source {id: 'src_no_team', title: 'Unowned Source', url: 'https://orphan/src', version: '0.1'})
        CREATE (f_no_team:Fact:MemoryNode {id: 'f_no_team', text: 'Unowned Fact', quote: 'unowned quote', status: 'HEALTHY'})
        CREATE (dec_no_team:Decision:Conclusion:MemoryNode {id: 'dec_no_team', text: 'Decision from unowned source', status: 'HEALTHY'})
        CREATE (f_no_team)-[:FROM_SOURCE]->(s_no_team)
        CREATE (f_no_team)-[:LEADS_TO]->(dec_no_team)
    """)
    res_unowned = queries.provenance(TENANT_BOUND, "dec_no_team")
    print(f"5.6 Source without Team: provenance('dec_no_team') -> {res_unowned}")

    # 5.7 Empty string and Null-like inputs
    try:
        res_empty = queries.provenance(TENANT_BOUND, "")
    except Exception as e:
        res_empty = f"Exception: {e}"
    print(f"5.7 Empty string ID: provenance('') -> {res_empty}")

    results["root_fact_depth_0"] = {
        "input_id": "fact_root_zero",
        "returned": res_root,
        "count": len(res_root),
        "notes": "Returns empty because pattern expects (dec:Decision)<-[:LEADS_TO*1..5]-(f:Fact)"
    }
    results["orphan_decision"] = {
        "input_id": "dec_orphan",
        "returned": res_orphan_dec,
        "count": len(res_orphan_dec),
        "notes": "Returns empty as expected (no ancestors)"
    }
    results["decision_from_sourceless_fact"] = {
        "input_id": "dec_from_no_source",
        "returned": res_no_source,
        "count": len(res_no_source),
        "notes": "Returns empty because pattern requires [:FROM_SOURCE]->(s:Source)"
    }
    results["non_existent_node"] = {
        "input_id": "node_does_not_exist_xyz",
        "returned": res_nonexistent,
        "count": len(res_nonexistent),
        "notes": "Gracefully returns empty list without error"
    }
    results["action_node"] = {
        "input_id": "act_terminal",
        "returned": res_action,
        "count": len(res_action),
        "notes": "Returns empty because pattern hardcodes (dec:Decision) label"
    }
    results["unowned_source"] = {
        "input_id": "dec_no_team",
        "returned": res_unowned,
        "count": len(res_unowned),
        "notes": "Returns owner=None due to OPTIONAL MATCH (team:Team)-[:OWNS]->(s)"
    }
    results["empty_string_id"] = {
        "input_id": "",
        "returned": res_empty,
        "count": len(res_empty) if isinstance(res_empty, list) else 0
    }

    return results


def run_all_tests():
    print("=" * 70)
    print("SUBAGENT 08: CAUSAL PROVENANCE TRACING (provenance) STRESS TEST")
    print("=" * 70)

    summary_report = {}

    summary_report["tests_1_and_2"] = test_1_and_2_deep_causal_chains()
    summary_report["test_3"] = test_3_branching_provenance()
    summary_report["test_4"] = test_4_concurrent_stress()
    summary_report["test_5"] = test_5_boundary_conditions()

    print("\n" + "=" * 70)
    print("COMPLETE STRESS TEST SUMMARY REPORT (JSON)")
    print("=" * 70)
    print(json.dumps(summary_report, indent=2))

    # Save to disk for reference
    with open("/mnt/c/Users/mdana/OneDrive/Documents/GitHub/iFone/bench/provenance_stress_results.json", "w") as f:
        json.dump(summary_report, f, indent=2)
    print("\nResults written to bench/provenance_stress_results.json")


if __name__ == "__main__":
    run_all_tests()
