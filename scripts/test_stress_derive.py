"""
Subagent 11: Stress-testing atomic derivation checks (derive)
Comprehensive Test Suite covering:
1. Valid HEALTHY parents (single, multiple, chained decisions)
2. 1 parent QUARANTINED (atomic rejection, no phantom nodes)
3. 1 parent non-existent (atomic rejection, no phantom nodes)
4. Race condition: concurrent quarantine vs derive at exact same millisecond across hundreds of iterations
5. Derive with 10 parent facts simultaneously (all healthy, 1 quarantined, 1 missing)
6. Parameter & logic edge cases (empty parents, duplicate parent IDs, return row counts, graph invariants)
"""

import time
import threading
from graph import queries

TENANT = "stress_derive_tenant"

def setup_tenant():
    g = queries.get_graph(TENANT, "commons")
    try:
        g.delete()
    except Exception:
        pass
    queries.ensure_indexes(TENANT)
    return g

def test_1_healthy_parents():
    print("\n--- TEST 1: Derive decisions with valid HEALTHY parents ---")
    g = setup_tenant()
    
    # 1a. Single parent fact
    g.query("CREATE (:Fact:MemoryNode {id: 'f_single', status: 'HEALTHY', text: 'Single Fact', stale: false})")
    res1 = queries.derive(TENANT, "dec_single", ["f_single"], "Decision from single fact", is_decision=True, owner_team="RiskTeam")
    assert res1["success"] is True, f"Expected success True, got {res1}"
    assert res1["conclusion_id"] == "dec_single"
    assert res1["status"] == "HEALTHY"
    
    # Verify node in graph
    node = g.query("MATCH (d:Decision {id: 'dec_single'}) RETURN d.id, labels(d), d.owner_team, d.text, d.status, d.stale").result_set
    assert len(node) == 1, "Decision node not found"
    row = node[0]
    assert "Decision" in row[1] and "Conclusion" in row[1] and "MemoryNode" in row[1], f"Labels mismatch: {row[1]}"
    assert row[2] == "RiskTeam"
    assert row[3] == "Decision from single fact"
    assert row[4] == "HEALTHY"
    assert row[5] is False
    
    # Verify edge
    edges = g.query("MATCH (p:Fact {id: 'f_single'})-[r:LEADS_TO]->(d:Decision {id: 'dec_single'}) RETURN type(r)").result_set
    assert len(edges) == 1, f"Expected 1 edge, found {len(edges)}"
    print("  [1a] Single healthy parent -> PASSED")

    # 1b. Multiple healthy parents (3 parents)
    g.query("CREATE (:Fact:MemoryNode {id: 'f_m1', status: 'HEALTHY', text: 'M1', stale: false})")
    g.query("CREATE (:Fact:MemoryNode {id: 'f_m2', status: 'HEALTHY', text: 'M2', stale: false})")
    g.query("CREATE (:Fact:MemoryNode {id: 'f_m3', status: 'HEALTHY', text: 'M3', stale: false})")
    res2 = queries.derive(TENANT, "dec_multi", ["f_m1", "f_m2", "f_m3"], "Multi-fact decision", is_decision=True, owner_team="OpsTeam")
    assert res2["success"] is True, f"Expected success True, got {res2}"
    
    # Verify edges from all 3
    edges3 = g.query("MATCH (p:MemoryNode)-[:LEADS_TO]->(d:Decision {id: 'dec_multi'}) RETURN p.id").result_set
    edge_parents = sorted([r[0] for r in edges3])
    assert edge_parents == ["f_m1", "f_m2", "f_m3"], f"Parent edges mismatch: {edge_parents}"
    print("  [1b] Multiple healthy parents (3) -> PASSED")

    # 1c. Chained derivation: Fact -> Decision 1 -> Decision 2
    res_chain = queries.derive(TENANT, "dec_chained", ["dec_single"], "Chained Decision", is_decision=True, owner_team="ExecTeam")
    assert res_chain["success"] is True, f"Chained derive failed: {res_chain}"
    chain_edge = g.query("MATCH (d1:Decision {id: 'dec_single'})-[:LEADS_TO]->(d2:Decision {id: 'dec_chained'}) RETURN d2.id").result_set
    assert len(chain_edge) == 1, "Chained edge not found"
    print("  [1c] Chained derivation (Fact -> Dec1 -> Dec2) -> PASSED")
    
    return True

def test_2_quarantined_parent():
    print("\n--- TEST 2: Derive decision where 1 parent is QUARANTINED ---")
    g = setup_tenant()
    
    # Create 3 facts: 2 healthy, 1 quarantined
    g.query("CREATE (:Fact:MemoryNode {id: 'f_h1', status: 'HEALTHY', text: 'Healthy 1', stale: false})")
    g.query("CREATE (:Fact:MemoryNode {id: 'f_h2', status: 'HEALTHY', text: 'Healthy 2', stale: false})")
    g.query("CREATE (:Fact:MemoryNode {id: 'f_q1', status: 'QUARANTINED', text: 'Quarantined Fact', stale: false, quarantined_at: timestamp()})")
    
    # Attempt derive
    res = queries.derive(TENANT, "dec_should_fail", ["f_h1", "f_q1", "f_h2"], "Should be rejected", is_decision=True)
    assert res["success"] is False, f"Expected failure, got {res}"
    assert "Atomic derivation guard refused" in res["error"]
    print(f"  [2a] Derive rejected with error: {res['error']}")
    
    # Verify atomicity: NO node was created
    nodes = g.query("MATCH (n {id: 'dec_should_fail'}) RETURN n").result_set
    assert len(nodes) == 0, f"Atomicity violation! Phantom node created: {nodes}"
    
    # Verify NO orphan edges created
    edges = g.query("MATCH ()-[r]->(n {id: 'dec_should_fail'}) RETURN r").result_set
    assert len(edges) == 0, f"Phantom edges created: {edges}"
    print("  [2b] Verified atomicity: 0 phantom nodes, 0 phantom edges -> PASSED")

    # Also test single quarantined parent
    res_single_q = queries.derive(TENANT, "dec_single_q", ["f_q1"], "Single quarantined parent", is_decision=True)
    assert res_single_q["success"] is False
    assert len(g.query("MATCH (n {id: 'dec_single_q'}) RETURN n").result_set) == 0
    print("  [2c] Single quarantined parent rejected -> PASSED")

    # Also test SUPERSEDED parent rejection
    g.query("CREATE (:Fact:MemoryNode {id: 'f_sup', status: 'SUPERSEDED', text: 'Superseded Fact', stale: false})")
    res_sup = queries.derive(TENANT, "dec_sup", ["f_sup"], "Superseded parent", is_decision=True)
    assert res_sup["success"] is False
    assert len(g.query("MATCH (n {id: 'dec_sup'}) RETURN n").result_set) == 0
    print("  [2d] SUPERSEDED parent rejected -> PASSED")
    
    return True

def test_3_nonexistent_parent():
    print("\n--- TEST 3: Derive decision where 1 parent is non-existent ---")
    g = setup_tenant()
    
    g.query("CREATE (:Fact:MemoryNode {id: 'f_real', status: 'HEALTHY', text: 'Real Fact', stale: false})")
    
    # Attempt derive with 1 real, 1 fake
    res = queries.derive(TENANT, "dec_ghost", ["f_real", "f_nonexistent_xyz"], "Ghost parent decision", is_decision=True)
    assert res["success"] is False, f"Expected failure, got {res}"
    assert "Atomic derivation guard refused" in res["error"]
    print(f"  [3a] Derive rejected with error: {res['error']}")
    
    # Verify atomicity
    nodes = g.query("MATCH (n {id: 'dec_ghost'}) RETURN n").result_set
    assert len(nodes) == 0, f"Atomicity violation! Phantom node created: {nodes}"
    print("  [3b] Verified atomicity: 0 phantom nodes created -> PASSED")

    # Attempt derive where ALL parents non-existent
    res_all_ghost = queries.derive(TENANT, "dec_all_ghost", ["ghost_1", "ghost_2"], "All ghost parents", is_decision=True)
    assert res_all_ghost["success"] is False
    assert len(g.query("MATCH (n {id: 'dec_all_ghost'}) RETURN n").result_set) == 0
    print("  [3c] All non-existent parents rejected -> PASSED")

    # Edge case: duplicate parent IDs ['f_real', 'f_real']
    # Let's see what Cypher size(parents) = size($parent_ids) does
    res_dup = queries.derive(TENANT, "dec_dup", ["f_real", "f_real"], "Duplicate parent ID", is_decision=True)
    print(f"  [3d] Duplicate parent IDs result: success={res_dup['success']} (error={res_dup.get('error')})")
    # Cypher matches f_real once, so parents has size 1 while $parent_ids has size 2, so it rejects!
    assert res_dup["success"] is False, "Duplicate parent IDs should be rejected because size mismatch"
    print("  [3d] Duplicate parent IDs handled atomically -> PASSED")
    
    return True

def test_4_race_condition():
    print("\n--- TEST 4: Race condition: concurrent quarantine vs derive at exact same millisecond ---")
    
    # We will run 100 trials of concurrent race conditions.
    # In each trial:
    # 1. We create a healthy parent fact `f_race_{i}`.
    # 2. We use a threading Barrier to trigger thread A (cascade quarantine) and thread B (derive) simultaneously.
    # 3. We record results of both threads.
    # 4. We assert:
    #    - If derive returned success=True, was it before quarantine?
    #      If derive succeeded before quarantine, cascade must have caught the decision and quarantined it!
    #    - If derive returned success=False, quarantine won the race.
    #    - Under NO CIRCUMSTANCES should a Decision ever remain HEALTHY under a QUARANTINED parent!
    #    - Under NO CIRCUMSTANCES should derive succeed on an already QUARANTINED parent!

    trials = 100
    derive_success_count = 0
    derive_rejected_count = 0
    invariant_violations = 0
    
    g = setup_tenant()
    
    for i in range(trials):
        parent_id = f"f_race_{i}"
        decision_id = f"dec_race_{i}"
        
        # Setup parent fact
        g.query(f"CREATE (:Fact:MemoryNode {{id: '{parent_id}', status: 'HEALTHY', text: 'Race Fact {i}', stale: false}})")
        
        barrier = threading.Barrier(2)
        results = {}
        
        def run_quarantine():
            barrier.wait()
            t_start = time.perf_counter()
            # Run quarantine cascade
            res = queries.cascade(TENANT, parent_id)
            t_end = time.perf_counter()
            results["quarantine"] = {"res": res, "t_start": t_start, "t_end": t_end}
            
        def run_derive():
            barrier.wait()
            t_start = time.perf_counter()
            # Run derive
            res = queries.derive(TENANT, decision_id, [parent_id], f"Race Decision {i}", is_decision=True)
            t_end = time.perf_counter()
            results["derive"] = {"res": res, "t_start": t_start, "t_end": t_end}
            
        t1 = threading.Thread(target=run_quarantine)
        t2 = threading.Thread(target=run_derive)
        
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        
        derive_res = results["derive"]["res"]
        quarantine_res = results["quarantine"]["res"]
        
        # Check graph state for this pair
        check = g.query(f"""
        MATCH (p:Fact {{id: '{parent_id}'}})
        OPTIONAL MATCH (d:Decision {{id: '{decision_id}'}})
        RETURN p.status, coalesce(d.status, 'NOT_CREATED')
        """).result_set[0]
        
        p_status = check[0]
        d_status = check[1]
        
        if derive_res["success"]:
            derive_success_count += 1
            # If derive succeeded, parent was healthy when derive ran.
            # Then cascade either:
            # - Ran after derive completed: so cascade captured d and marked it QUARANTINED!
            # - Let's check: is d_status QUARANTINED?
            if d_status == "HEALTHY" and p_status == "QUARANTINED":
                # Severe race bug: derive created HEALTHY decision, but cascade missed it!
                invariant_violations += 1
                print(f"  [!] VIOLATION in trial {i}: Decision is HEALTHY under QUARANTINED parent!")
        else:
            derive_rejected_count += 1
            # Derive was rejected! Verify no decision node was created
            assert d_status == "NOT_CREATED", f"Trial {i}: derive failed but decision exists: {d_status}"
            assert p_status == "QUARANTINED", f"Trial {i}: derive failed but parent status is {p_status}"
    
    # Audit global invariants
    inv = queries.invariant_check(TENANT)
    
    print(f"  Total trials: {trials}")
    print(f"  Derive won race (succeeded & cascade quarantined it): {derive_success_count}")
    print(f"  Quarantine won race (derive atomically rejected): {derive_rejected_count}")
    print(f"  Invariant violations: {invariant_violations}")
    print(f"  Graph invariant audit: healthy_children_under_quarantined = {inv['healthy_children_under_quarantined']}")
    assert invariant_violations == 0, f"Found {invariant_violations} invariant violations!"
    assert inv["healthy_children_under_quarantined"] == 0, f"Orphan healthy children detected: {inv}"
    assert inv["all_passed"] is True, f"Invariant checks failed: {inv}"
    print("  Race condition stress test -> PASSED (100% atomic consistency)")
    return True

def test_5_ten_parent_facts():
    print("\n--- TEST 5: Derive with 10 parent facts simultaneously ---")
    g = setup_tenant()
    
    parent_ids = [f"f_10_{i}" for i in range(10)]
    for pid in parent_ids:
        g.query(f"CREATE (:Fact:MemoryNode {{id: '{pid}', status: 'HEALTHY', text: 'Fact {pid}', stale: false}})")
        
    # 5a. All 10 healthy
    t0 = time.time()
    res = queries.derive(TENANT, "dec_10_healthy", parent_ids, "Decision with 10 parents", is_decision=True, owner_team="MultiTeam")
    ms = (time.time() - t0) * 1000
    assert res["success"] is True, f"Expected success, got {res}"
    assert res["conclusion_id"] == "dec_10_healthy"
    print(f"  [5a] Derive with 10 healthy parents succeeded in {ms:.2f} ms")
    
    # Verify all 10 edges exist
    edges = g.query("MATCH (p:Fact)-[:LEADS_TO]->(d:Decision {id: 'dec_10_healthy'}) RETURN p.id").result_set
    edge_pids = sorted([r[0] for r in edges])
    assert edge_pids == sorted(parent_ids), f"Edge parent mismatch: found {len(edges)} edges: {edge_pids}"
    print("  [5a] All 10 incoming LEADS_TO edges verified -> PASSED")

    # 5b. 10 parents where 1 is QUARANTINED
    g.query("CREATE (:Fact:MemoryNode {id: 'f_10_q', status: 'QUARANTINED', text: 'Quarantined Fact 10', stale: false})")
    parents_with_q = parent_ids[:9] + ["f_10_q"]
    res_q = queries.derive(TENANT, "dec_10_q", parents_with_q, "Decision with 1 quarantined parent", is_decision=True)
    assert res_q["success"] is False, f"Expected failure, got {res_q}"
    assert "Atomic derivation guard refused" in res_q["error"]
    # Verify no node created
    assert len(g.query("MATCH (n {id: 'dec_10_q'}) RETURN n").result_set) == 0
    print("  [5b] 10 parents with 1 QUARANTINED atomically rejected -> PASSED")

    # 5c. 10 parents where 1 is non-existent
    parents_with_missing = parent_ids[:9] + ["f_10_nonexistent"]
    res_m = queries.derive(TENANT, "dec_10_m", parents_with_missing, "Decision with 1 missing parent", is_decision=True)
    assert res_m["success"] is False, f"Expected failure, got {res_m}"
    assert "Atomic derivation guard refused" in res_m["error"]
    assert len(g.query("MATCH (n {id: 'dec_10_m'}) RETURN n").result_set) == 0
    print("  [5c] 10 parents with 1 non-existent atomically rejected -> PASSED")

    # 5d. Cascade test on 1 of the 10 parents
    # If parent f_10_0 is quarantined, does cascade propagate to dec_10_healthy?
    cascade_res = queries.cascade(TENANT, "f_10_0")
    assert cascade_res["success"] is True
    dec_check = g.query("MATCH (d:Decision {id: 'dec_10_healthy'}) RETURN d.status, d.quarantine_root").result_set[0]
    assert dec_check[0] == "QUARANTINED", f"Expected QUARANTINED, got {dec_check[0]}"
    assert dec_check[1] == "f_10_0"
    print("  [5d] Quarantine cascade through 10-parent node verified -> PASSED")
    
    return True

def test_6_logic_and_parameters():
    print("\n--- TEST 6: Parameter & Logic Analysis ---")
    g = setup_tenant()
    
    # 6a. Empty parent list `[]`
    res_empty = queries.derive(TENANT, "dec_empty", [], "No parents", is_decision=True)
    print(f"  [6a] Empty parent list `[]`: success={res_empty['success']}, res={res_empty}")
    # In Cypher: MATCH (p) WHERE p.id IN [] -> collect(p) is []. size(parents) = size([]) -> 0 = 0 -> ALL() is True -> creates node!
    # Does a decision make sense with 0 parents? Usually conclusions derive from something!
    
    # 6b. Check Cypher row return count behavior
    # Note on line 192 of graph/queries.py:
    # UNWIND parents AS p
    # CREATE (p)-[:LEADS_TO]->(c)
    # RETURN c.id, c.status
    # If parents has N elements, UNWIND produces N rows.
    # In derive(), `rows = len(res.result_set)` is logged to QUERY_LOG.
    g.query("CREATE (:Fact:MemoryNode {id: 'fp1', status: 'HEALTHY'}), (:Fact:MemoryNode {id: 'fp2', status: 'HEALTHY'}), (:Fact:MemoryNode {id: 'fp3', status: 'HEALTHY'})")
    queries.derive(TENANT, "dec_unwind_check", ["fp1", "fp2", "fp3"], "Unwind check", is_decision=True)
    last_log = queries.get_query_log(1)[0]
    print(f"  [6b] Query log rows for 3 parents: {last_log['rows']} rows (due to UNWIND without DISTINCT/aggregation)")
    
    # 6c. Non-decision conclusion (is_decision=False)
    queries.derive(TENANT, "conc_plain", ["fp1"], "Plain conclusion", is_decision=False)
    labels = g.query("MATCH (c {id: 'conc_plain'}) RETURN labels(c)").result_set[0][0]
    assert "Conclusion" in labels and "MemoryNode" in labels
    assert "Decision" not in labels
    print(f"  [6c] is_decision=False labels: {labels} -> PASSED")

    return True

if __name__ == "__main__":
    print("Starting Subagent 11 Stress Test Suite...")
    t_start = time.time()
    test_1_healthy_parents()
    test_2_quarantined_parent()
    test_3_nonexistent_parent()
    test_4_race_condition()
    test_5_ten_parent_facts()
    test_6_logic_and_parameters()
    total_time = time.time() - t_start
    print("\n==========================================")
    print(f"ALL 6 TEST SUITES PASSED in {total_time:.2f} seconds!")
    print("==========================================")
