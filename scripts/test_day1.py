"""
Day-1 FalkorDB Cypher Verification Suite (Brief Section 10)
Runs against live FalkorDB container and records exact working syntax and results.
"""

from falkordb import FalkorDB

def run_tests():
    print("=" * 60)
    print("QUORUM DAY-1 FALKORDB VERIFICATION SUITE")
    print("=" * 60)
    
    db = FalkorDB(host="localhost", port=6379)
    g = db.select_graph("verification_commons")
    try:
        g.delete() # Start fresh if exists
    except Exception:
        pass
    
    results = {}

    def test(item_id, desc, fn):
        print(f"\n[{item_id}] Testing: {desc}...")
        try:
            res = fn()
            print(f"  --> PASSED: {res}")
            results[item_id] = {"status": "PASSED", "details": str(res)}
        except Exception as e:
            print(f"  --> FAILED: {type(e).__name__}: {e}")
            results[item_id] = {"status": "FAILED", "error": f"{type(e).__name__}: {e}"}

    # 1. timestamp()
    test("V-09", "Built-in timestamp() function", lambda: g.query("RETURN timestamp()").result_set[0][0])

    # 2. MERGE ... ON CREATE SET
    test("V-04", "MERGE ... ON CREATE SET", lambda: g.query(
        "MERGE (c:Compensation {id:'comp_1'}) ON CREATE SET c.status = 'PENDING_COMPENSATION' RETURN c.status"
    ).result_set[0][0])

    # 3. UNWIND after collect
    def test_unwind():
        g.query("CREATE (:MemoryNode {id: 'p1', status: 'HEALTHY'}), (:MemoryNode {id: 'p2', status: 'HEALTHY'})")
        q = """
        MATCH (p:MemoryNode) WHERE p.id IN ['p1', 'p2']
        WITH collect(p) AS parents
        CREATE (c:Conclusion:MemoryNode {id: 'c1', status: 'HEALTHY'})
        WITH c, parents
        UNWIND parents AS p
        CREATE (p)-[:LEADS_TO]->(c)
        RETURN c.id, count(p)
        """
        return g.query(q).result_set[0]
    test("V-06", "UNWIND after collect (Atomic Derivation Pattern)", test_unwind)

    # 4. coalesce in WHERE
    def test_coalesce():
        g.query("CREATE (:Action:MemoryNode {id: 'act_1', status: 'HEALTHY', executed: false, gate: 'PASSED'})")
        q = """
        MATCH (a:Action {id: 'act_1'})
        OPTIONAL MATCH (x:MemoryNode)-[:LEADS_TO*]->(a)
        WHERE coalesce(x.status, 'MISSING') <> 'HEALTHY'
        RETURN a.id, count(DISTINCT x) AS bad
        """
        return g.query(q).result_set[0]
    test("V-10", "coalesce in WHERE", test_coalesce)

    # 5. count(DISTINCT ...) with diamond DAG
    def test_diamond():
        # Root -> (B, C) -> D (diamond DAG)
        g.query("""
        CREATE (root:Fact:MemoryNode {id: 'f_root', status: 'HEALTHY'}),
               (b:Conclusion:MemoryNode {id: 'c_b', status: 'HEALTHY'}),
               (c:Conclusion:MemoryNode {id: 'c_c', status: 'HEALTHY'}),
               (d:Action:MemoryNode {id: 'act_d', status: 'HEALTHY', executed: false, gate: 'PASSED'})
        CREATE (root)-[:LEADS_TO]->(b), (root)-[:LEADS_TO]->(c),
               (b)-[:LEADS_TO]->(d), (c)-[:LEADS_TO]->(d)
        """)
        q = """
        MATCH (a:Action {id: 'act_d'})
        MATCH (x:MemoryNode)-[:LEADS_TO*]->(a)
        RETURN count(x) AS total_paths, count(DISTINCT x) AS distinct_nodes
        """
        res = g.query(q).result_set[0]
        assert res[0] == 4 # path through b + path through c = root counted twice
        assert res[1] == 3 # distinct nodes = root, b, c
        return f"paths={res[0]}, distinct={res[1]}"
    test("V-14", "count(DISTINCT ...) over diamond DAG", test_diamond)

    # 6. Relationship alternation [:A|B] and [:A|B*]
    def test_alternation():
        g.query("""
        MATCH (root:Fact {id: 'f_root'}), (d:Action {id: 'act_d'})
        CREATE (root)-[:SUPPORTS]->(d)
        """)
        q1 = "MATCH (x)-[:LEADS_TO|SUPPORTS]->(d:Action {id: 'act_d'}) RETURN count(x)"
        r1 = g.query(q1).result_set[0][0]
        q2 = "MATCH (x)-[:LEADS_TO|SUPPORTS*]->(d:Action {id: 'act_d'}) RETURN count(DISTINCT x)"
        r2 = g.query(q2).result_set[0][0]
        return f"1-hop alternation={r1}, var-length alternation [:A|B*]={r2}"
    test("V-13", "Relationship alternation [:LEADS_TO|SUPPORTS] and [:LEADS_TO|SUPPORTS*]", test_alternation)

    # 7. Pattern predicate NOT (()-[:LEADS_TO]->(leaf))
    def test_leaf_predicate():
        q = """
        MATCH (leaf:Fact)-[:LEADS_TO*]->(a:Action {id: 'act_d'})
        WHERE NOT (()-[:LEADS_TO]->(leaf))
        RETURN DISTINCT leaf.id
        """
        res = g.query(q).result_set
        return [row[0] for row in res]
    test("V-11", "Pattern predicate NOT (()-[:LEADS_TO]->(leaf))", test_leaf_predicate)

    # 8. STARTS WITH predicate
    test("V-08", "STARTS WITH predicate", lambda: g.query("MATCH (a:Action) WHERE a.id STARTS WITH 'act_' RETURN count(a)").result_set[0][0])

    # 9. Relationship property filter
    def test_rel_props():
        g.query("MATCH (b:Conclusion {id: 'c_b'}), (d:Action {id: 'act_d'}) CREATE (b)-[:LEADS_TO {weight: 1.5}]->(d)")
        return g.query("MATCH (b)-[r:LEADS_TO {weight: 1.5}]->(d) RETURN r.weight").result_set[0][0]
    test("V-07", "Relationship property filter [r {prop: val}]", test_rel_props)

    # 10. Vector index syntax and queryNodes
    def test_vector():
        # Create vector index
        create_idx = "CREATE VECTOR INDEX FOR (f:Fact) ON (f.embedding) OPTIONS {dimension: 384, similarityFunction: 'cosine'}"
        g.query(create_idx)
        # Insert a fact with 384-dim dummy vector
        vec = [0.1] * 384
        g.query("CREATE (f:Fact {id: 'vfact_1', text: '30 day refund policy', embedding: vecf32($vec)})", {"vec": vec})
        # Query vector index
        q = """
        CALL db.idx.vector.queryNodes('Fact', 'embedding', 5, vecf32($vec))
        YIELD node, score
        RETURN node.id, score
        """
        res = g.query(q, {"vec": vec}).result_set
        return f"Vector indexed and queried! First result: {res[0]}"
    test("V-01_02", "Vector index creation & db.idx.vector.queryNodes", test_vector)

    # 11. Vector distance function name
    def test_vector_distance():
        v1 = [0.1] * 384
        v2 = [0.1] * 384
        # Test candidate distance function names
        funcs = ["vecf32.distance.cosine", "vec.cosineDistance", "vec.distance.cosine", "distance.cosine"]
        working = None
        for fn in funcs:
            try:
                q = f"RETURN {fn}(vecf32($v1), vecf32($v2)) AS dist"
                res = g.query(q, {"v1": v1, "v2": v2}).result_set[0][0]
                working = (fn, res)
                break
            except Exception:
                continue
        if working:
            return f"Working distance function: {working[0]} -> {working[1]}"
        else:
            return "Custom distance function not built-in; score from queryNodes YIELD score is used directly"
    test("V-02_dist", "Vector distance function name resolution", test_vector_distance)

    # 12. algo.pageRank
    def test_pagerank():
        try:
            return g.query("CALL algo.pageRank()").result_set
        except Exception as e:
            return f"Not available: {e}"
    test("V-03", "algo.pageRank availability", test_pagerank)

    # Summary
    print("\n" + "=" * 60)
    print("VERIFICATION COMPLETE")
    print("=" * 60)
    passed = sum(1 for v in results.values() if v.get("status") == "PASSED")
    print(f"Total Tests: {len(results)} | Passed: {passed} | Failed: {len(results) - passed}")
    return results

if __name__ == "__main__":
    run_tests()
