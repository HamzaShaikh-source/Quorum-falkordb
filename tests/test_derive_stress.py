"""
tests/test_derive_stress.py

Subagent 11: Stress-testing atomic derivation checks (derive).
Specific tests:
1. Derive decisions with valid HEALTHY parents -> verify success.
2. Derive decision where 1 parent is QUARANTINED -> verify atomic rejection.
3. Derive decision where 1 parent is non-existent -> verify atomic rejection.
4. Race condition: concurrent thread quarantines parent AT THE EXACT SAME MILLISECOND another thread attempts derive.
   Verify derive never succeeds on a quarantined parent and zero invariant violations.
5. Derive with 10 parent facts simultaneously.
6. Extreme concurrent barrage: 10 parallel derive threads racing with 1 cascade thread.
7. Logic and parameters edge-cases (empty parents, duplicate parent IDs, is_decision flag).
"""

import time
import threading
import graph.queries as queries

TENANT = "test_derive_stress"


def reset_graph(tenant_id: str):
    g = queries.get_graph(tenant_id, "commons")
    try:
        g.query("MATCH (n) DETACH DELETE n")
    except Exception:
        pass
    queries.ensure_indexes(tenant_id)
    return g


# =========================================================================
# TEST 1: Derive decisions with valid HEALTHY parents
# =========================================================================

def test_derive_decisions_valid_healthy_parents():
    """
    Test 1:
    - Single HEALTHY parent -> derive decision -> success
    - Multiple HEALTHY parents -> derive decision -> success, all LEADS_TO edges created
    - Chained decision derivation (Fact -> Dec1 -> Dec2) -> success
    """
    g = reset_graph(TENANT)

    # 1a. Single parent fact
    g.query("CREATE (:Fact:MemoryNode {id: 'f_h1', status: 'HEALTHY', text: 'Healthy Fact 1', stale: false})")
    res1 = queries.derive(TENANT, "dec_1", ["f_h1"], "Decision from single fact", is_decision=True, owner_team="RiskTeam")
    assert res1["success"] is True
    assert res1["conclusion_id"] == "dec_1"
    assert res1["status"] == "HEALTHY"

    # Verify node properties & labels
    node_res = g.query("MATCH (d:Decision {id: 'dec_1'}) RETURN d.id, labels(d), d.owner_team, d.text, d.status, d.stale").result_set
    assert len(node_res) == 1
    row = node_res[0]
    labels = row[1]
    assert "Decision" in labels and "Conclusion" in labels and "MemoryNode" in labels
    assert row[2] == "RiskTeam"
    assert row[3] == "Decision from single fact"
    assert row[4] == "HEALTHY"
    assert row[5] is False

    # Verify LEADS_TO edge
    edge_res = g.query("MATCH (f:Fact {id: 'f_h1'})-[r:LEADS_TO]->(d:Decision {id: 'dec_1'}) RETURN type(r)").result_set
    assert len(edge_res) == 1

    # 1b. Multiple healthy parents (3 parents)
    g.query("CREATE (:Fact:MemoryNode {id: 'f_m1', status: 'HEALTHY', text: 'M1', stale: false})")
    g.query("CREATE (:Fact:MemoryNode {id: 'f_m2', status: 'HEALTHY', text: 'M2', stale: false})")
    g.query("CREATE (:Fact:MemoryNode {id: 'f_m3', status: 'HEALTHY', text: 'M3', stale: false})")
    res_m = queries.derive(TENANT, "dec_multi", ["f_m1", "f_m2", "f_m3"], "Multi-fact decision", is_decision=True, owner_team="OpsTeam")
    assert res_m["success"] is True

    # Verify all 3 incoming edges
    edges_multi = g.query("MATCH (p:MemoryNode)-[:LEADS_TO]->(d:Decision {id: 'dec_multi'}) RETURN p.id").result_set
    p_ids = sorted([r[0] for r in edges_multi])
    assert p_ids == ["f_m1", "f_m2", "f_m3"]

    # 1c. Chained derivation: Fact -> Dec1 -> Dec2
    res_chain = queries.derive(TENANT, "dec_chained", ["dec_1"], "Chained decision", is_decision=True)
    assert res_chain["success"] is True
    chain_edge = g.query("MATCH (d1:Decision {id: 'dec_1'})-[:LEADS_TO]->(d2:Decision {id: 'dec_chained'}) RETURN d2.id").result_set
    assert len(chain_edge) == 1


# =========================================================================
# TEST 2: Derive decision where 1 parent is QUARANTINED
# =========================================================================

def test_derive_quarantined_parent_atomic_rejection():
    """
    Test 2:
    - 2 parents (1 HEALTHY, 1 QUARANTINED) -> atomically rejected
    - Verify 0 phantom nodes or edges created
    - All QUARANTINED parents -> atomically rejected
    - SUPERSEDED parent -> atomically rejected
    """
    g = reset_graph(TENANT)

    g.query("CREATE (:Fact:MemoryNode {id: 'f_ok1', status: 'HEALTHY', text: 'OK 1', stale: false})")
    g.query("CREATE (:Fact:MemoryNode {id: 'f_ok2', status: 'HEALTHY', text: 'OK 2', stale: false})")
    g.query("CREATE (:Fact:MemoryNode {id: 'f_bad', status: 'QUARANTINED', text: 'Quarantined Fact', stale: false, quarantined_at: timestamp()})")

    # 2a. Attempt derive with 1 QUARANTINED parent among HEALTHY ones
    res = queries.derive(TENANT, "dec_bad_parent", ["f_ok1", "f_bad", "f_ok2"], "Must fail", is_decision=True)
    assert res["success"] is False
    assert "Atomic derivation guard refused" in res["error"]

    # Verify atomicity: NO node created
    nodes = g.query("MATCH (n {id: 'dec_bad_parent'}) RETURN n").result_set
    assert len(nodes) == 0, f"Atomicity violation! Node created: {nodes}"
    edges = g.query("MATCH ()-[r]->(n {id: 'dec_bad_parent'}) RETURN r").result_set
    assert len(edges) == 0

    # 2b. Single quarantined parent
    res_single = queries.derive(TENANT, "dec_single_bad", ["f_bad"], "Single bad parent", is_decision=True)
    assert res_single["success"] is False
    assert len(g.query("MATCH (n {id: 'dec_single_bad'}) RETURN n").result_set) == 0

    # 2c. SUPERSEDED parent
    g.query("CREATE (:Fact:MemoryNode {id: 'f_super', status: 'SUPERSEDED', text: 'Superseded Fact', stale: false})")
    res_sup = queries.derive(TENANT, "dec_super_parent", ["f_super"], "Superseded parent", is_decision=True)
    assert res_sup["success"] is False
    assert len(g.query("MATCH (n {id: 'dec_super_parent'}) RETURN n").result_set) == 0


# =========================================================================
# TEST 3: Derive decision where 1 parent is non-existent
# =========================================================================

def test_derive_nonexistent_parent_atomic_rejection():
    """
    Test 3:
    - 1 parent real, 1 parent non-existent -> atomically rejected
    - All parents non-existent -> atomically rejected
    - Verify 0 phantom nodes created
    - Duplicate parent IDs -> atomically rejected (size mismatch)
    """
    g = reset_graph(TENANT)
    g.query("CREATE (:Fact:MemoryNode {id: 'f_exists', status: 'HEALTHY', text: 'Existing Fact', stale: false})")

    # 3a. 1 real, 1 non-existent
    res = queries.derive(TENANT, "dec_ghost", ["f_exists", "f_missing_999"], "Ghost parent", is_decision=True)
    assert res["success"] is False
    assert "Atomic derivation guard refused" in res["error"]
    assert len(g.query("MATCH (n {id: 'dec_ghost'}) RETURN n").result_set) == 0

    # 3b. All non-existent
    res_all_missing = queries.derive(TENANT, "dec_all_ghost", ["ghost_a", "ghost_b"], "All ghosts", is_decision=True)
    assert res_all_missing["success"] is False
    assert len(g.query("MATCH (n {id: 'dec_all_ghost'}) RETURN n").result_set) == 0

    # 3c. Duplicate parent IDs ['f_exists', 'f_exists']
    res_dup = queries.derive(TENANT, "dec_dup", ["f_exists", "f_exists"], "Duplicate parent ID", is_decision=True)
    assert res_dup["success"] is False
    assert len(g.query("MATCH (n {id: 'dec_dup'}) RETURN n").result_set) == 0


# =========================================================================
# TEST 4: Race Condition: Quarantine vs Derive at exact same millisecond
# =========================================================================

def test_derive_race_condition_concurrent_quarantine():
    """
    Test 4:
    Concurrent thread quarantines parent AT THE EXACT SAME MILLISECOND another thread attempts derive.
    Verify derive never succeeds on a quarantined parent and zero invariant violations.
    """
    g = reset_graph(TENANT)
    trials = 100
    derive_success_count = 0
    derive_rejected_count = 0

    for i in range(trials):
        parent_id = f"f_race_{i}"
        dec_id = f"dec_race_{i}"

        g.query(f"CREATE (:Fact:MemoryNode {{id: '{parent_id}', status: 'HEALTHY', text: 'Race Fact {i}', stale: false}})")

        barrier = threading.Barrier(2)
        results = {}

        def run_quarantine():
            barrier.wait()
            res = queries.cascade(TENANT, parent_id)
            results["quarantine"] = res

        def run_derive():
            barrier.wait()
            res = queries.derive(TENANT, dec_id, [parent_id], f"Race Decision {i}", is_decision=True)
            results["derive"] = res

        t1 = threading.Thread(target=run_quarantine)
        t2 = threading.Thread(target=run_derive)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        derive_res = results["derive"]

        # Check graph state for this pair
        check = g.query(f"""
        MATCH (p:Fact {{id: '{parent_id}'}})
        OPTIONAL MATCH (d:Decision {{id: '{dec_id}'}})
        RETURN p.status, coalesce(d.status, 'NOT_CREATED')
        """).result_set[0]

        p_status = check[0]
        d_status = check[1]

        if derive_res["success"]:
            derive_success_count += 1
            # If derive succeeded before cascade, cascade must have caught the decision and quarantined it
            assert d_status == "QUARANTINED", f"Trial {i}: Decision is {d_status} under QUARANTINED parent!"
        else:
            derive_rejected_count += 1
            assert d_status == "NOT_CREATED", f"Trial {i}: derive failed but decision node exists!"

    # Audit invariants
    inv = queries.invariant_check(TENANT)
    assert inv["healthy_children_under_quarantined"] == 0, f"Invariant violation detected: {inv}"
    assert inv["all_passed"] is True
    assert derive_success_count + derive_rejected_count == trials


# =========================================================================
# TEST 5: Derive with 10 parent facts simultaneously
# =========================================================================

def test_derive_10_parent_facts_simultaneously():
    """
    Test 5:
    - Derive with 10 parent facts simultaneously (all healthy) -> success & 10 incoming LEADS_TO edges
    - Derive with 10 parent facts where 1 is QUARANTINED -> atomic rejection
    - Derive with 10 parent facts where 1 is non-existent -> atomic rejection
    - Quarantine cascade on any 1 parent cascades to decision
    """
    g = reset_graph(TENANT)

    parent_ids = [f"f_10_{i}" for i in range(10)]
    for pid in parent_ids:
        g.query(f"CREATE (:Fact:MemoryNode {{id: '{pid}', status: 'HEALTHY', text: 'Fact {pid}', stale: false}})")

    # 5a. All 10 healthy
    t0 = time.perf_counter()
    res = queries.derive(TENANT, "dec_10_healthy", parent_ids, "Decision with 10 parents", is_decision=True, owner_team="MultiTeam")
    dur_ms = (time.perf_counter() - t0) * 1000
    assert res["success"] is True
    assert res["conclusion_id"] == "dec_10_healthy"

    # Verify all 10 incoming LEADS_TO edges
    edges = g.query("MATCH (p:Fact)-[:LEADS_TO]->(d:Decision {id: 'dec_10_healthy'}) RETURN p.id").result_set
    edge_pids = sorted([r[0] for r in edges])
    assert edge_pids == sorted(parent_ids)

    # 5b. 10 parents where 1 is QUARANTINED
    g.query("CREATE (:Fact:MemoryNode {id: 'f_10_bad', status: 'QUARANTINED', text: 'Quarantined 10th', stale: false})")
    parents_with_bad = parent_ids[:9] + ["f_10_bad"]
    res_bad = queries.derive(TENANT, "dec_10_bad", parents_with_bad, "Decision 1 bad", is_decision=True)
    assert res_bad["success"] is False
    assert "Atomic derivation guard refused" in res_bad["error"]
    assert len(g.query("MATCH (n {id: 'dec_10_bad'}) RETURN n").result_set) == 0

    # 5c. 10 parents where 1 is missing
    parents_with_missing = parent_ids[:9] + ["f_missing_nonexistent"]
    res_missing = queries.derive(TENANT, "dec_10_missing", parents_with_missing, "Decision 1 missing", is_decision=True)
    assert res_missing["success"] is False
    assert len(g.query("MATCH (n {id: 'dec_10_missing'}) RETURN n").result_set) == 0

    # 5d. Cascade test on 1 of the 10 parents
    casc = queries.cascade(TENANT, parent_ids[3])
    assert casc["success"] is True
    dec_state = g.query("MATCH (d:Decision {id: 'dec_10_healthy'}) RETURN d.status, d.quarantine_root").result_set[0]
    assert dec_state[0] == "QUARANTINED"
    assert dec_state[1] == parent_ids[3]


# =========================================================================
# TEST 6: Extreme Concurrent Barrage (10 Parallel Derives vs 1 Cascade)
# =========================================================================

def test_extreme_concurrent_barrage():
    """
    Test 6:
    10 threads concurrently derive from root fact while 1 thread cascades quarantine.
    Run across 20 rounds (200 derivation attempts).
    Assert zero invariant violations.
    """
    g = reset_graph(TENANT)
    rounds = 20

    for r in range(rounds):
        root_fact = f"fact_barrage_{r}"
        g.query(f"CREATE (:Fact:MemoryNode {{id: '{root_fact}', status: 'HEALTHY', text: 'Barrage Root {r}'}})")

        barrier = threading.Barrier(11)
        results = {}

        def derive_worker(idx):
            dec_id = f"dec_barrage_{r}_{idx}"
            barrier.wait()
            res = queries.derive(TENANT, dec_id, [root_fact], f"Barrage {idx}", is_decision=True)
            results[f"derive_{idx}"] = res

        def quarantine_worker():
            barrier.wait()
            res = queries.cascade(TENANT, root_fact)
            results["quarantine"] = res

        threads = [threading.Thread(target=derive_worker, args=(i,)) for i in range(10)]
        threads.append(threading.Thread(target=quarantine_worker))

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Invariant check after each round
        inv = queries.invariant_check(TENANT)
        assert inv["healthy_children_under_quarantined"] == 0, f"Invariant failed in round {r}: {inv}"

    inv_final = queries.invariant_check(TENANT)
    assert inv_final["all_passed"] is True


# =========================================================================
# TEST 7: Logic & Parameter Edge Cases
# =========================================================================

def test_derive_logic_and_parameters():
    """
    Test 7:
    - Empty parents list `[]` -> safely rejected
    - is_decision=False vs is_decision=True labels
    - Higher parent counts: 20 and 50 parents
    """
    g = reset_graph(TENANT)

    # 7a. Empty parents list
    res_empty = queries.derive(TENANT, "dec_no_parents", [], "No parents", is_decision=True)
    assert res_empty["success"] is False
    assert len(g.query("MATCH (n {id: 'dec_no_parents'}) RETURN n").result_set) == 0

    # 7b. is_decision=False labels
    g.query("CREATE (:Fact:MemoryNode {id: 'f_p1', status: 'HEALTHY', text: 'P1'})")
    res_conc = queries.derive(TENANT, "conc_1", ["f_p1"], "Plain conclusion", is_decision=False)
    assert res_conc["success"] is True
    labels = g.query("MATCH (c {id: 'conc_1'}) RETURN labels(c)").result_set[0][0]
    assert "Conclusion" in labels and "MemoryNode" in labels
    assert "Decision" not in labels

    # 7c. 50 parents
    pids_50 = [f"f_50_{i}" for i in range(50)]
    for pid in pids_50:
        g.query(f"CREATE (:Fact:MemoryNode {{id: '{pid}', status: 'HEALTHY', text: 'Fact {pid}'}})")

    res_50 = queries.derive(TENANT, "dec_50", pids_50, "Decision 50 parents", is_decision=True)
    assert res_50["success"] is True
    edges_50 = g.query("MATCH (p)-[:LEADS_TO]->(d:Decision {id: 'dec_50'}) RETURN count(p)").result_set[0][0]
    assert edges_50 == 50
