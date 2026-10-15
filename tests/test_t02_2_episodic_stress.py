"""
Comprehensive Stress-Testing & Invariant Verification for T02-2 Episodic Memory
================================================================================
Target:
  - record_episode
  - close_session
  - resume_agent
  - session_history

Tests:
1. Concurrently record 100 messages into an episodic session across multiple threads
   - Barrier synchronization for maximum concurrency
   - Test both monotonic timestamps and real-time clock timestamps
   - Invariant check: Message count, Session count, HAS_MESSAGE links
2. NEXT chain ordering, timestamp ordering, and session link integrity in session_history
   - Graph topology analysis of (:NEXT) edges (forks, merges, cycles, orphans)
   - Discrepancy analysis between docstring ("NEXT-linked traversal") and implementation (Cypher ORDER BY ts)
3. close_session: verify session summary Fact written into tenant commons graph with correct asserted_by metadata
   - Contract compliance (asserted_by vs agent_id)
   - Idempotency & epoch bump verification
4. resume_agent: verify the 2-query flow accurately returns raw recent episodic messages + commons summary facts
   - Multi-session isolation
   - Cross-agent isolation
   - Semantic filter bug analysis (f.kind == 'EPISODIC_SUMMARY' vs any Fact with agent_id)
5. Boundary conditions
   - Empty session history
   - Single message session
   - Resume non-existent agent/session
   - Re-closing an already closed session
   - Content with unicode, quotes, newlines, and Cypher syntax
   - Extreme limits (limit=0, limit=1, limit=100)
"""

import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Any

import graph.queries as queries

TENANT = "test_episodic_stress"


def reset_test_graphs(tenant_id: str, agent_ids: List[str]):
    """Clean private agent raw graphs and tenant commons graph."""
    for aid in agent_ids:
        try:
            raw_g = queries.get_graph(tenant_id, f"agent_{aid}:raw")
            raw_g.delete()
        except Exception:
            pass
    try:
        commons_g = queries.get_graph(tenant_id, "commons")
        commons_g.delete()
    except Exception:
        pass
    queries.ensure_indexes(tenant_id)


# =========================================================================
# TEST 1 & 2: Concurrency & Ordering Stress Test
# =========================================================================

def run_concurrent_stress_test(num_messages: int = 100, num_threads: int = 20, use_clock_ts: bool = False) -> Dict[str, Any]:
    agent_id = f"agent_conc_{'clock' if use_clock_ts else 'mono'}"
    session_id = f"sess_conc_{'clock' if use_clock_ts else 'mono'}"
    reset_test_graphs(TENANT, [agent_id])

    base_ts = 1700000000000
    barrier = threading.Barrier(num_threads)
    results = [None] * num_messages
    errors = []
    latencies = []

    def worker(i: int):
        barrier.wait()  # Synchronize threads for peak race condition
        ts = None if use_clock_ts else (base_ts + i * 10)
        mid = f"msg_{i:03d}"
        role = "user" if i % 2 == 0 else "assistant"
        content = f"Stress payload #{i} with timestamp {ts}"
        
        t0 = time.time()
        try:
            res = queries.record_episode(
                tenant_id=TENANT,
                agent_id=agent_id,
                session_id=session_id,
                message_id=mid,
                role=role,
                content=content,
                ts_ms=ts,
            )
            lat = (time.time() - t0) * 1000
            return (i, mid, res, lat, None)
        except Exception as e:
            lat = (time.time() - t0) * 1000
            return (i, mid, None, lat, str(e))

    t_start = time.time()
    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(worker, i) for i in range(num_messages)]
        for f in as_completed(futures):
            i, mid, res, lat, err = f.result()
            latencies.append(lat)
            if err:
                errors.append(f"{mid}: {err}")
            else:
                results[i] = res
    total_time_ms = (time.time() - t_start) * 1000

    # Graph Inspection
    raw_g = queries.get_graph(TENANT, f"agent_{agent_id}:raw")
    sess_nodes = raw_g.query("MATCH (s:Session {id: $sess_id}) RETURN count(s)", {"sess_id": session_id}).result_set[0][0]
    total_msgs = raw_g.query("MATCH (m:Message {session_id: $sess_id}) RETURN count(m)", {"sess_id": session_id}).result_set[0][0]
    linked_msgs = raw_g.query("MATCH (s:Session {id: $sess_id})-[:HAS_MESSAGE]->(m:Message) RETURN count(m)", {"sess_id": session_id}).result_set[0][0]

    # session_history retrieval
    history = queries.session_history(TENANT, agent_id, session_id)
    history_len = len(history)
    hist_ts = [m["ts"] for m in history]
    is_sorted_ts = hist_ts == sorted(hist_ts)

    # NEXT chain inspection
    next_res = raw_g.query("""
        MATCH (m1:Message)-[r:NEXT]->(m2:Message)
        WHERE m1.session_id = $sess_id
        RETURN m1.id, m1.ts, m2.id, m2.ts
    """, {"sess_id": session_id}).result_set
    
    total_next_edges = len(next_res)
    out_deg = {}
    in_deg = {}
    temporal_violations = 0

    for src, s_ts, dst, d_ts in next_res:
        out_deg[src] = out_deg.get(src, 0) + 1
        in_deg[dst] = in_deg.get(dst, 0) + 1
        if s_ts >= d_ts:
            temporal_violations += 1

    forking_nodes = {k: v for k, v in out_deg.items() if v > 1}
    merging_nodes = {k: v for k, v in in_deg.items() if v > 1}
    unlinked_sources = num_messages - len(out_deg)
    unlinked_dests = num_messages - len(in_deg)

    latencies_sorted = sorted(latencies)
    p50 = latencies_sorted[int(len(latencies_sorted) * 0.50)]
    p95 = latencies_sorted[int(len(latencies_sorted) * 0.95)]
    p99 = latencies_sorted[int(len(latencies_sorted) * 0.99)]

    return {
        "use_clock_ts": use_clock_ts,
        "num_messages": num_messages,
        "num_threads": num_threads,
        "total_time_ms": total_time_ms,
        "p50_ms": p50,
        "p95_ms": p95,
        "p99_ms": p99,
        "errors_count": len(errors),
        "errors": errors,
        "session_nodes_count": sess_nodes,
        "total_msgs_in_db": total_msgs,
        "linked_msgs_count": linked_msgs,
        "history_len": history_len,
        "is_sorted_ts": is_sorted_ts,
        "total_next_edges": total_next_edges,
        "forking_nodes_count": len(forking_nodes),
        "max_fork_degree": max(forking_nodes.values()) if forking_nodes else 1,
        "merging_nodes_count": len(merging_nodes),
        "temporal_violations": temporal_violations,
        "unlinked_sources": unlinked_sources,
        "unlinked_dests": unlinked_dests,
    }


# =========================================================================
# TEST 3: close_session & asserted_by Verification
# =========================================================================

def run_close_session_test() -> Dict[str, Any]:
    agent_id = "agent_close_audit"
    session_id = "sess_close_audit_001"
    reset_test_graphs(TENANT, [agent_id])

    # 1. Populate session with 10 messages
    base_ts = 1700000500000
    for i in range(10):
        queries.record_episode(
            tenant_id=TENANT,
            agent_id=agent_id,
            session_id=session_id,
            message_id=f"msg_aud_{i}",
            role="user" if i % 2 == 0 else "assistant",
            content=f"Message body number {i} for audit testing",
            ts_ms=base_ts + i * 500,
        )

    # Record initial epoch
    epoch_before = queries.bump_epoch(TENANT)

    # 2. Call close_session
    close_res = queries.close_session(TENANT, agent_id, session_id)
    summary_fact_id = close_res.get("summary_fact_id")

    # 3. Epoch verification
    commons_g = queries.get_graph(TENANT, "commons")
    epoch_res = commons_g.query("MATCH (m:Meta {id: 'meta'}) RETURN m.epoch").result_set
    epoch_after = epoch_res[0][0] if epoch_res else 1

    # 4. Check Fact properties
    fact_res = commons_g.query("""
        MATCH (f:Fact:MemoryNode {id: $fid})
        RETURN f.id, f.key, f.text, f.session_id, f.agent_id, f.msg_count,
               f.first_ts, f.last_ts, f.status, f.kind, f.valid_from,
               f.asserted_by, properties(f)
    """, {"fid": summary_fact_id}).result_set

    props = fact_res[0][12] if fact_res else {}

    # 5. Check if asserted_by metadata is present as property or relationship
    has_asserted_by_prop = "asserted_by" in props and props["asserted_by"] is not None
    asserted_by_val = props.get("asserted_by")

    agent_edge_res = commons_g.query("""
        MATCH (a:Agent)-[r]->(f:Fact {id: $fid})
        RETURN type(r), a.id, a.tier
    """, {"fid": summary_fact_id}).result_set

    # 6. Idempotency & Re-closing
    close_res_2 = queries.close_session(TENANT, agent_id, session_id)
    fact_count = commons_g.query("MATCH (f:Fact {id: $fid}) RETURN count(f)", {"fid": summary_fact_id}).result_set[0][0]

    # 7. Add another message and close again (Incremental close)
    queries.record_episode(
        tenant_id=TENANT,
        agent_id=agent_id,
        session_id=session_id,
        message_id="msg_aud_10",
        role="user",
        content="Appended message after closing",
        ts_ms=base_ts + 10 * 500,
    )
    close_res_3 = queries.close_session(TENANT, agent_id, session_id)
    updated_fact = commons_g.query("MATCH (f:Fact {id: $fid}) RETURN f.msg_count, f.last_ts", {"fid": summary_fact_id}).result_set[0]

    return {
        "close_res": close_res,
        "summary_fact_id": summary_fact_id,
        "epoch_bumped": epoch_after > epoch_before,
        "epoch_before": epoch_before,
        "epoch_after": epoch_after,
        "fact_exists": len(fact_res) == 1,
        "properties": dict(props),
        "has_asserted_by_prop": has_asserted_by_prop,
        "asserted_by_val": asserted_by_val,
        "agent_edges": agent_edge_res,
        "idempotency_fact_count": fact_count,
        "incremental_close_msg_count": updated_fact[0],
        "incremental_close_last_ts": updated_fact[1],
    }


# =========================================================================
# TEST 4: resume_agent 2-Query Flow Verification
# =========================================================================

def run_resume_agent_test() -> Dict[str, Any]:
    target_agent = "agent_resume_primary"
    other_agent = "agent_resume_secondary"
    reset_test_graphs(TENANT, [target_agent, other_agent])

    # Session A: 3 messages, CLOSED
    sess_a = "sess_target_closed"
    for i in range(3):
        queries.record_episode(TENANT, target_agent, sess_a, f"msg_a_{i}", "user", f"A {i}", 1700000001000 + i * 100)
    queries.close_session(TENANT, target_agent, sess_a)

    # Session B: 4 messages, OPEN (latest messages)
    sess_b = "sess_target_open"
    for i in range(4):
        queries.record_episode(TENANT, target_agent, sess_b, f"msg_b_{i}", "assistant", f"B {i}", 1700000005000 + i * 100)

    # Other agent session: CLOSED
    sess_other = "sess_other_closed"
    queries.record_episode(TENANT, other_agent, sess_other, "msg_other_1", "user", "Other agent data", 1700000009000)
    queries.close_session(TENANT, other_agent, sess_other)

    # Inject a non-summary Fact with agent_id into commons graph to test filter precision
    commons_g = queries.get_graph(TENANT, "commons")
    commons_g.query("""
        CREATE (:Fact:MemoryNode {
            id: 'fact_unrelated_policy',
            key: 'policy.secret',
            text: 'Confidential policy document',
            agent_id: $aid,
            last_ts: 1700000099999,
            kind: 'DOMAIN_POLICY'
        })
    """, {"aid": target_agent})

    # Call resume_agent
    res = queries.resume_agent(TENANT, target_agent, limit=10)

    recent_msgs = res.get("recent_messages", [])
    summaries = res.get("session_summaries", [])

    # Check Query 1: Raw recent messages
    # Expected: 4 from sess_b + 3 from sess_a = 7 total messages for target_agent
    recent_sess_ids = [m["session_id"] for m in recent_msgs]
    recent_ts = [m["ts"] for m in recent_msgs]
    is_ts_desc = recent_ts == sorted(recent_ts, reverse=True)
    has_other_msgs = any(m["session_id"] == sess_other for m in recent_msgs)

    # Check Query 2: Commons summaries
    # Does it return sess_a?
    has_sess_a = any(s["session_id"] == sess_a for s in summaries)
    has_sess_other = any(s["session_id"] == sess_other for s in summaries)
    # Critical test: Did it pollute summaries with 'fact_unrelated_policy' (which is DOMAIN_POLICY, not EPISODIC_SUMMARY)?
    unrelated_polluted = any(s["fact_id"] == "fact_unrelated_policy" for s in summaries)

    return {
        "resumed": res.get("resumed"),
        "recent_messages_count": len(recent_msgs),
        "recent_messages_sessions": recent_sess_ids,
        "is_recent_desc": is_ts_desc,
        "has_other_agent_messages": has_other_msgs,
        "summaries_count": len(summaries),
        "summary_sessions": [s["session_id"] for s in summaries],
        "has_target_summary": has_sess_a,
        "has_other_summary": has_sess_other,
        "unrelated_fact_polluted_summaries": unrelated_polluted,
    }


# =========================================================================
# TEST 5: Boundary Conditions & Edge Cases
# =========================================================================

def run_boundary_conditions_test() -> Dict[str, Any]:
    agent_id = "agent_boundary_spec"
    reset_test_graphs(TENANT, [agent_id])

    boundaries = {}

    # 1. Non-existent session history
    h_empty = queries.session_history(TENANT, agent_id, "ghost_session")
    boundaries["empty_session_history"] = {
        "result": h_empty,
        "passed": h_empty == []
    }

    # 2. Close non-existent / empty session
    c_empty = queries.close_session(TENANT, agent_id, "ghost_session")
    boundaries["close_empty_session"] = {
        "result": c_empty,
        "passed": c_empty.get("success") is False and "error" in c_empty
    }

    # 3. Resume non-existent agent
    r_ghost = queries.resume_agent(TENANT, "ghost_agent_unknown")
    boundaries["resume_ghost_agent"] = {
        "result": r_ghost,
        "passed": (
            r_ghost.get("agent_id") == "ghost_agent_unknown"
            and r_ghost.get("recent_messages") == []
            and r_ghost.get("session_summaries") == []
            and r_ghost.get("resumed") is True
        )
    }

    # 4. Single message session
    s_single = "sess_single"
    m_single = queries.record_episode(TENANT, agent_id, s_single, "lone_msg_1", "system", "Initialize system prompt", 1700000010000)
    h_single = queries.session_history(TENANT, agent_id, s_single)
    c_single = queries.close_session(TENANT, agent_id, s_single)
    r_single = queries.resume_agent(TENANT, agent_id, limit=5)
    
    boundaries["single_message_session"] = {
        "record_success": m_single.get("success") is True,
        "history_count": len(h_single),
        "close_success": c_single.get("success") is True,
        "msg_count": c_single.get("msg_count"),
        "passed": (
            len(h_single) == 1
            and c_single.get("msg_count") == 1
            and len(r_single.get("recent_messages", [])) == 1
            and len(r_single.get("session_summaries", [])) == 1
        )
    }

    # 5. Extreme limits on resume_agent
    r_lim0 = queries.resume_agent(TENANT, agent_id, limit=0)
    r_lim1000 = queries.resume_agent(TENANT, agent_id, limit=1000)
    boundaries["extreme_limits"] = {
        "limit_0_msg_count": len(r_lim0.get("recent_messages", [])),
        "limit_1000_msg_count": len(r_lim1000.get("recent_messages", [])),
        "passed": len(r_lim0.get("recent_messages", [])) == 0 and len(r_lim1000.get("recent_messages", [])) >= 1
    }

    # 6. Payload with newlines, unicode, special chars, Cypher injection
    s_payload = "sess_payload_stress"
    attack_payload = (
        "SELECT * FROM users;\n"
        "'; MATCH (n) DETACH DELETE n; RETURN 'pwned';\n"
        "Emojis: 🤖🧠⚡️\n"
        "Quotes: \"double\" and 'single' and `backticks`\n"
        "JSON: {\"nested\": [1, 2, 3], \"key\": null}\n"
        "Special symbols: <> & | $ % ^ * @ ! #"
    )
    m_payload = queries.record_episode(TENANT, agent_id, s_payload, "msg_attack_1", "user", attack_payload, 1700000020000)
    h_payload = queries.session_history(TENANT, agent_id, s_payload)
    c_payload = queries.close_session(TENANT, agent_id, s_payload)
    
    saved_payload = h_payload[0]["content"] if h_payload else ""
    boundaries["malformed_and_injection_payload"] = {
        "exact_match": saved_payload == attack_payload,
        "close_success": c_payload.get("success") is True,
        "passed": saved_payload == attack_payload and c_payload.get("success") is True
    }

    return boundaries


def test_pytest_concurrent_100_msgs_monotonic():
    res = run_concurrent_stress_test(num_messages=100, num_threads=20, use_clock_ts=False)
    assert res["errors_count"] == 0
    assert res["session_nodes_count"] == 1
    assert res["total_msgs_in_db"] == 100
    assert res["linked_msgs_count"] == 100
    assert res["history_len"] == 100
    assert res["is_sorted_ts"] is True


def test_pytest_concurrent_100_msgs_clock():
    res = run_concurrent_stress_test(num_messages=100, num_threads=20, use_clock_ts=True)
    assert res["errors_count"] == 0
    assert res["session_nodes_count"] == 1
    assert res["total_msgs_in_db"] == 100
    assert res["linked_msgs_count"] == 100
    assert res["history_len"] == 100
    assert res["is_sorted_ts"] is True


def test_pytest_close_session():
    res = run_close_session_test()
    assert res["fact_exists"] is True
    assert res["epoch_bumped"] is True
    assert res["idempotency_fact_count"] == 1
    assert res["properties"]["agent_id"] == "agent_close_audit"
    assert res["incremental_close_msg_count"] == 11


def test_pytest_resume_agent():
    res = run_resume_agent_test()
    assert res["resumed"] is True
    assert res["recent_messages_count"] == 7
    assert res["is_recent_desc"] is True
    assert res["has_other_agent_messages"] is False
    assert res["has_target_summary"] is True
    assert res["has_other_summary"] is False


def test_pytest_boundary_conditions():
    res = run_boundary_conditions_test()
    for name, data in res.items():
        assert data["passed"] is True, f"Boundary test {name} failed: {data}"


if __name__ == "__main__":

    print("=" * 80)
    print("RUNNING IN-DEPTH T02-2 STRESS & INVARIANT TEST SUITE")
    print("=" * 80)

    # 1. Monotonic TS Concurrency Test
    print("\n[EXEC] Test 1A: 100 Messages Concurrently across 20 Threads (Monotonic Timestamps)")
    t1a = run_concurrent_stress_test(num_messages=100, num_threads=20, use_clock_ts=False)
    print(f"  Duration: {t1a['total_time_ms']:.2f}ms | P50: {t1a['p50_ms']:.2f}ms | P95: {t1a['p95_ms']:.2f}ms")
    print(f"  Messages: {t1a['total_msgs_in_db']}/100 | Sessions: {t1a['session_nodes_count']} | HAS_MESSAGE: {t1a['linked_msgs_count']}")
    print(f"  session_history: {t1a['history_len']} msgs | Sorted: {t1a['is_sorted_ts']}")
    print(f"  NEXT edges: {t1a['total_next_edges']} | Forks (out > 1): {t1a['forking_nodes_count']} | Merges (in > 1): {t1a['merging_nodes_count']}")
    print(f"  Unlinked Sources: {t1a['unlinked_sources']} | Unlinked Dests: {t1a['unlinked_dests']}")

    # 2. Clock TS Concurrency Test
    print("\n[EXEC] Test 1B: 100 Messages Concurrently across 20 Threads (System Clock Timestamps)")
    t1b = run_concurrent_stress_test(num_messages=100, num_threads=20, use_clock_ts=True)
    print(f"  Duration: {t1b['total_time_ms']:.2f}ms | P50: {t1b['p50_ms']:.2f}ms | P95: {t1b['p95_ms']:.2f}ms")
    print(f"  Messages: {t1b['total_msgs_in_db']}/100 | Sessions: {t1b['session_nodes_count']} | HAS_MESSAGE: {t1b['linked_msgs_count']}")
    print(f"  session_history: {t1b['history_len']} msgs | Sorted: {t1b['is_sorted_ts']}")
    print(f"  NEXT edges: {t1b['total_next_edges']} | Forks (out > 1): {t1b['forking_nodes_count']} | Merges (in > 1): {t1b['merging_nodes_count']}")
    print(f"  Unlinked Sources: {t1b['unlinked_sources']} | Unlinked Dests: {t1b['unlinked_dests']}")

    # 3. close_session Test
    print("\n[EXEC] Test 3: close_session & asserted_by Metadata Invariant")
    t3 = run_close_session_test()
    print(f"  Summary Fact ID: {t3['summary_fact_id']}")
    print(f"  Fact Exists: {t3['fact_exists']} | Epoch Bumped: {t3['epoch_bumped']} ({t3['epoch_before']} -> {t3['epoch_after']})")
    print(f"  asserted_by in Properties: {t3['has_asserted_by_prop']} (val: {t3['asserted_by_val']})")
    print(f"  agent_id in Properties: {t3['properties'].get('agent_id')}")
    print(f"  Agent Edges to Fact: {t3['agent_edges']}")
    print(f"  Idempotency (Count on duplicate close): {t3['idempotency_fact_count']}")
    print(f"  Incremental Close (msg_count updated): {t3['incremental_close_msg_count']}")

    # 4. resume_agent Test
    print("\n[EXEC] Test 4: resume_agent 2-Query Flow Verification")
    t4 = run_resume_agent_test()
    print(f"  Resumed Flag: {t4['resumed']}")
    print(f"  Recent Messages Count: {t4['recent_messages_count']} | Sorted DESC: {t4['is_recent_desc']}")
    print(f"  Other Agent Messages Leaked: {t4['has_other_agent_messages']}")
    print(f"  Summaries Count: {t4['summaries_count']} | Target Agent Summary Present: {t4['has_target_summary']}")
    print(f"  Other Agent Summary Present: {t4['has_other_summary']}")
    print(f"  BUG CHECK - Unrelated Fact Polluted Summaries: {t4['unrelated_fact_polluted_summaries']}")

    # 5. Boundary Conditions Test
    print("\n[EXEC] Test 5: Boundary Conditions & Edge Cases")
    t5 = run_boundary_conditions_test()
    for name, data in t5.items():
        print(f"  {name}: {'PASSED' if data['passed'] else 'FAILED'}")

    print("\n" + "=" * 80)
    print("ALL TESTS COMPLETE")
    print("=" * 80)
