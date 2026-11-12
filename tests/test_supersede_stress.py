"""
tests/test_supersede_stress.py

Subagent 09: Stress-Testing T03-4 Knowledge Currency, SUPERSEDES Links & Stale Propagation (supersede_with_review)
Covers:
1. Construct multi-tier tree: Fact -> Conclusion -> 3 Decisions -> 5 Actions.
2. Call supersede_with_review with a new replacement Fact.
3. Verify SUPERSEDES edge is created pointing new -> old (schema standard).
4. Verify stale=True cascades to ALL downstream nodes (Conclusion, Decisions, Actions).
5. Verify REVIEW handoffs are automatically created for all downstream decisions with context_root properly set.
6. Test chain of supersessions: Fact v1 -> Fact v2 -> Fact v3 and verify stale propagation across historical chain.
"""

import graph.queries as queries

TENANT = "test_subagent_09_stress"


def reset_graph(tenant_id: str):
    g = queries.get_graph(tenant_id, "commons")
    try:
        g.query("MATCH (n) DETACH DELETE n")
    except Exception:
        pass
    queries.ensure_indexes(tenant_id)


class TestSupersedeWithReviewStress:

    def test_01_multi_tier_tree_construction_and_supersede(self):
        """
        1. Construct multi-tier tree: Fact -> Conclusion -> 3 Decisions -> 5 Actions.
        2. Call supersede_with_review with new replacement Fact.
        3. Verify SUPERSEDES edge and node statuses.
        """
        reset_graph(TENANT)
        g = queries.get_graph(TENANT, "commons")

        # Step 1: Multi-tier tree
        tree_cypher = """
        CREATE (f:Fact:MemoryNode {
            id: 'fact_tier_root',
            key: 'refund_window_days',
            text: 'Standard refund window is 30 days',
            value: '30',
            author: 'compliance_officer',
            status: 'HEALTHY',
            stale: false,
            valid_from: timestamp()
        })
        CREATE (c:Conclusion:MemoryNode {
            id: 'conc_tier_1',
            text: 'Refund requests within 30 days are eligible',
            status: 'HEALTHY',
            stale: false,
            created_at: timestamp()
        })
        CREATE (f)-[:LEADS_TO]->(c)

        CREATE (d1:Decision:MemoryNode {
            id: 'dec_tier_us',
            text: 'Approve refund under standard 30-day policy (US)',
            status: 'HEALTHY',
            stale: false,
            created_at: timestamp()
        })
        CREATE (d2:Decision:MemoryNode {
            id: 'dec_tier_eu',
            text: 'Approve refund under standard 30-day policy (EU)',
            status: 'HEALTHY',
            stale: false,
            created_at: timestamp()
        })
        CREATE (d3:Decision:MemoryNode {
            id: 'dec_tier_apac',
            text: 'Approve refund under standard 30-day policy (APAC)',
            status: 'HEALTHY',
            stale: false,
            created_at: timestamp()
        })
        CREATE (c)-[:LEADS_TO]->(d1)
        CREATE (c)-[:LEADS_TO]->(d2)
        CREATE (c)-[:LEADS_TO]->(d3)

        CREATE (a1:Action:MemoryNode {
            id: 'act_tier_1', type: 'stripe_refund', text: 'Issue Stripe refund 1',
            status: 'HEALTHY', stale: false, executed: false, created_at: timestamp()
        })
        CREATE (a2:Action:MemoryNode {
            id: 'act_tier_2', type: 'stripe_refund', text: 'Issue Stripe refund 2',
            status: 'HEALTHY', stale: false, executed: false, created_at: timestamp()
        })
        CREATE (a3:Action:MemoryNode {
            id: 'act_tier_3', type: 'adyen_refund', text: 'Issue Adyen refund 3',
            status: 'HEALTHY', stale: false, executed: false, created_at: timestamp()
        })
        CREATE (a4:Action:MemoryNode {
            id: 'act_tier_4', type: 'alipay_refund', text: 'Issue Alipay refund 4',
            status: 'HEALTHY', stale: false, executed: false, created_at: timestamp()
        })
        CREATE (a5:Action:MemoryNode {
            id: 'act_tier_5', type: 'alipay_refund', text: 'Issue Alipay refund 5',
            status: 'HEALTHY', stale: false, executed: false, created_at: timestamp()
        })
        CREATE (d1)-[:LEADS_TO]->(a1)
        CREATE (d1)-[:LEADS_TO]->(a2)
        CREATE (d2)-[:LEADS_TO]->(a3)
        CREATE (d3)-[:LEADS_TO]->(a4)
        CREATE (d3)-[:LEADS_TO]->(a5)
        """
        g.query(tree_cypher)

        # Verify initial state
        assert g.query("MATCH (f:Fact) RETURN count(f)").result_set[0][0] == 1
        assert g.query("MATCH (c:Conclusion) RETURN count(c)").result_set[0][0] == 1
        assert g.query("MATCH (d:Decision) RETURN count(d)").result_set[0][0] == 3
        assert g.query("MATCH (a:Action) RETURN count(a)").result_set[0][0] == 5

        # Step 2: Call supersede_with_review
        res_sup = queries.supersede_with_review(
            tenant_id=TENANT,
            old_fact_key="refund_window_days",
            new_fact_id="fact_tier_v2",
            new_text="Standard refund window is 45 days (updated policy)",
            new_value="45",
            author="policy_admin",
            source_url="https://internal.ops/refund-policy-v2",
        )
        assert res_sup["success"] is True
        assert res_sup["old_fact_key"] == "refund_window_days"
        assert res_sup["new_fact_id"] == "fact_tier_v2"
        # 1 conclusion + 3 decisions + 5 actions = 9 nodes stale
        assert res_sup["stale_propagated"] == 9
        assert res_sup["review_handoffs_created"] == 3

        # Step 3: Verify SUPERSEDES edge
        res_edge = g.query("""
            MATCH (new:Fact {id: 'fact_tier_v2'})-[r:SUPERSEDES]->(old:Fact {id: 'fact_tier_root'})
            RETURN type(r), r.at
        """)
        assert len(res_edge.result_set) == 1
        assert res_edge.result_set[0][0] == "SUPERSEDES"
        assert res_edge.result_set[0][1] is not None

        # Verify old fact status
        res_old = g.query("MATCH (f:Fact {id: 'fact_tier_root'}) RETURN f.status, f.stale, f.superseded_at")
        assert res_old.result_set[0][0] == "SUPERSEDED"
        assert res_old.result_set[0][1] is False
        assert res_old.result_set[0][2] is not None

        # Verify new fact status
        res_new = g.query("MATCH (f:Fact {id: 'fact_tier_v2'}) RETURN f.status, f.stale, f.value")
        assert res_new.result_set[0][0] == "HEALTHY"
        assert res_new.result_set[0][1] is False
        assert res_new.result_set[0][2] == "45"

    def test_02_verify_stale_cascades_to_all_downstream_nodes(self):
        """
        Verify stale=True cascades to ALL downstream nodes (Conclusion, Decisions, Actions).
        """
        g = queries.get_graph(TENANT, "commons")

        # Verify Conclusion stale
        res_c = g.query("MATCH (c:Conclusion {id: 'conc_tier_1'}) RETURN c.stale, c.stale_since")
        assert len(res_c.result_set) == 1
        assert res_c.result_set[0][0] is True
        assert res_c.result_set[0][1] is not None

        # Verify all 3 Decisions stale
        res_d = g.query("""
            MATCH (d:Decision) WHERE d.id IN ['dec_tier_us', 'dec_tier_eu', 'dec_tier_apac']
            RETURN d.id, d.stale, d.stale_since
        """)
        assert len(res_d.result_set) == 3
        for row in res_d.result_set:
            assert row[1] is True, f"Decision {row[0]} should be stale"
            assert row[2] is not None

        # Verify all 5 Actions stale
        res_a = g.query("""
            MATCH (a:Action) WHERE a.id IN ['act_tier_1', 'act_tier_2', 'act_tier_3', 'act_tier_4', 'act_tier_5']
            RETURN a.id, a.stale, a.stale_since
        """)
        assert len(res_a.result_set) == 5
        for row in res_a.result_set:
            assert row[1] is True, f"Action {row[0]} should be stale"
            assert row[2] is not None

    def test_03_verify_review_handoffs_created_with_context_root(self):
        """
        Verify REVIEW handoffs are automatically created for all downstream decisions
        with context_root properly set.
        """
        g = queries.get_graph(TENANT, "commons")

        res_h = g.query("""
            MATCH (d:Decision)-[:REQUIRES_REVIEW]->(h:Handoff)
            RETURN d.id, h.id, h.kind, h.status, h.context_root, h.assigned_to, h.reason
        """)
        assert len(res_h.result_set) == 3
        decisions_covered = set()
        for row in res_h.result_set:
            dec_id = row[0]
            decisions_covered.add(dec_id)
            assert row[2] == "REVIEW", "Handoff kind must be REVIEW"
            assert row[3] == "PENDING", "Handoff status must be PENDING"
            assert row[4] == dec_id, "Handoff context_root must equal decision ID"
            assert row[5] == "ops-team"
            assert "superseded" in row[6].lower()

        assert decisions_covered == {"dec_tier_us", "dec_tier_eu", "dec_tier_apac"}

    def test_04_chain_of_supersessions_and_historical_propagation(self):
        """
        Test chain of supersessions: Fact v1 -> Fact v2 -> Fact v3.
        Verify stale propagation across the full historical chain.
        Verify only 1 new node per version is created (no duplicates).
        """
        chain_tenant = f"{TENANT}_chain"
        reset_graph(chain_tenant)
        gc = queries.get_graph(chain_tenant, "commons")

        # Step 1: Create Fact v1 with downstream chain
        gc.query("""
        CREATE (f1:Fact:MemoryNode {
            id: 'fact_chain_v1',
            key: 'overdraft_limit',
            value: '500',
            text: 'Overdraft limit is $500',
            status: 'HEALTHY',
            stale: false,
            valid_from: timestamp()
        })
        CREATE (c1:Conclusion:MemoryNode {
            id: 'conc_chain_v1',
            text: 'Allow overdraft up to $500',
            status: 'HEALTHY',
            stale: false,
            created_at: timestamp()
        })
        CREATE (d1:Decision:MemoryNode {
            id: 'dec_chain_v1',
            text: 'Approve $500 overdraft transaction',
            status: 'HEALTHY',
            stale: false,
            created_at: timestamp()
        })
        CREATE (a1:Action:MemoryNode {
            id: 'act_chain_v1',
            type: 'allow_overdraft',
            status: 'HEALTHY',
            stale: false,
            executed: false,
            created_at: timestamp()
        })
        CREATE (f1)-[:LEADS_TO]->(c1)
        CREATE (c1)-[:LEADS_TO]->(d1)
        CREATE (d1)-[:LEADS_TO]->(a1)
        """)

        # 1st supersession: v1 -> v2
        res_v2 = queries.supersede_with_review(
            tenant_id=chain_tenant,
            old_fact_key="overdraft_limit",
            new_fact_id="fact_chain_v2",
            new_text="Overdraft limit is $750 (inflation tier)",
            new_value="750",
            author="risk_manager",
        )
        assert res_v2["success"] is True
        assert res_v2["stale_propagated"] == 3  # c1, d1, a1
        assert res_v2["review_handoffs_created"] == 1

        # Add new downstream nodes to v2
        gc.query("""
        MATCH (f2:Fact {id: 'fact_chain_v2'})
        CREATE (c2:Conclusion:MemoryNode {
            id: 'conc_chain_v2',
            text: 'Allow overdraft up to $750 based on v2',
            status: 'HEALTHY',
            stale: false,
            created_at: timestamp()
        })
        CREATE (d2:Decision:MemoryNode {
            id: 'dec_chain_v2',
            text: 'Approve $750 overdraft transaction',
            status: 'HEALTHY',
            stale: false,
            created_at: timestamp()
        })
        CREATE (a2:Action:MemoryNode {
            id: 'act_chain_v2',
            type: 'allow_overdraft',
            status: 'HEALTHY',
            stale: false,
            executed: false,
            created_at: timestamp()
        })
        CREATE (f2)-[:LEADS_TO]->(c2)
        CREATE (c2)-[:LEADS_TO]->(d2)
        CREATE (d2)-[:LEADS_TO]->(a2)
        """)

        # 2nd supersession: v2 -> v3
        res_v3 = queries.supersede_with_review(
            tenant_id=chain_tenant,
            old_fact_key="overdraft_limit",
            new_fact_id="fact_chain_v3",
            new_text="Overdraft limit is $1000 (premium tier)",
            new_value="1000",
            author="executive_board",
        )
        assert res_v3["success"] is True
        # Propagates to all descendants across the key (3 from v1 + 3 from v2 = 6 total)
        assert res_v3["stale_propagated"] == 6

        # Verify node counts: strictly 1 node per version
        res_all_facts = gc.query("MATCH (f:Fact {key: 'overdraft_limit'}) RETURN f.id, f.status, f.value")
        facts_dict = {r[0]: (r[1], r[2]) for r in res_all_facts.result_set}
        assert len(facts_dict) == 3, f"Expected 3 unique fact nodes, found {len(facts_dict)}"
        assert facts_dict["fact_chain_v1"] == ("SUPERSEDED", "500")
        assert facts_dict["fact_chain_v2"] == ("SUPERSEDED", "750")
        assert facts_dict["fact_chain_v3"] == ("HEALTHY", "1000")

        # Verify SUPERSEDES chain: v3 -> v2 -> v1
        res_edges = gc.query("""
            MATCH (src:Fact)-[:SUPERSEDES]->(tgt:Fact)
            RETURN src.id, tgt.id
        """)
        edge_pairs = set((r[0], r[1]) for r in res_edges.result_set)
        assert ("fact_chain_v2", "fact_chain_v1") in edge_pairs
        assert ("fact_chain_v3", "fact_chain_v2") in edge_pairs
        assert len(edge_pairs) == 2, f"Expected clean linear chain (2 edges), found {len(edge_pairs)}: {edge_pairs}"

        # Verify stale status across full historical chain
        res_stale = gc.query("""
            MATCH (n:MemoryNode)
            WHERE n.id IN ['conc_chain_v1', 'dec_chain_v1', 'act_chain_v1',
                           'conc_chain_v2', 'dec_chain_v2', 'act_chain_v2']
            RETURN n.id, n.stale
        """)
        stale_map = {r[0]: r[1] for r in res_stale.result_set}
        assert len(stale_map) == 6
        for nid, is_stale in stale_map.items():
            assert is_stale is True, f"Node {nid} in historical chain should be marked stale"

    def test_05_nonexistent_fact_error_handling(self):
        """
        Verify supersede_with_review cleanly handles missing key.
        """
        res = queries.supersede_with_review(
            tenant_id=TENANT,
            old_fact_key="nonexistent_key_9999",
            new_fact_id="new_f",
            new_text="Some text",
            new_value="val",
        )
        assert res["success"] is False
        assert "not found" in res["error"].lower()

    def test_06_owning_team_resolution_via_transitive_provenance(self):
        """
        Verify that owning team is correctly resolved through:
        (Team)-[:OWNS]->(Source)-[:FROM_SOURCE]->(Fact)-[:LEADS_TO*1..]->(Decision)
        """
        prov_tenant = f"{TENANT}_prov"
        reset_graph(prov_tenant)
        gp = queries.get_graph(prov_tenant, "commons")

        # Create Team, Source, Fact, Conclusion, Decision
        gp.query("""
        CREATE (t:Team {id: 'fraud_investigations'})
        CREATE (s:Source {id: 'src_fraud_sop'})
        CREATE (t)-[:OWNS]->(s)
        CREATE (f:Fact:MemoryNode {
            id: 'fact_fraud_rule',
            key: 'max_daily_transfers',
            value: '5',
            text: 'Max daily transfers is 5',
            status: 'HEALTHY',
            stale: false,
            valid_from: timestamp()
        })
        CREATE (s)-[:FROM_SOURCE]->(f)
        CREATE (c:Conclusion:MemoryNode {
            id: 'conc_fraud_rule',
            text: 'Flag user exceeding 5 transfers',
            status: 'HEALTHY',
            stale: false,
            created_at: timestamp()
        })
        CREATE (d:Decision:MemoryNode {
            id: 'dec_fraud_block',
            text: 'Block user with >5 transfers',
            status: 'HEALTHY',
            stale: false,
            created_at: timestamp()
        })
        CREATE (f)-[:LEADS_TO]->(c)
        CREATE (c)-[:LEADS_TO]->(d)
        """)

        # Supersede fact
        res_sup = queries.supersede_with_review(
            tenant_id=prov_tenant,
            old_fact_key="max_daily_transfers",
            new_fact_id="fact_fraud_rule_v2",
            new_text="Max daily transfers is 10 (elevated for holiday season)",
            new_value="10",
            author="fraud_lead",
        )
        assert res_sup["success"] is True
        assert res_sup["review_handoffs_created"] == 1
        assert res_sup["handoffs"][0]["assigned_to"] == "fraud_investigations"

        # Verify in graph
        res_h = gp.query("MATCH (h:Handoff) RETURN h.assigned_to, h.context_root")
        assert len(res_h.result_set) == 1
        assert res_h.result_set[0][0] == "fraud_investigations"
        assert res_h.result_set[0][1] == "dec_fraud_block"

