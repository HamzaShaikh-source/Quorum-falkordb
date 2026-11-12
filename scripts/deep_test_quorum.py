import sys
import time
sys.path.append(r'C:\Users\erstr\.gemini\antigravity')
from quorum_client import get_session, call_tool

sys.stdout.reconfigure(encoding='utf-8')

def test_feature(name, fn):
    print(f"\n--- [TEST] {name} ---")
    try:
        t0 = time.time()
        res = fn()
        dt = (time.time() - t0) * 1000
        print(f"  Result ({dt:.1f}ms): {res.get('status', 'OK')}")
        return True, res
    except Exception as e:
        print(f"  FAILED with exception: {e}")
        return False, str(e)

def main():
    sess = get_session()
    print(f"Session established: {sess}")
    results = {}

    # Test 1: remember_fact (Create Root Source & Fact)
    def t1():
        r = call_tool(sess, "remember_fact", {
            "key": "test_refund_sla_uk",
            "value": "48 hours",
            "text": "Refund requests in the UK region must be resolved within 48 hours.",
            "scope": "commons"
        })
        return r.get("structuredContent", {}).get("result", {})
    ok1, r1 = test_feature("remember_fact", t1)
    fact_id = r1.get("fact_id")
    results["remember_fact"] = (ok1, fact_id)
    print(f"  Fact ID: {fact_id}")

    # Test 2: derive (Create a derived conclusion)
    derived_id = None
    if fact_id:
        def t2():
            r = call_tool(sess, "derive", {
                "source_ids": [fact_id],
                "conclusion": "UK customer ticket #8819 is entitled to immediate escalation if pending over 48 hours.",
                "scope": "commons"
            })
            return r.get("structuredContent", {}).get("result", {})
        ok2, r2 = test_feature("derive", t2)
        derived_id = r2.get("conclusion_id") or r2.get("id")
        results["derive"] = (ok2, derived_id)
        print(f"  Derived Node: {derived_id}")

    # Test 3: find_path (Shortest causal chain)
    if fact_id and derived_id:
        def t3():
            r = call_tool(sess, "find_path", {
                "from_id": fact_id,
                "to_id": derived_id
            })
            return r.get("structuredContent", {}).get("result", {})
        ok3, r3 = test_feature("find_path", t3)
        results["find_path"] = (ok3, r3)
        print(f"  Causal path: {r3}")

    # Test 4: stage_action & execute_action
    action_id = None
    def t4():
        r = call_tool(sess, "stage_action", {
            "action_type": "refund_payout",
            "target": "cust_uk_8819",
            "params": {"amount": 50, "currency": "GBP"},
            "reason": "SLA exceeded 48h based on fact " + str(fact_id)
        })
        return r.get("structuredContent", {}).get("result", {})
    ok4, r4 = test_feature("stage_action", t4)
    action_id = r4.get("action_id") or r4.get("id")
    results["stage_action"] = (ok4, action_id)
    print(f"  Staged Action ID: {action_id}")

    # Test 5: explain_blast_radius (Before Quarantine)
    if fact_id:
        def t5():
            r = call_tool(sess, "explain_blast_radius", {
                "node_id": fact_id
            })
            return r.get("structuredContent", {}).get("result", {})
        ok5, r5 = test_feature("explain_blast_radius", t5)
        results["blast_radius"] = (ok5, r5)
        print(f"  Blast Radius impact count: {r5.get('impact_count', 0)}, details: {r5}")

    # Test 6: quarantine (Poison containment)
    if fact_id:
        def t6():
            r = call_tool(sess, "quarantine", {
                "node_id": fact_id,
                "reason": "Audit defect: UK policy was repealed and replaced with 24 hours."
            })
            return r.get("structuredContent", {}).get("result", {})
        ok6, r6 = test_feature("quarantine", t6)
        results["quarantine"] = (ok6, r6)
        print(f"  Quarantine Status: {r6}")

    # Test 7: Verify quarantine suppression via retrieve_context
    def t7():
        r = call_tool(sess, "retrieve_context", {
            "query": "refund requests in the UK region",
            "format": "compact"
        })
        return r.get("structuredContent", {}).get("result", {})
    ok7, r7 = test_feature("verify_quarantine_suppression", t7)
    results["quarantine_suppression"] = (ok7, r7)
    print(f"  Retrieved count after quarantine: {r7.get('count', 0)} (Should be 0 or suppressed)")

    # Test 8: restore (Un-quarantine)
    if fact_id:
        def t8():
            r = call_tool(sess, "restore", {
                "node_id": fact_id
            })
            return r.get("structuredContent", {}).get("result", {})
        ok8, r8 = test_feature("restore", t8)
        results["restore"] = (ok8, r8)
        print(f"  Restore Status: {r8}")

    # Test 9: delta (Check epoch changes)
    def t9():
        r = call_tool(sess, "delta", {})
        return r.get("structuredContent", {}).get("result", {})
    ok9, r9 = test_feature("delta", t9)
    results["delta"] = (ok9, r9)
    print(f"  Delta output: {r9}")

    # Test 10: Multi-Agent Handoff
    def t10():
        r = call_tool(sess, "claim_handoff", {
            "handoff_id": "h_test_audit_8819",
            "task": "Audit UK refund escalation",
            "timeout_seconds": 60
        })
        return r.get("structuredContent", {}).get("result", {})
    ok10, r10 = test_feature("claim_handoff", t10)
    results["claim_handoff"] = (ok10, r10)
    print(f"  Handoff result: {r10}")

    print("\n================ TEST SUMMARY ================")
    for k, v in results.items():
        print(f"  {k:28}: {'PASS' if v[0] else 'FAIL'}")

if __name__ == "__main__":
    main()
