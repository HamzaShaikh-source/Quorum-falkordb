import json
import time
from typing import Any, Dict, List
from graph import queries

TENANT = "subagent_09_test"

def run_tests():
    print("=" * 70)
    print("SUBAGENT 09: STRESS-TEST T03-4 KNOWLEDGE CURRENCY")
    print("=" * 70)

    g = queries.get_graph(TENANT, "commons")
    try:
        g.delete()
    except Exception:
        pass
    g = queries.get_graph(TENANT, "commons")

    results: Dict[str, Any] = {}

    # -------------------------------------------------------------------------
    # TEST 1: Construct multi-tier tree (Fact -> Conclusion -> 3 Decisions -> 5 Actions)
    # -------------------------------------------------------------------------
    print("\n--- TEST 1: Construct Multi-Tier Tree ---")
    tree_cypher = """
    CREATE (f:Fact:MemoryNode {
        id: 'fact_tier_1',
        key: 'policy_window',
        text: 'Standard policy window is 30 days',
        value: '30',
        author: 'compliance_team',
        status: 'HEALTHY',
        stale: false,
        valid_from: timestamp()
    })
    CREATE (c:Conclusion:MemoryNode {
        id: 'conc_tier_1',
        text: 'Customers within 30 days are eligible for standard refund',
        status: 'HEALTHY',
        stale: false,
        created_at: timestamp()
    })
    CREATE (f)-[:LEADS_TO]->(c)

    CREATE (d1:Decision:MemoryNode {
        id: 'dec_1',
        text: 'Approve refund under standard 30-day policy (US)',
        status: 'HEALTHY',
        stale: false,
        created_at: timestamp()
    })
    CREATE (d2:Decision:MemoryNode {
        id: 'dec_2',
        text: 'Approve refund under standard 30-day policy (EU)',
        status: 'HEALTHY',
        stale: false,
        created_at: timestamp()
    })
    CREATE (d3:Decision:MemoryNode {
        id: 'dec_3',
        text: 'Approve refund under standard 30-day policy (APAC)',
        status: 'HEALTHY',
        stale: false,
        created_at: timestamp()
    })
    CREATE (c)-[:LEADS_TO]->(d1)
    CREATE (c)-[:LEADS_TO]->(d2)
    CREATE (c)-[:LEADS_TO]->(d3)

    CREATE (a1:Action:MemoryNode {
        id: 'act_1',
        type: 'refund_stripe',
        text: 'Execute Stripe refund for order 101',
        status: 'HEALTHY',
        stale: false,
        executed: false,
        created_at: timestamp()
    })
    CREATE (a2:Action:MemoryNode {
        id: 'act_2',
        type: 'refund_stripe',
        text: 'Execute Stripe refund for order 102',
        status: 'HEALTHY',
        stale: false,
        executed: false,
        created_at: timestamp()
    })
    CREATE (a3:Action:MemoryNode {
        id: 'act_3',
        type: 'refund_adyen',
        text: 'Execute Adyen refund for order 201',
        status: 'HEALTHY',
        stale: false,
        executed: false,
        created_at: timestamp()
    })
    CREATE (a4:Action:MemoryNode {
        id: 'act_4',
        type: 'refund_alipay',
        text: 'Execute Alipay refund for order 301',
        status: 'HEALTHY',
        stale: false,
        executed: false,
        created_at: timestamp()
    })
    CREATE (a5:Action:MemoryNode {
        id: 'act_5',
        type: 'refund_alipay',
        text: 'Execute Alipay refund for order 302',
        status: 'HEALTHY',
        stale: false,
        executed: false,
        created_at: timestamp()
    })
    CREATE (d1)-[:LEADS_TO]->(a1)
    CREATE (d1)-[:LEADS_TO]->(a2)
    CREATE (d2)-[:LEADS_TO]->(a3)
    CREATE (d3)-[:LEADS_TO]->(a4)
    CREATE (d3)-[:LEADS_TO]->(a5)
    RETURN f.id, c.id, [d1.id, d2.id, d3.id], [a1.id, a2.id, a3.id, a4.id, a5.id]
    """
    res_tree = g.query(tree_cypher)
    print(f"Tree constructed. Rows: {len(res_tree.result_set)}")
    results["test_1_tree"] = {
        "success": len(res_tree.result_set) == 1,
        "nodes": {
            "fact": 1,
            "conclusion": 1,
            "decisions": 3,
            "actions": 5
        }
    }

    # -------------------------------------------------------------------------
    # TEST 2: Call supersede_with_review with new replacement Fact
    # -------------------------------------------------------------------------
    print("\n--- TEST 2: Call supersede_with_review ---")
    t0 = time.time()
    supersede_res = queries.supersede_with_review(
        tenant_id=TENANT,
        old_fact_key="policy_window",
        new_fact_id="fact_tier_2",
        new_text="Updated policy window is 45 days",
        new_value="45",
        author="policy_bot_v2",
        source_url="https://internal.policy/docs/refund-v2",
    )
    elapsed_ms = (time.time() - t0) * 1000
    print(f"supersede_with_review returned: {supersede_res} in {elapsed_ms:.2f}ms")
    results["test_2_call"] = supersede_res

    # -------------------------------------------------------------------------
    # TEST 3: Verify SUPERSEDES edge direction and properties
    # -------------------------------------------------------------------------
    print("\n--- TEST 3: Verify SUPERSEDES edge ---")
    # Check new -> old
    res_new_to_old = g.query("""
        MATCH (new:Fact {id: 'fact_tier_2'})-[r:SUPERSEDES]->(old:Fact {id: 'fact_tier_1'})
        RETURN type(r) AS rel_type, r.at AS superseded_at
    """)
    # Check old -> new
    res_old_to_new = g.query("""
        MATCH (old:Fact {id: 'fact_tier_1'})-[r:SUPERSEDES]->(new:Fact {id: 'fact_tier_2'})
        RETURN type(r) AS rel_type, r.at AS superseded_at
    """)
    print(f"new -> old (fact_tier_2 -> fact_tier_1): {res_new_to_old.result_set}")
    print(f"old -> new (fact_tier_1 -> fact_tier_2): {res_old_to_new.result_set}")

    # Also check fact statuses
    res_facts = g.query("""
        MATCH (f:Fact) WHERE f.id IN ['fact_tier_1', 'fact_tier_2']
        RETURN f.id, f.status, f.stale, f.value, f.superseded_at
    """)
    facts_dict = {r[0]: {"status": r[1], "stale": r[2], "value": r[3], "superseded_at": r[4]} for r in res_facts.result_set}
    print(f"Fact node properties: {facts_dict}")

    results["test_3_supersedes_edge"] = {
        "new_to_old_exists": len(res_new_to_old.result_set) > 0,
        "old_to_new_exists": len(res_old_to_new.result_set) > 0,
        "edge_properties": res_new_to_old.result_set[0] if res_new_to_old.result_set else None,
        "facts": facts_dict
    }

    # -------------------------------------------------------------------------
    # TEST 4: Verify stale=True cascades to ALL downstream nodes
    # (Conclusion, Decisions, Actions)
    # -------------------------------------------------------------------------
    print("\n--- TEST 4: Verify Stale Cascading ---")
    res_stale = g.query("""
        MATCH (n:MemoryNode)
        WHERE n.id IN ['conc_tier_1', 'dec_1', 'dec_2', 'dec_3', 'act_1', 'act_2', 'act_3', 'act_4', 'act_5']
        RETURN n.id, labels(n), n.stale, n.stale_since
    """)
    stale_map = {}
    for r in res_stale.result_set:
        stale_map[r[0]] = {
            "labels": r[1],
            "stale": r[2],
            "stale_since": r[3]
        }
    print("Downstream nodes stale status:")
    for nid, data in stale_map.items():
        print(f"  {nid} ({data['labels']}): stale={data['stale']}, stale_since={data['stale_since']}")

    conclusions_stale = all(data["stale"] is True for nid, data in stale_map.items() if "Conclusion" in data["labels"])
    decisions_stale = all(data["stale"] is True for nid, data in stale_map.items() if "Decision" in data["labels"])
    actions_stale = all(data["stale"] is True for nid, data in stale_map.items() if "Action" in data["labels"])

    results["test_4_stale_cascade"] = {
        "all_conclusions_stale": conclusions_stale,
        "all_decisions_stale": decisions_stale,
        "all_actions_stale": actions_stale,
        "full_cascade_to_all_downstream": conclusions_stale and decisions_stale and actions_stale,
        "stale_map": stale_map
    }

    # -------------------------------------------------------------------------
    # TEST 5: Verify REVIEW handoffs are automatically created
    # for all downstream decisions with context_root properly set
    # -------------------------------------------------------------------------
    print("\n--- TEST 5: Verify REVIEW Handoffs ---")
    res_handoffs = g.query("""
        MATCH (d:Decision)-[:REQUIRES_REVIEW]->(h:Handoff)
        RETURN d.id AS decision_id, h.id AS handoff_id, h.kind AS kind,
               h.status AS status, h.context_root AS context_root,
               h.assigned_to AS assigned_to, h.reason AS reason
    """)
    handoffs_list = []
    for r in res_handoffs.result_set:
        handoffs_list.append({
            "decision_id": r[0],
            "handoff_id": r[1],
            "kind": r[2],
            "status": r[3],
            "context_root": r[4],
            "assigned_to": r[5],
            "reason": r[6]
        })
    print(f"Created {len(handoffs_list)} REVIEW handoffs:")
    for h in handoffs_list:
        print(f"  Handoff {h['handoff_id']}: decision={h['decision_id']}, context_root={h['context_root']}, kind={h['kind']}, status={h['status']}, assigned_to={h['assigned_to']}")

    decisions_with_handoff = {h["decision_id"] for h in handoffs_list}
    context_root_correct = all(h["context_root"] == h["decision_id"] for h in handoffs_list)
    kinds_correct = all(h["kind"] == "REVIEW" for h in handoffs_list)

    results["test_5_review_handoffs"] = {
        "count": len(handoffs_list),
        "expected_count": 3,
        "all_decisions_covered": decisions_with_handoff == {"dec_1", "dec_2", "dec_3"},
        "context_root_properly_set": context_root_correct,
        "kind_is_review": kinds_correct,
        "handoffs": handoffs_list
    }

    # -------------------------------------------------------------------------
    # TEST 6: Chain of supersessions: Fact v1 -> Fact v2 -> Fact v3
    # -------------------------------------------------------------------------
    print("\n--- TEST 6: Chain of Supersessions (Fact v1 -> v2 -> v3) ---")
    chain_tenant = f"{TENANT}_chain"
    gc = queries.get_graph(chain_tenant, "commons")
    try:
        gc.delete()
    except Exception:
        pass
    gc = queries.get_graph(chain_tenant, "commons")

    # Construct initial Fact v1 tree
    gc.query("""
    CREATE (f1:Fact:MemoryNode {
        id: 'fact_v1',
        key: 'shipping_fee',
        text: 'Standard shipping fee is $5',
        value: '5',
        author: 'shipping_team',
        status: 'HEALTHY',
        stale: false,
        valid_from: timestamp()
    })
    CREATE (c1:Conclusion:MemoryNode {
        id: 'conc_v1',
        text: 'Charge $5 flat rate shipping',
        status: 'HEALTHY',
        stale: false,
        created_at: timestamp()
    })
    CREATE (d1:Decision:MemoryNode {
        id: 'dec_v1',
        text: 'Invoice order with $5 fee',
        status: 'HEALTHY',
        stale: false,
        created_at: timestamp()
    })
    CREATE (a1:Action:MemoryNode {
        id: 'act_v1',
        type: 'charge_card',
        text: 'Charge card $5 shipping',
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
    print("Calling 1st supersession: v1 -> v2...")
    res_sup_1 = queries.supersede_with_review(
        tenant_id=chain_tenant,
        old_fact_key="shipping_fee",
        new_fact_id="fact_v2",
        new_text="Standard shipping fee is $7 (inflation adjustment)",
        new_value="7",
        author="ops_lead",
    )
    print(f"v1 -> v2 result: {res_sup_1}")

    # Add dependent nodes for v2 before v2 is superseded
    gc.query("""
    CREATE (c2:Conclusion:MemoryNode {
        id: 'conc_v2',
        text: 'Charge $7 flat rate shipping based on v2',
        status: 'HEALTHY',
        stale: false,
        created_at: timestamp()
    })
    CREATE (d2:Decision:MemoryNode {
        id: 'dec_v2',
        text: 'Invoice order with $7 fee',
        status: 'HEALTHY',
        stale: false,
        created_at: timestamp()
    })
    CREATE (a2:Action:MemoryNode {
        id: 'act_v2',
        type: 'charge_card',
        text: 'Charge card $7 shipping',
        status: 'HEALTHY',
        stale: false,
        executed: false,
        created_at: timestamp()
    })
    WITH c2, d2, a2
    MATCH (f2:Fact {id: 'fact_v2'})
    CREATE (f2)-[:LEADS_TO]->(c2)
    CREATE (c2)-[:LEADS_TO]->(d2)
    CREATE (d2)-[:LEADS_TO]->(a2)
    """)

    # 2nd supersession: v2 -> v3
    print("Calling 2nd supersession: v2 -> v3...")
    res_sup_2 = queries.supersede_with_review(
        tenant_id=chain_tenant,
        old_fact_key="shipping_fee",
        new_fact_id="fact_v3",
        new_text="Standard shipping fee is $10 (fuel surcharge)",
        new_value="10",
        author="executive",
    )
    print(f"v2 -> v3 result: {res_sup_2}")

    # Inspect all facts in chain graph
    res_chain_facts = gc.query("""
        MATCH (f:Fact {key: 'shipping_fee'})
        RETURN f.id, f.value, f.status, f.stale
    """)
    print("Chain facts in graph:")
    chain_facts = []
    for r in res_chain_facts.result_set:
        print(f"  {r[0]}: value={r[1]}, status={r[2]}, stale={r[3]}")
        chain_facts.append({"id": r[0], "value": r[1], "status": r[2], "stale": r[3]})

    # Inspect SUPERSEDES edges
    res_chain_edges = gc.query("""
        MATCH (n:Fact)-[r:SUPERSEDES]->(m:Fact)
        RETURN n.id AS source, type(r) AS rel, m.id AS target
    """)
    chain_edges = [{"from": r[0], "rel": r[1], "to": r[2]} for r in res_chain_edges.result_set]
    print(f"Chain SUPERSEDES edges: {chain_edges}")

    # Check stale status of v1 and v2 branches
    res_chain_stale = gc.query("""
        MATCH (n:MemoryNode)
        WHERE n.id IN ['conc_v1', 'dec_v1', 'act_v1', 'conc_v2', 'dec_v2', 'act_v2']
        RETURN n.id, labels(n), n.stale
    """)
    chain_stale_map = {r[0]: {"labels": r[1], "stale": r[2]} for r in res_chain_stale.result_set}
    print("Chain branch stale statuses:")
    for nid, d in chain_stale_map.items():
        print(f"  {nid}: stale={d['stale']}")

    # Count nodes with id 'fact_v3'
    v3_nodes = [f for f in chain_facts if f["id"] == "fact_v3"]
    print(f"Number of fact_v3 nodes created: {len(v3_nodes)}")

    results["test_6_chain"] = {
        "sup_1_result": res_sup_1,
        "sup_2_result": res_sup_2,
        "facts": chain_facts,
        "edges": chain_edges,
        "branch_stale": chain_stale_map,
        "v3_node_count": len(v3_nodes),
    }

    print("\n" + "=" * 70)
    print("SUMMARY RESULTS JSON:")
    print("=" * 70)
    print(json.dumps(results, indent=2))

if __name__ == "__main__":
    run_tests()
