"""
Unit and Stress Tests for T03-2: Who Owns What & Who Knows What (who_knows)
pytest tests/test_who_knows_stress.py -v
"""

import pytest
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from graph import queries

TENANT = "test_pytest_who_knows"


@pytest.fixture(autouse=True)
def clean_graph():
    g = queries.get_graph(TENANT, "commons")
    try:
        g.delete()
    except Exception:
        pass
    queries.ensure_indexes(TENANT)
    yield


class TestWhoKnowsScoringAndRanking:
    """Test 1: Scoring and ranking across multiple topics ('billing', 'kubernetes', 'auth')."""

    def test_multi_topic_ranking(self):
        g = queries.get_graph(TENANT, "commons")

        # Create topics
        g.query("CREATE (:Topic {id: 'billing', name: 'Billing'})")
        g.query("CREATE (:Topic {id: 'kubernetes', name: 'Kubernetes'})")
        g.query("CREATE (:Topic {id: 'auth', name: 'Authentication'})")

        # Create people & teams
        g.query("CREATE (:Team {id: 'team_bill', name: 'Billing Team'})")
        g.query("CREATE (:Person {id: 'p_alice', name: 'Alice'})")
        g.query("CREATE (:Person {id: 'p_bob', name: 'Bob'})")
        g.query("CREATE (:Person {id: 'p_charlie', name: 'Charlie'})")
        g.query("CREATE (:Person {id: 'p_dana', name: 'Dana'})")

        # Alice: 3 authored, 2 resolved on billing (5 evidence)
        for i in range(3):
            g.query(
                "CREATE (s:Source {id: $sid, kind: 'DOC'}) "
                "WITH s MATCH (p:Person {id: 'p_alice'}), (t:Topic {id: 'billing'}) "
                "CREATE (p)-[:AUTHORED]->(s), (s)-[:ABOUT]->(t)",
                {"sid": f"src_a_{i}"}
            )
        for i in range(2):
            g.query(
                "CREATE (s:Source {id: $sid, kind: 'TICKET'}) "
                "WITH s MATCH (p:Person {id: 'p_alice'}), (t:Topic {id: 'billing'}) "
                "CREATE (p)-[:RESOLVED]->(s), (s)-[:ABOUT]->(t)",
                {"sid": f"src_ar_{i}"}
            )

        # Bob: 3 authored on billing (3 evidence)
        for i in range(3):
            g.query(
                "CREATE (s:Source {id: $sid, kind: 'DOC'}) "
                "WITH s MATCH (p:Person {id: 'p_bob'}), (t:Topic {id: 'billing'}) "
                "CREATE (p)-[:AUTHORED]->(s), (s)-[:ABOUT]->(t)",
                {"sid": f"src_b_{i}"}
            )

        # Charlie: 1 resolved on billing (1 evidence)
        g.query(
            "CREATE (s:Source {id: 'src_c_1', kind: 'TICKET'}) "
            "WITH s MATCH (p:Person {id: 'p_charlie'}), (t:Topic {id: 'billing'}) "
            "CREATE (p)-[:RESOLVED]->(s), (s)-[:ABOUT]->(t)"
        )

        # Dana: 0 sources

        res = queries.who_knows(TENANT, "billing")
        assert len(res) == 3
        assert res[0]["person_id"] == "p_alice"
        assert res[0]["evidence_count"] == 5
        assert res[1]["person_id"] == "p_bob"
        assert res[1]["evidence_count"] == 3
        assert res[2]["person_id"] == "p_charlie"
        assert res[2]["evidence_count"] == 1
        assert not any(r["person_id"] == "p_dana" for r in res)


class TestComplexTopologies:
    """Test 2: Complex graph topologies (shared ownership, multi-team, orphans, cycles)."""

    def test_shared_ownership_and_coauthoring(self):
        g = queries.get_graph(TENANT, "commons")
        g.query("""
        CREATE (top:Topic {id: 'top_shared', name: 'Shared'})
        CREATE (p1:Person {id: 'p1', name: 'Author 1'})
        CREATE (p2:Person {id: 'p2', name: 'Author 2'})
        CREATE (t1:Team {id: 't1', name: 'Team 1'})
        CREATE (t2:Team {id: 't2', name: 'Team 2'})
        CREATE (s:Source {id: 's_shared', kind: 'DOC'})
        CREATE (p1)-[:AUTHORED]->(s), (p2)-[:AUTHORED]->(s)
        CREATE (t1)-[:OWNS]->(s), (t2)-[:OWNS]->(s)
        CREATE (s)-[:ABOUT]->(top)
        """)
        res = queries.who_knows(TENANT, "top_shared")
        assert len(res) == 2
        assert {r["person_id"] for r in res} == {"p1", "p2"}
        assert all(r["evidence_count"] == 1 for r in res)

    def test_orphan_topics(self):
        g = queries.get_graph(TENANT, "commons")
        g.query("CREATE (:Topic {id: 'top_empty', name: 'Empty Orphan'})")
        assert queries.who_knows(TENANT, "top_empty") == []

        # Orphan with unowned source but has author
        g.query("""
        CREATE (top:Topic {id: 'top_unowned', name: 'Unowned Topic'})
        CREATE (s:Source {id: 's_unowned', kind: 'DOC'})
        CREATE (p:Person {id: 'p_solo', name: 'Solo'})
        CREATE (p)-[:AUTHORED]->(s), (s)-[:ABOUT]->(top)
        """)
        res = queries.who_knows(TENANT, "top_unowned")
        assert len(res) == 1
        assert res[0]["person_id"] == "p_solo"

    def test_circular_contribution_chains(self):
        g = queries.get_graph(TENANT, "commons")
        g.query("""
        CREATE (top:Topic {id: 'top_cycle', name: 'Cycle Topic'})
        CREATE (pa:Person {id: 'pa', name: 'Person A'})
        CREATE (pb:Person {id: 'pb', name: 'Person B'})
        CREATE (s1:Source {id: 's1', kind: 'DOC'})
        CREATE (s2:Source {id: 's2', kind: 'DOC'})
        CREATE (pa)-[:AUTHORED]->(s1), (pb)-[:RESOLVED]->(s1), (s1)-[:ABOUT]->(top)
        CREATE (pb)-[:AUTHORED]->(s2), (pa)-[:RESOLVED]->(s2), (s2)-[:ABOUT]->(top)
        CREATE (s1)-[:REFERENCES]->(s2), (s2)-[:REFERENCES]->(s1)
        """)
        t0 = time.time()
        res = queries.who_knows(TENANT, "top_cycle")
        ms = (time.time() - t0) * 1000
        assert ms < 200, "Circular query should not hang"
        assert len(res) == 2
        assert {r["evidence_count"] for r in res} == {2}


class TestConcurrencyAndDynamicWrites:
    """Test 3: 100 queries across 10 threads while dynamically adding edges."""

    def test_concurrent_100_queries_with_dynamic_writes(self):
        g = queries.get_graph(TENANT, "commons")
        g.query("CREATE (:Topic {id: 'conc_topic', name: 'Concurrency'})")
        g.query("CREATE (:Person {id: 'p_base', name: 'Base'})")
        g.query("""
        CREATE (s:Source {id: 's_base', kind: 'DOC'})
        WITH s MATCH (p:Person {id: 'p_base'}), (t:Topic {id: 'conc_topic'})
        CREATE (p)-[:AUTHORED]->(s), (s)-[:ABOUT]->(t)
        """)

        stop_writer = threading.Event()
        writer_errors = []

        def writer():
            wg = queries.get_graph(TENANT, "commons")
            idx = 0
            while not stop_writer.is_set():
                try:
                    pid = f"dyn_p_{idx}"
                    sid = f"dyn_s_{idx}"
                    wg.query(
                        "CREATE (p:Person {id: $pid, name: $pid}) "
                        "CREATE (s:Source {id: $sid, kind: 'DOC'}) "
                        "WITH p, s MATCH (t:Topic {id: 'conc_topic'}) "
                        "CREATE (p)-[:AUTHORED]->(s), (s)-[:ABOUT]->(t) "
                        "CREATE (p)-[:KNOWS]->(t)",
                        {"pid": pid, "sid": sid}
                    )
                    idx += 1
                    time.sleep(0.01)
                except Exception as e:
                    writer_errors.append(str(e))

        t = threading.Thread(target=writer)
        t.start()

        num_threads = 10
        queries_per_thread = 10
        total_queries = num_threads * queries_per_thread
        reader_errors = []

        def reader():
            for _ in range(queries_per_thread):
                try:
                    r = queries.who_knows(TENANT, "conc_topic")
                    assert len(r) >= 1
                except Exception as e:
                    reader_errors.append(str(e))
                time.sleep(0.005)

        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(reader) for _ in range(num_threads)]
            for f in as_completed(futures):
                f.result()

        stop_writer.set()
        t.join()

        assert len(reader_errors) == 0
        assert len(writer_errors) == 0


class TestBoundaryConditions:
    """Test 4: Non-existent topic, empty graph, and 50+ contributors."""

    def test_non_existent_topic(self):
        res = queries.who_knows(TENANT, "non_existent_topic_404")
        assert res == []

    def test_empty_graph(self):
        empty_tenant = "empty_tenant_boundary"
        g = queries.get_graph(empty_tenant, "commons")
        try:
            g.delete()
        except Exception:
            pass
        res = queries.who_knows(empty_tenant, "any_topic")
        assert res == []

    def test_topic_with_50_plus_contributors(self):
        g = queries.get_graph(TENANT, "commons")
        g.query("CREATE (:Topic {id: 'large_topic', name: 'Large Topic'})")

        num_contributors = 55
        for i in range(1, num_contributors + 1):
            pid = f"p_usr_{i:02d}"
            count = num_contributors - i + 1
            g.query(
                "CREATE (p:Person {id: $pid, name: $pid}) "
                "WITH p MATCH (t:Topic {id: 'large_topic'}) "
                "UNWIND range(1, $count) AS idx "
                "CREATE (s:Source {id: $pid + '_s_' + toString(idx), kind: 'DOC'}) "
                "CREATE (p)-[:AUTHORED]->(s), (s)-[:ABOUT]->(t)",
                {"pid": pid, "count": count}
            )

        t0 = time.time()
        res = queries.who_knows(TENANT, "large_topic")
        ms = (time.time() - t0) * 1000

        assert len(res) == num_contributors
        assert res[0]["person_id"] == "p_usr_01"
        assert res[0]["evidence_count"] == 55
        assert res[-1]["person_id"] == f"p_usr_{num_contributors:02d}"
        assert res[-1]["evidence_count"] == 1
        assert ms < 50, f"Query on 55 contributors should be fast (<50ms), got {ms:.2f}ms"
