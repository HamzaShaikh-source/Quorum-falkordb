"""
Subagent 11: Extreme Concurrent Stress Test
Tests:
1. High-throughput multi-thread derivation race (10 concurrent derive workers vs 1 quarantine cascade worker).
2. Large parent sets: 20, 50 parents simultaneous derivation.
3. Latency distribution and performance benchmarking.
"""

import time
import threading
from graph import queries

TENANT = "stress_derive_extreme"

def setup_tenant():
    g = queries.get_graph(TENANT, "commons")
    try:
        g.delete()
    except Exception:
        pass
    queries.ensure_indexes(TENANT)
    return g

def test_extreme_multithread_race():
    print("\n--- EXTREME TEST 1: 10 Concurrent Derivations vs 1 Quarantine Cascade ---")
    g = setup_tenant()
    rounds = 30
    total_derivations_attempted = rounds * 10
    derivations_succeeded = 0
    derivations_rejected = 0
    
    for r in range(rounds):
        root_fact = f"fact_extreme_{r}"
        g.query(f"CREATE (:Fact:MemoryNode {{id: '{root_fact}', status: 'HEALTHY', text: 'Root {r}'}})")
        
        barrier = threading.Barrier(11) # 10 derive workers + 1 quarantine worker
        results = {}
        
        def derive_worker(worker_idx):
            dec_id = f"dec_{r}_{worker_idx}"
            barrier.wait()
            res = queries.derive(TENANT, dec_id, [root_fact], f"Worker {worker_idx} decision", is_decision=True)
            results[f"derive_{worker_idx}"] = res
            
        def quarantine_worker():
            barrier.wait()
            # slight jitter or instant trigger
            res = queries.cascade(TENANT, root_fact)
            results["quarantine"] = res
            
        threads = [threading.Thread(target=derive_worker, args=(i,)) for i in range(10)]
        threads.append(threading.Thread(target=quarantine_worker))
        
        for t in threads:
            t.start()
        for t in threads:
            t.join()
            
        for i in range(10):
            res = results[f"derive_{i}"]
            if res["success"]:
                derivations_succeeded += 1
            else:
                derivations_rejected += 1
                
        # Immediately verify invariant for this round
        inv = queries.invariant_check(TENANT)
        assert inv["healthy_children_under_quarantined"] == 0, f"Invariant failed in round {r}: {inv}"
        
    print(f"  Rounds executed: {rounds}")
    print(f"  Total derive attempts: {total_derivations_attempted}")
    print(f"  Derives succeeded (and subsequently quarantined by cascade): {derivations_succeeded}")
    print(f"  Derives rejected atomically by guard: {derivations_rejected}")
    
    inv_final = queries.invariant_check(TENANT)
    print(f"  Final invariant audit: {inv_final}")
    assert inv_final["all_passed"] is True
    print("  --> EXTREME RACE TEST PASSED: Zero invariant violations across all rounds!\n")


def test_large_parent_sets():
    print("--- EXTREME TEST 2: High Cardinality Parent Facts (20 and 50 parents) ---")
    g = setup_tenant()
    
    for count in [20, 50]:
        parent_ids = [f"f_{count}_{i}" for i in range(count)]
        for pid in parent_ids:
            g.query(f"CREATE (:Fact:MemoryNode {{id: '{pid}', status: 'HEALTHY', text: 'Fact {pid}'}})")
            
        t0 = time.perf_counter()
        res = queries.derive(TENANT, f"dec_{count}", parent_ids, f"Decision with {count} parents", is_decision=True)
        t_el = (time.perf_counter() - t0) * 1000
        assert res["success"] is True, f"Failed for {count} parents: {res}"
        
        # Verify edge count
        edges = g.query(f"MATCH (p)-[:LEADS_TO]->(d:Decision {{id: 'dec_{count}'}}) RETURN count(p)").result_set[0][0]
        assert edges == count, f"Expected {count} edges, got {edges}"
        print(f"  Derive with {count} parents: SUCCESS in {t_el:.2f} ms ({edges} edges created)")
        
        # Verify atomic rejection if 1 parent is quarantined
        g.query(f"MATCH (p:Fact {{id: '{parent_ids[0]}'}}) SET p.status = 'QUARANTINED'")
        t0 = time.perf_counter()
        res_fail = queries.derive(TENANT, f"dec_{count}_fail", parent_ids, f"Failing {count} parents", is_decision=True)
        t_el_fail = (time.perf_counter() - t0) * 1000
        assert res_fail["success"] is False
        assert "Atomic derivation guard refused" in res_fail["error"]
        print(f"  Derive with {count} parents (1 quarantined): ATOMICALLY REJECTED in {t_el_fail:.2f} ms")


def test_repeated_derive_id_conflict():
    print("\n--- EXTREME TEST 3: Duplicate/Conflicting Decision IDs ---")
    g = setup_tenant()
    g.query("CREATE (:Fact:MemoryNode {id: 'f_base', status: 'HEALTHY', text: 'Base Fact'})")
    
    res1 = queries.derive(TENANT, "dec_unique", ["f_base"], "Initial decision", is_decision=True)
    assert res1["success"] is True
    
    # Attempting to derive with the exact same decision ID
    # Since cypher uses CREATE (not MERGE), FalkorDB creates another node or what happens?
    res2 = queries.derive(TENANT, "dec_unique", ["f_base"], "Second decision with same ID", is_decision=True)
    nodes = g.query("MATCH (d:Decision {id: 'dec_unique'}) RETURN count(d)").result_set[0][0]
    print(f"  Calling derive twice with same ID: nodes with ID={nodes}")
    # Note: Cypher CREATE doesn't enforce unique constraints unless unique index is defined.
    # In FalkorDB schema, indexes are range indexes by default unless unique constraints are used.

if __name__ == "__main__":
    test_extreme_multithread_race()
    test_large_parent_sets()
    test_repeated_derive_id_conflict()
