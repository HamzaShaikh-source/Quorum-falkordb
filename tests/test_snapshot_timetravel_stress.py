"""
tests/test_snapshot_timetravel_stress.py

Subagent 18: Stress-testing Graph Snapshots & Time-Travel (graph_snapshot, as_of).
Comprehensive test suite verifying:
1. 10 sequential graph epochs with graph_snapshot taken at each epoch.
2. Querying as_of(epoch=E) vs as_of(timestamp_ms=T) across past epochs (E=1, 3, 5, 8).
3. Verification of returned node states, statuses, counts vs historical expectations.
4. Non-destructive invariant: time-travel queries must not mutate graph head.
5. In-depth parameter and logic analysis for as_of and graph_snapshot.
"""

import os
import sys
import time
from typing import Dict, Any

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import graph.queries as queries

TENANT = "test_snapshot_stress"


def get_commons():
    return queries.get_graph(TENANT, "commons")


def clean_graph():
    db = queries.get_db()
    try:
        db.select_graph(f"tenant_{TENANT}:commons").delete()
    except Exception:
        pass
    # Initialize fresh graph with Meta node at epoch 1
    queries.get_graph(TENANT, "commons")
    queries.ensure_indexes(TENANT)


def get_current_epoch() -> int:
    g = get_commons()
    res = g.query("MATCH (m:Meta {id: 'meta'}) RETURN m.epoch")
    return res.result_set[0][0] if res.result_set else 1


# Persistent holder for 10-epoch execution across test methods
EPOCH_DATA: Dict[int, Dict[str, Any]] = {}
EPOCH_SNAPSHOTS: Dict[int, Dict[str, Any]] = {}


class TestSnapshotTimeTravelStress:

    @classmethod
    def setup_class(cls):
        clean_graph()

    @classmethod
    def teardown_class(cls):
        clean_graph()

    def test_01_mutate_graph_across_10_sequential_epochs(self):
        """
        Test 1: Mutate graph across 10 sequential epochs, taking graph_snapshot at each epoch.
        Verifies:
        - Epoch strictly advances from 1 through 10.
        - Graph mutations reflect at each epoch.
        - graph_snapshot returns epoch, nodes, and edges matching each live epoch.
        """
        clean_graph()
        g = get_commons()
        dummy_embedding = [0.05] * 384

        print("\n================================================================================")
        print("TEST 1: 10 SEQUENTIAL GRAPH EPOCHS & LIVE SNAPSHOTS")
        print("================================================================================")

        # -------------------------------------------------------------
        # EPOCH 1: Initial Graph Creation
        # -------------------------------------------------------------
        t_start_e1 = int(time.time() * 1000)
        time.sleep(0.01)  # Ensure distinct timestamp
        # Create initial facts: F1 (refund policy), F2 (shipping SLA), F3 (vip discount)
        g.query("""
        CREATE (:Fact:MemoryNode {
            id: 'F1', key: 'refund_window', value: '30', text: 'Refund window is 30 days',
            status: 'HEALTHY', valid_from: timestamp(), stale: false
        })
        """)
        g.query("""
        CREATE (:Fact:MemoryNode {
            id: 'F2', key: 'shipping_sla', value: '3', text: 'Standard shipping SLA is 3 days',
            status: 'HEALTHY', valid_from: timestamp(), stale: false
        })
        """)
        g.query("""
        CREATE (:Fact:MemoryNode {
            id: 'F3', key: 'vip_discount', value: '15', text: 'VIP tier discount is 15%',
            status: 'HEALTHY', valid_from: timestamp(), stale: false
        })
        """)
        # Derive conclusion C1 from F1
        queries.derive(TENANT, "C1", ["F1"], "Auto-approve refunds within 30 days", is_decision=False)
        # Stage action A1 from C1
        queries.stage_action(TENANT, "A1", ["C1"], "refund", {"amount": 50}, "COMPENSABLE", gate="PASSED")

        e1 = get_current_epoch()
        assert e1 == 1, f"Expected Epoch 1, got {e1}"
        snap1 = queries.graph_snapshot(TENANT)
        assert snap1["epoch"] == 1
        node_ids = [n["id"] for n in snap1["nodes"]]
        print(f"Epoch 1 node IDs: {node_ids}")
        # Note: Meta node (:Meta {id: 'meta'}) is returned in graph_snapshot as a node with kind='fact'!
        biz_nodes = [n for n in snap1["nodes"] if n["id"] != "meta"]
        assert len(biz_nodes) == 5, f"Expected 5 business nodes (F1, F2, F3, C1, A1), got {len(biz_nodes)}"
        assert "meta" not in node_ids, "Meta node should be excluded from business nodes list"
        t_e1 = int(time.time() * 1000)
        EPOCH_DATA[1] = {"t_start": t_start_e1, "t_end": t_e1, "epoch": 1, "desc": "Initial 3 Facts + 1 Conclusion + 1 Action"}
        EPOCH_SNAPSHOTS[1] = snap1
        print(f"Epoch 1 captured: {len(snap1['nodes'])} nodes, {len(snap1['edges'])} edges, epoch={snap1['epoch']}")

        # -------------------------------------------------------------
        # EPOCH 2: Quarantine F2 (Shipping SLA) via cascade
        # -------------------------------------------------------------
        time.sleep(0.01)
        t_start_e2 = int(time.time() * 1000)
        casc_res = queries.cascade(TENANT, "F2")
        assert casc_res.get("success") is True
        e2 = get_current_epoch()
        assert e2 == 2, f"Expected Epoch 2 after cascade, got {e2}"
        snap2 = queries.graph_snapshot(TENANT)
        assert snap2["epoch"] == 2
        # F2 should now have status QUARANTINED
        f2_node = next(n for n in snap2["nodes"] if n["id"] == "F2")
        assert f2_node["status"] == "QUARANTINED"
        t_e2 = int(time.time() * 1000)
        EPOCH_DATA[2] = {"t_start": t_start_e2, "t_end": t_e2, "epoch": 2, "desc": "Quarantined F2"}
        EPOCH_SNAPSHOTS[2] = snap2
        print(f"Epoch 2 captured: {len(snap2['nodes'])} nodes, {len(snap2['edges'])} edges, epoch={snap2['epoch']}")

        # -------------------------------------------------------------
        # EPOCH 3: Restore F2 via restore
        # -------------------------------------------------------------
        time.sleep(0.01)
        t_start_e3 = int(time.time() * 1000)
        rest_res = queries.restore(TENANT, "F2")
        assert rest_res.get("success") is True
        e3 = get_current_epoch()
        assert e3 == 3, f"Expected Epoch 3 after restore, got {e3}"
        snap3 = queries.graph_snapshot(TENANT)
        assert snap3["epoch"] == 3
        f2_node_rest = next(n for n in snap3["nodes"] if n["id"] == "F2")
        assert f2_node_rest["status"] == "HEALTHY"
        t_e3 = int(time.time() * 1000)
        EPOCH_DATA[3] = {"t_start": t_start_e3, "t_end": t_e3, "epoch": 3, "desc": "Restored F2 to HEALTHY"}
        EPOCH_SNAPSHOTS[3] = snap3
        print(f"Epoch 3 captured: {len(snap3['nodes'])} nodes, {len(snap3['edges'])} edges, epoch={snap3['epoch']}")

        # -------------------------------------------------------------
        # EPOCH 4: Supersede F1 (30 days -> 45 days)
        # -------------------------------------------------------------
        time.sleep(0.01)
        t_start_e4 = int(time.time() * 1000)
        sup_res = queries.supersede(
            TENANT,
            old_fact_id="F1",
            new_fact_id="F1_v2",
            new_value="45",
            new_text="Refund window updated to 45 days",
            new_embedding=dummy_embedding,
        )
        assert sup_res.get("success") is True
        e4 = get_current_epoch()
        assert e4 == 4, f"Expected Epoch 4 after supersede, got {e4}"
        snap4 = queries.graph_snapshot(TENANT)
        assert snap4["epoch"] == 4
        f1_old = next(n for n in snap4["nodes"] if n["id"] == "F1")
        f1_new = next(n for n in snap4["nodes"] if n["id"] == "F1_v2")
        assert f1_old["status"] == "SUPERSEDED"
        assert f1_new["status"] == "HEALTHY"
        # C1 should be marked stale=True due to stale propagation
        c1_node = next(n for n in snap4["nodes"] if n["id"] == "C1")
        assert c1_node["stale"] is True
        t_e4 = int(time.time() * 1000)
        EPOCH_DATA[4] = {"t_start": t_start_e4, "t_end": t_e4, "epoch": 4, "desc": "Superseded F1 with F1_v2 (45 days); C1 stale"}
        EPOCH_SNAPSHOTS[4] = snap4
        print(f"Epoch 4 captured: {len(snap4['nodes'])} nodes, {len(snap4['edges'])} edges, epoch={snap4['epoch']}")

        # -------------------------------------------------------------
        # EPOCH 5: Add F4 (Holiday Policy) + Derive Decision D1 + bump epoch
        # -------------------------------------------------------------
        time.sleep(0.01)
        t_start_e5 = int(time.time() * 1000)
        g.query("""
        CREATE (:Fact:MemoryNode {
            id: 'F4', key: 'holiday_policy', value: 'extended', text: 'Holiday returns extended to Jan 31',
            status: 'HEALTHY', valid_from: timestamp(), stale: false
        })
        """)
        queries.derive(TENANT, "D1", ["F4"], "Extended holiday return approvals active", is_decision=True)
        queries.bump_epoch(TENANT)
        e5 = get_current_epoch()
        assert e5 == 5, f"Expected Epoch 5, got {e5}"
        snap5 = queries.graph_snapshot(TENANT)
        assert snap5["epoch"] == 5
        assert any(n["id"] == "F4" for n in snap5["nodes"])
        assert any(n["id"] == "D1" and n["is_decision"] is True for n in snap5["nodes"])
        t_e5 = int(time.time() * 1000)
        EPOCH_DATA[5] = {"t_start": t_start_e5, "t_end": t_e5, "epoch": 5, "desc": "Added F4 + Decision D1"}
        EPOCH_SNAPSHOTS[5] = snap5
        print(f"Epoch 5 captured: {len(snap5['nodes'])} nodes, {len(snap5['edges'])} edges, epoch={snap5['epoch']}")

        # -------------------------------------------------------------
        # EPOCH 6: Quarantine F1_v2 (cascade)
        # -------------------------------------------------------------
        time.sleep(0.01)
        t_start_e6 = int(time.time() * 1000)
        casc_res_6 = queries.cascade(TENANT, "F1_v2")
        assert casc_res_6.get("success") is True
        e6 = get_current_epoch()
        assert e6 == 6, f"Expected Epoch 6 after cascade, got {e6}"
        snap6 = queries.graph_snapshot(TENANT)
        assert snap6["epoch"] == 6
        f1_v2_node = next(n for n in snap6["nodes"] if n["id"] == "F1_v2")
        assert f1_v2_node["status"] == "QUARANTINED"
        t_e6 = int(time.time() * 1000)
        EPOCH_DATA[6] = {"t_start": t_start_e6, "t_end": t_e6, "epoch": 6, "desc": "Quarantined F1_v2"}
        EPOCH_SNAPSHOTS[6] = snap6
        print(f"Epoch 6 captured: {len(snap6['nodes'])} nodes, {len(snap6['edges'])} edges, epoch={snap6['epoch']}")

        # -------------------------------------------------------------
        # EPOCH 7: Supersede F3 (VIP discount 15% -> 20%)
        # -------------------------------------------------------------
        time.sleep(0.01)
        t_start_e7 = int(time.time() * 1000)
        sup_res_7 = queries.supersede(
            TENANT,
            old_fact_id="F3",
            new_fact_id="F3_v2",
            new_value="20",
            new_text="VIP tier discount updated to 20%",
            new_embedding=dummy_embedding,
        )
        assert sup_res_7.get("success") is True
        e7 = get_current_epoch()
        assert e7 == 7, f"Expected Epoch 7, got {e7}"
        snap7 = queries.graph_snapshot(TENANT)
        assert snap7["epoch"] == 7
        f3_old = next(n for n in snap7["nodes"] if n["id"] == "F3")
        f3_new = next(n for n in snap7["nodes"] if n["id"] == "F3_v2")
        assert f3_old["status"] == "SUPERSEDED"
        assert f3_new["status"] == "HEALTHY"
        t_e7 = int(time.time() * 1000)
        EPOCH_DATA[7] = {"t_start": t_start_e7, "t_end": t_e7, "epoch": 7, "desc": "Superseded F3 with F3_v2 (20% discount)"}
        EPOCH_SNAPSHOTS[7] = snap7
        print(f"Epoch 7 captured: {len(snap7['nodes'])} nodes, {len(snap7['edges'])} edges, epoch={snap7['epoch']}")

        # -------------------------------------------------------------
        # EPOCH 8: Restore F1_v2
        # -------------------------------------------------------------
        time.sleep(0.01)
        t_start_e8 = int(time.time() * 1000)
        rest_res_8 = queries.restore(TENANT, "F1_v2")
        assert rest_res_8.get("success") is True
        e8 = get_current_epoch()
        assert e8 == 8, f"Expected Epoch 8, got {e8}"
        snap8 = queries.graph_snapshot(TENANT)
        assert snap8["epoch"] == 8
        f1_v2_rest = next(n for n in snap8["nodes"] if n["id"] == "F1_v2")
        assert f1_v2_rest["status"] == "HEALTHY"
        t_e8 = int(time.time() * 1000)
        EPOCH_DATA[8] = {"t_start": t_start_e8, "t_end": t_e8, "epoch": 8, "desc": "Restored F1_v2 to HEALTHY"}
        EPOCH_SNAPSHOTS[8] = snap8
        print(f"Epoch 8 captured: {len(snap8['nodes'])} nodes, {len(snap8['edges'])} edges, epoch={snap8['epoch']}")

        # -------------------------------------------------------------
        # EPOCH 9: Stage & Execute Action A2 from F4 + bump epoch
        # -------------------------------------------------------------
        time.sleep(0.01)
        t_start_e9 = int(time.time() * 1000)
        queries.stage_action(TENANT, "A2", ["F4"], "notify", {"campaign": "holiday"}, "COMPENSABLE", gate="PASSED")
        exec_res = queries.execute_guard(TENANT, "A2")
        assert exec_res.get("success") is True
        queries.bump_epoch(TENANT)
        e9 = get_current_epoch()
        assert e9 == 9, f"Expected Epoch 9, got {e9}"
        snap9 = queries.graph_snapshot(TENANT)
        assert snap9["epoch"] == 9
        a2_node = next(n for n in snap9["nodes"] if n["id"] == "A2")
        assert a2_node["executed"] is True
        t_e9 = int(time.time() * 1000)
        EPOCH_DATA[9] = {"t_start": t_start_e9, "t_end": t_e9, "epoch": 9, "desc": "Staged and executed Action A2"}
        EPOCH_SNAPSHOTS[9] = snap9
        print(f"Epoch 9 captured: {len(snap9['nodes'])} nodes, {len(snap9['edges'])} edges, epoch={snap9['epoch']}")

        # -------------------------------------------------------------
        # EPOCH 10: Quarantine F4 (cascade)
        # -------------------------------------------------------------
        time.sleep(0.01)
        t_start_e10 = int(time.time() * 1000)
        casc_res_10 = queries.cascade(TENANT, "F4")
        assert casc_res_10.get("success") is True
        e10 = get_current_epoch()
        assert e10 == 10, f"Expected Epoch 10, got {e10}"
        snap10 = queries.graph_snapshot(TENANT)
        assert snap10["epoch"] == 10
        f4_node = next(n for n in snap10["nodes"] if n["id"] == "F4")
        d1_node = next(n for n in snap10["nodes"] if n["id"] == "D1")
        assert f4_node["status"] == "QUARANTINED"
        assert d1_node["status"] == "QUARANTINED"
        t_e10 = int(time.time() * 1000)
        EPOCH_DATA[10] = {"t_start": t_start_e10, "t_end": t_e10, "epoch": 10, "desc": "Cascade quarantined F4 and D1"}
        EPOCH_SNAPSHOTS[10] = snap10
        print(f"Epoch 10 captured: {len(snap10['nodes'])} nodes, {len(snap10['edges'])} edges, epoch={snap10['epoch']}")

        print("================================================================================")
        print("10 SEQUENTIAL EPOCHS SUCCESSFULLY CREATED AND VERIFIED.")
        print("================================================================================\n")

    def test_02_query_as_of_epoch_parameter_mismatch(self):
        """
        Test 2A: Parameter evaluation when calling as_of with epoch keyword or epoch integer.
        Checks:
        1. Calling queries.as_of(TENANT, epoch=1) raises TypeError (unexpected keyword argument).
        2. Calling queries.as_of(TENANT, 1) interprets 1 as timestamp_ms=1 (Jan 1, 1970 00:00:00.001 UTC)
           and returns 0 facts because all facts were created with timestamp() > 1700000000000.
        """
        print("\n================================================================================")
        print("TEST 2A: PARAMETER VERIFICATION - as_of(epoch=E)")
        print("================================================================================")

        # 1. Test keyword argument epoch=E
        keyword_error = None
        try:
            queries.as_of(TENANT, epoch=1)  # type: ignore
        except TypeError as e:
            keyword_error = str(e)

        print(f"as_of(epoch=1) result: Caught expected TypeError -> '{keyword_error}'")
        assert keyword_error is not None, "as_of should reject 'epoch' keyword argument"
        assert "unexpected keyword argument 'epoch'" in keyword_error or "epoch" in keyword_error

        # 2. Test positional argument passing integer epoch (1, 3, 5, 8)
        for ep in [1, 3, 5, 8]:
            res = queries.as_of(TENANT, ep)
            print(f"as_of(TENANT, {ep}) [interpreted as timestamp_ms={ep}ms] returned: {res}")
            assert res == [], f"Expected [] when passing epoch number {ep} as timestamp_ms"

        print("Verdict on as_of signature: Parameter signature is (tenant_id: str, timestamp_ms: int).")
        print("It does NOT accept 'epoch=E' keyword argument or map epoch integer to epoch timestamp.\n")

    def test_03_query_as_of_timestamp_across_past_epochs(self):
        """
        Test 2B & 3: Historical belief recall via as_of across past epochs (E=1, 3, 5, 8).
        Uses exact historical timestamps captured at each epoch.
        Verifies:
        - Returned node counts vs expected active facts at that timestamp.
        - Which node kinds are returned (Fact only vs other nodes).
        - Status behavior (current in-place status vs historical status).
        """
        print("\n================================================================================")
        print("TEST 2B & 3: HISTORICAL BELIEF RECALL VIA as_of(timestamp_ms)")
        print("================================================================================")

        # Epoch 1: T_e1. Active facts created: F1, F2, F3.
        t_e1 = EPOCH_DATA[1]["t_end"]
        res_e1 = queries.as_of(TENANT, t_e1)
        res_e1_ids = {r["id"] for r in res_e1}
        print(f"\n[Past Epoch 1 @ t={t_e1}]:")
        print(f"  Facts returned ({len(res_e1)}): {res_e1}")
        # At t_e1, F1, F2, F3 existed and had valid_from <= t_e1.
        # F1 was superseded at t_e4 (valid_to = t_e4 > t_e1), so valid_to check passes.
        # F3 was superseded at t_e7 (valid_to = t_e7 > t_e1), so valid_to check passes.
        # F2 was quarantined at t_e2 and released at t_e3, so at t_e1 quarantined_at was not yet <= t_e1.
        assert "F1" in res_e1_ids, "F1 should be present at Epoch 1"
        assert "F2" in res_e1_ids, "F2 should be present at Epoch 1"
        assert "F3" in res_e1_ids, "F3 should be present at Epoch 1"
        assert "F4" not in res_e1_ids, "F4 was created at Epoch 5, must not be present at Epoch 1"
        assert "F1_v2" not in res_e1_ids, "F1_v2 was created at Epoch 4, must not be present at Epoch 1"
        assert "F3_v2" not in res_e1_ids, "F3_v2 was created at Epoch 7, must not be present at Epoch 1"

        # Check returned statuses at Epoch 1:
        # NOTE: In-place property mutation issue!
        f1_e1_status = next(r["status"] for r in res_e1 if r["id"] == "F1")
        print(f"  CRITICAL LOGIC OBSERVATION: F1 status returned at Epoch 1 timestamp is '{f1_e1_status}'.")
        print("  Historically at Epoch 1, F1 was HEALTHY, but because FalkorDB mutates properties in-place,")
        print(f"  as_of returns the current property value '{f1_e1_status}'.")

        # Epoch 3: T_e3. Active facts: F1 (valid_to > t_e3), F2 (restored at t_e3, released_at <= t_e3), F3 (valid_to > t_e3).
        t_e3 = EPOCH_DATA[3]["t_end"]
        res_e3 = queries.as_of(TENANT, t_e3)
        res_e3_ids = {r["id"] for r in res_e3}
        print(f"\n[Past Epoch 3 @ t={t_e3}]:")
        print(f"  Facts returned ({len(res_e3)}): {res_e3}")
        assert "F1" in res_e3_ids
        assert "F2" in res_e3_ids
        assert "F3" in res_e3_ids
        assert "F4" not in res_e3_ids

        # Epoch 5: T_e5. F1 superseded by F1_v2. F4 created. Active facts: F1_v2, F2, F3, F4.
        t_e5 = EPOCH_DATA[5]["t_end"]
        res_e5 = queries.as_of(TENANT, t_e5)
        res_e5_ids = {r["id"] for r in res_e5}
        print(f"\n[Past Epoch 5 @ t={t_e5}]:")
        print(f"  Facts returned ({len(res_e5)}): {res_e5}")
        assert "F1_v2" in res_e5_ids, "F1_v2 should be active at Epoch 5"
        assert "F4" in res_e5_ids, "F4 should be active at Epoch 5"
        assert "F1" not in res_e5_ids, "F1 was superseded at Epoch 4 (valid_to <= t_e5), must be excluded"
        assert "F3" in res_e5_ids, "F3 not yet superseded at Epoch 5, must be present"

        # Epoch 8: T_e8. F1_v2 restored. F3 superseded by F3_v2. F4 active.
        t_e8 = EPOCH_DATA[8]["t_end"]
        res_e8 = queries.as_of(TENANT, t_e8)
        res_e8_ids = {r["id"] for r in res_e8}
        print(f"\n[Past Epoch 8 @ t={t_e8}]:")
        print(f"  Facts returned ({len(res_e8)}): {res_e8}")
        assert "F1_v2" in res_e8_ids, "F1_v2 restored at Epoch 8, must be active"
        assert "F3_v2" in res_e8_ids, "F3_v2 superseded F3 at Epoch 7, must be active"
        assert "F4" in res_e8_ids, "F4 active at Epoch 8"
        assert "F1" not in res_e8_ids, "F1 superseded at Epoch 4, must be excluded"
        assert "F3" not in res_e8_ids, "F3 superseded at Epoch 7, must be excluded"

        # Check non-Fact node exclusion:
        for res in [res_e1, res_e3, res_e5, res_e8]:
            for r in res:
                assert not r["id"].startswith("C"), "as_of must only return Facts, but returned Conclusion"
                assert not r["id"].startswith("A"), "as_of must only return Facts, but returned Action"
                assert not r["id"].startswith("D"), "as_of must only return Facts, but returned Decision"

        print("\nas_of historical filtering correctly filters Facts by valid_from/valid_to and quarantine window!")

    def test_04_graph_snapshot_as_of_ms_parameter_behavior(self):
        """
        Test 2C: Evaluate graph_snapshot(tenant_id, as_of_ms=T).
        Verifies:
        - graph_snapshot accepts as_of_ms parameter.
        - But currently ignores as_of_ms, always returning current head graph (epoch=10).
        """
        print("\n================================================================================")
        print("TEST 2C: graph_snapshot(tenant_id, as_of_ms) BEHAVIOR")
        print("================================================================================")

        t_e1 = EPOCH_DATA[1]["t_end"]
        snap_past = queries.graph_snapshot(TENANT, as_of_ms=t_e1)
        snap_head = queries.graph_snapshot(TENANT)

        print(f"Head snapshot: epoch={snap_head['epoch']}, nodes={len(snap_head['nodes'])}, edges={len(snap_head['edges'])}")
        print(f"Snapshot with as_of_ms={t_e1}: epoch={snap_past['epoch']}, nodes={len(snap_past['nodes'])}, edges={len(snap_past['edges'])}")

        assert snap_past["epoch"] == 10, "graph_snapshot currently ignores as_of_ms and returns head epoch (10)"
        assert len(snap_past["nodes"]) == len(snap_head["nodes"]), "graph_snapshot currently returns head node count"
        assert len(snap_past["edges"]) == len(snap_head["edges"]), "graph_snapshot currently returns head edge count"

        print("Verdict on graph_snapshot: Parameter 'as_of_ms' is present in function signature,")
        print("but implementation ignores it and performs unconditional MATCH (n) on current graph head.")

    def test_05_time_travel_queries_are_strictly_non_destructive(self):
        """
        Test 4: Verify time-travel queries are completely non-destructive.
        - Captures exact graph head before time-travel queries:
          - Meta.epoch
          - Node IDs, labels, and all properties
          - Edges (source, type, target, properties)
        - Runs 100 mixed time-travel queries across past epochs:
          - queries.as_of(TENANT, timestamp_ms=T_e1)
          - queries.as_of(TENANT, timestamp_ms=T_e3)
          - queries.as_of(TENANT, timestamp_ms=T_e5)
          - queries.as_of(TENANT, timestamp_ms=T_e8)
          - queries.as_of(TENANT, 1) (invalid epoch passed as ts)
          - queries.graph_snapshot(TENANT, as_of_ms=T_e1)
          - queries.graph_snapshot(TENANT, as_of_ms=T_e5)
        - Re-captures graph head and asserts:
          - Meta.epoch is unchanged (strictly 10).
          - Node count and identities are strictly unchanged.
          - Every node's properties (status, stale, executed, valid_from, valid_to, quarantined_at) are identical.
          - Edge count and topology are strictly unchanged.
        """
        print("\n================================================================================")
        print("TEST 4: NON-DESTRUCTIVE TIME-TRAVEL VERIFICATION")
        print("================================================================================")

        g = get_commons()

        # Capture pre-query head state
        pre_epoch = get_current_epoch()
        pre_nodes = g.query("MATCH (n) RETURN n.id, labels(n), properties(n) ORDER BY n.id").result_set
        pre_edges = g.query("MATCH (src)-[r]->(tgt) RETURN src.id, type(r), tgt.id, properties(r) ORDER BY src.id, tgt.id").result_set

        pre_nodes_dict = {r[0]: (r[1], r[2]) for r in pre_nodes if r[0]}
        pre_edges_set = {(r[0], r[1], r[2]) for r in pre_edges}

        print(f"Pre-query Graph Head: Epoch={pre_epoch}, Nodes={len(pre_nodes_dict)}, Edges={len(pre_edges_set)}")

        # Execute 100 time-travel queries
        t0 = time.time()
        query_count = 0
        for _ in range(20):
            for ep in [1, 3, 5, 8]:
                t_ep = EPOCH_DATA[ep]["t_end"]
                queries.as_of(TENANT, t_ep)
                queries.graph_snapshot(TENANT, as_of_ms=t_ep)
                queries.as_of(TENANT, ep)  # invalid epoch integer
                query_count += 3

        duration_ms = (time.time() - t0) * 1000
        print(f"Executed {query_count} time-travel queries in {duration_ms:.2f}ms ({duration_ms/query_count:.2f}ms/query)")

        # Capture post-query head state
        post_epoch = get_current_epoch()
        post_nodes = g.query("MATCH (n) RETURN n.id, labels(n), properties(n) ORDER BY n.id").result_set
        post_edges = g.query("MATCH (src)-[r]->(tgt) RETURN src.id, type(r), tgt.id, properties(r) ORDER BY src.id, tgt.id").result_set

        post_nodes_dict = {r[0]: (r[1], r[2]) for r in post_nodes if r[0]}
        post_edges_set = {(r[0], r[1], r[2]) for r in post_edges}

        print(f"Post-query Graph Head: Epoch={post_epoch}, Nodes={len(post_nodes_dict)}, Edges={len(post_edges_set)}")

        # Strict Assertions
        assert pre_epoch == post_epoch, f"Epoch mutated from {pre_epoch} to {post_epoch}!"
        assert len(pre_nodes_dict) == len(post_nodes_dict), "Node count mutated!"
        assert pre_nodes_dict.keys() == post_nodes_dict.keys(), "Node set mutated!"
        assert pre_edges_set == post_edges_set, "Edge set mutated!"

        # Deep property comparison for every node
        for nid, (labels, props) in pre_nodes_dict.items():
            post_labels, post_props = post_nodes_dict[nid]
            assert labels == post_labels, f"Labels mutated for node {nid}!"
            assert props == post_props, f"Properties mutated for node {nid}: {props} vs {post_props}"

        print("Non-destructive invariant verified: 100% PASS (Zero state mutation, Zero side-effects).\n")


if __name__ == "__main__":
    t = TestSnapshotTimeTravelStress()
    t.setup_class()
    try:
        t.test_01_mutate_graph_across_10_sequential_epochs()
        t.test_02_query_as_of_epoch_parameter_mismatch()
        t.test_03_query_as_of_timestamp_across_past_epochs()
        t.test_04_graph_snapshot_as_of_ms_parameter_behavior()
        t.test_05_time_travel_queries_are_strictly_non_destructive()
        print("\n>>> ALL STRESS TESTS COMPLETED SUCCESSFULLY! <<<")
    finally:
        t.teardown_class()
