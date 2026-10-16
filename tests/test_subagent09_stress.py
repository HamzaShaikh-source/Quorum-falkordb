import asyncio
import threading
import time
import pytest
from app.services.event_bus import event_bus, TenantEventBuffer
from app.services.scenario_orchestrator import scenario_orchestrator
from app.graph.queries import get_graph_store


@pytest.mark.asyncio
async def test_high_throughput_event_storm():
    """
    High-throughput event storm: 1,000 events pushed in rapid succession
    while 20 clients stream SSE.
    """
    tenant = "test_storm"
    buf = event_bus.get_tenant_buffer(tenant)
    client_count = 20
    event_count = 1000

    received_per_client = [[] for _ in range(client_count)]
    stop_flags = [asyncio.Event() for _ in range(client_count)]

    # 20 subscriber queues
    queues = [buf.subscribe() for _ in range(client_count)]

    async def client_worker(idx: int, q: asyncio.Queue):
        while not stop_flags[idx].is_set():
            try:
                ev = await asyncio.wait_for(q.get(), timeout=0.1)
                received_per_client[idx].append(ev.seq)
                if len(received_per_client[idx]) == event_count:
                    break
            except asyncio.TimeoutError:
                if stop_flags[idx].is_set():
                    break

    # Start 20 client tasks
    client_tasks = [
        asyncio.create_task(client_worker(i, queues[i]))
        for i in range(client_count)
    ]

    # Publish 1,000 events in rapid succession (from multiple threads to simulate concurrent writes)
    def pusher(start_idx, count):
        for i in range(count):
            buf.publish("test_event", {"payload": f"val_{start_idx + i}"})

    threads = []
    # 4 worker threads publishing 250 events each = 1,000 events
    for t_i in range(4):
        t = threading.Thread(target=pusher, args=(t_i * 250, 250))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    # Wait for clients to consume all 1,000 events
    start_wait = time.time()
    while any(len(r) < event_count for r in received_per_client):
        await asyncio.sleep(0.05)
        if time.time() - start_wait > 5.0:
            break

    # Stop clients and cleanup
    for sf in stop_flags:
        sf.set()
    await asyncio.gather(*client_tasks, return_exceptions=True)

    for q in queues:
        buf.unsubscribe(q)

    # Assertions
    # 1. Monotonic sequence: current_seq must be exactly 1000
    assert buf.current_seq == 1000, f"Expected current_seq 1000, got {buf.current_seq}"

    # 2. Buffer overflow: history must be capped at max_buffer_size (500)
    assert len(buf.history) == 500, f"Expected history capped at 500, got {len(buf.history)}"
    assert buf.history[0].seq == 501
    assert buf.history[-1].seq == 1000

    # 3. All 20 clients received all 1,000 events strictly in increasing sequence
    for i in range(client_count):
        rec = received_per_client[i]
        assert len(rec) == event_count, f"Client {i} received {len(rec)}/{event_count} events"
        # Verify strictly increasing
        for j in range(1, len(rec)):
            assert rec[j] == rec[j - 1] + 1, f"Client {i} received out of order: {rec[j - 1]} followed by {rec[j]}"


def test_reset_hammer_concurrency():
    """
    Reset hammer: 30 parallel requests to scenario_orchestrator.reset.
    Must maintain invariant consistency and thread safety.
    """
    tenant = "test_reset_hammer"
    errors = []

    def call_reset():
        try:
            res = scenario_orchestrator.reset(tenant)
            assert res["status"] == "reset"
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=call_reset) for _ in range(30)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0, f"Reset hammer encountered errors: {errors}"
    store = get_graph_store(tenant)
    assert "src_policy_v3" in store.nodes
    assert "fact_30d" in store.nodes
    assert len(store.nodes) == 2
    assert len(store.edges) == 1
    inv = store.check_invariants()
    assert inv["ok"] is True


def test_out_of_order_beat_execution():
    """
    Rapid out-of-order beat execution:
    Beat 4 (supersede) before beat 1 (inject_poison)
    Beat 3 (audit) before beat 1
    Beat 2 (run_agents) before beat 1
    All must succeed deterministically without uncaught exceptions or corrupted state.
    """
    tenant = "test_ooo_beats"
    scenario_orchestrator.reset(tenant)

    # 1. Beat 4 before beat 1
    res4 = scenario_orchestrator.supersede(tenant)
    assert res4["status"] == "superseded"
    assert res4["new_fact"] == "fact_45d_v4"

    # Reset
    scenario_orchestrator.reset(tenant)

    # 2. Beat 3 before beat 1 (auto-injects poison and audits)
    res3 = scenario_orchestrator.audit(tenant)
    assert res3["status"] == "audited"

    # Reset
    scenario_orchestrator.reset(tenant)

    # 3. Beat 2 before beat 1 (auto-injects poison and runs agents)
    res2 = scenario_orchestrator.run_agents(tenant)
    assert res2["status"] == "agents_completed"
    assert "conc_eligible_1042" in res2["derived_decision"]

    # 4. Beat 3 after beat 2
    res3_after_2 = scenario_orchestrator.audit(tenant)
    assert res3_after_2["status"] == "audited"
    assert res3_after_2["quarantined_count"] > 0
    assert res3_after_2["execute_guard_refused_staged"] is True

    # 5. Beat 4 after beat 3
    res4_after_3 = scenario_orchestrator.supersede(tenant)
    assert res4_after_3["status"] == "superseded"


def test_event_buffer_get_events_since():
    """
    Test get_events_since edge cases:
    - Normal range
    - Client ahead of server
    - Client missed too many (gap > buffer)
    - Negative sequence number
    - Empty buffer
    """
    tenant = "test_since"
    buf = TenantEventBuffer(tenant, max_buffer_size=10)

    # Empty buffer: client at seq 0 returns []
    assert buf.get_events_since(0) == []
    # Client ahead of empty buffer: seq 5 > current_seq (0) -> None
    assert buf.get_events_since(5) is None

    # Push 15 events (exceeds max_buffer_size of 10)
    for i in range(15):
        buf.publish("test", {"i": i})

    assert buf.current_seq == 15
    assert len(buf.history) == 10
    # Earliest in history is seq 6
    assert buf.history[0].seq == 6

    # Client at seq 5: 5 == earliest - 1 (contiguous, missed 0)
    events = buf.get_events_since(5)
    assert events is not None
    assert len(events) == 10
    assert events[0].seq == 6

    # Client at seq 4: 4 < 5 (gap! dropped event 5)
    assert buf.get_events_since(4) is None

    # Client at seq 0: gap
    assert buf.get_events_since(0) is None

    # Client at seq -1: gap
    assert buf.get_events_since(-1) is None

    # Client at seq 15: up to date
    up_to_date = buf.get_events_since(15)
    assert up_to_date == []

    # Client at seq 16: ahead of server
    assert buf.get_events_since(16) is None
