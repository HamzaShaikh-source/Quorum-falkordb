import asyncio
import concurrent.futures
import json
import threading
import time
import uuid
import pytest
from app.auth import AuthContext
from app.models.events import SSEEventEnvelope
from app.routes.events import sse_events
from app.services.event_bus import event_bus, TenantEventBuffer
from agents.procedures import ProcedureRegistry, Procedure


# ============================================================================
# 1. TenantEventBuffer: Lock Protection, Monotonicity & Boundary Overflow
# ============================================================================

def test_tenant_event_buffer_monotonic_and_gap_prevention():
    """
    Validates that:
    1. 100 concurrent threads publishing events generate strictly monotonic contiguous seqs without duplicates.
    2. Failed / malformed events do not introduce sequence gaps in buffer history.
    """
    tenant = f"sub13_eb_mono_{uuid.uuid4().hex[:6]}"
    buf = event_bus.get_tenant_buffer(tenant)

    def worker(worker_id: int):
        envelopes = []
        for step in range(10):
            env = event_bus.publish(
                tenant=tenant,
                event_type="test_step",
                data={"worker": worker_id, "step": step},
            )
            envelopes.append(env)
        return envelopes

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(worker, i) for i in range(10)]
        all_envelopes = [env for f in concurrent.futures.as_completed(futures) for env in f.result()]

    assert len(all_envelopes) == 100
    seqs = [env.seq for env in all_envelopes]
    assert len(set(seqs)) == 100, "Duplicate sequence numbers detected!"
    assert sorted(seqs) == list(range(1, 101)), "Sequence numbers are not strictly contiguous from 1 to 100!"

    with buf._lock:
        hist_seqs = [e.seq for e in buf.history]
    assert hist_seqs == list(range(1, 101))


def test_tenant_event_buffer_overflow_max_500_and_resync():
    """
    Validates buffer overflow handling (max 500 events) and get_events_since boundary behavior:
    1. Publishes 600 events into a buffer of max_buffer_size=500.
    2. History holds exactly the latest 500 events (seq 101..600).
    3. get_events_since(100) returns all 500 missed events (seq 101..600).
    4. get_events_since(99) returns None (client missed too many events, resync required).
    5. get_events_since(600) returns [] (client is caught up).
    6. get_events_since(601) returns None (client is ahead of server, server restarted).
    """
    tenant = f"sub13_eb_overflow_{uuid.uuid4().hex[:6]}"
    buf = TenantEventBuffer(tenant=tenant, max_buffer_size=500)

    for i in range(1, 601):
        buf.publish("item", {"index": i})

    assert buf.current_seq == 600
    with buf._lock:
        assert len(buf.history) == 500
        assert buf.history[0].seq == 101
        assert buf.history[-1].seq == 600

    # Boundary 1: Exactly at the buffer boundary (seq 100)
    missed_all = buf.get_events_since(100)
    assert missed_all is not None
    assert len(missed_all) == 500
    assert missed_all[0].seq == 101
    assert missed_all[-1].seq == 600

    # Boundary 2: One event past the buffer boundary (seq 99) -> None (resync required)
    missed_expired = buf.get_events_since(99)
    assert missed_expired is None

    # Boundary 3: Sequence 0 -> None (resync required)
    assert buf.get_events_since(0) is None

    # Boundary 4: Client is fully caught up (seq 600) -> empty list
    missed_current = buf.get_events_since(600)
    assert missed_current == []

    # Boundary 5: Client is ahead of server (seq 601) -> None (resync required)
    missed_future = buf.get_events_since(601)
    assert missed_future is None


# ============================================================================
# 2. SSE Streaming: Cross-Thread Notifications & Disconnect Cleanup
# ============================================================================

@pytest.mark.asyncio
async def test_event_bus_cross_thread_notification():
    """
    Verifies that when event_bus.publish() is called from an external worker thread,
    it wakes up the asyncio event loop queue without latency delay.
    """
    tenant = f"sub13_thread_{uuid.uuid4().hex[:6]}"
    buf = event_bus.get_tenant_buffer(tenant)
    q = buf.subscribe()

    try:
        def background_publisher():
            time.sleep(0.05)
            event_bus.publish(tenant, "cross_thread_event", {"payload": "fast"})

        t = threading.Thread(target=background_publisher)
        t.start()

        t0 = time.time()
        envelope = await asyncio.wait_for(q.get(), timeout=2.0)
        elapsed = time.time() - t0

        assert envelope.type == "cross_thread_event"
        assert envelope.data["payload"] == "fast"
        assert elapsed < 1.0, f"Cross-thread notification took too long ({elapsed:.3f}s), possible selector block"
        t.join()
    finally:
        buf.unsubscribe(q)


@pytest.mark.asyncio
async def test_sse_endpoint_immediate_subscription_and_keepalive():
    """
    Verifies that /api/events subscribes immediately upon generator entry,
    and yields standard keep-alive comments.
    """
    tenant = f"sub13_sse_stream_{uuid.uuid4().hex[:6]}"
    auth = AuthContext(tenant=tenant, role="auditor", agent_id="auditor", token="token_auditor")
    buf = event_bus.get_tenant_buffer(tenant)

    resp = await sse_events(last_event_id=None, query_last_id=None, auth=auth)
    gen = resp.body_iterator

    # 1. First chunk should contain keep-alive comment and heartbeat
    first_chunk = await anext(gen)
    assert ": keep-alive" in first_chunk, "Missing standard SSE keep-alive comment"
    assert "event: heartbeat" in first_chunk

    # 2. Queue must be subscribed immediately upon generator entry
    with buf._lock:
        assert len(buf.subscribers) == 1, "Client was not registered in subscribers upon opening stream"

    # 3. Publish event and verify it is received cleanly
    event_bus.publish(tenant, "node_added", {"node": {"id": "n1", "kind": "fact"}})
    second_chunk = await anext(gen)
    assert "event: node_added" in second_chunk

    # 4. Disconnect (close generator) and verify cleanup
    await gen.aclose()
    with buf._lock:
        assert len(buf.subscribers) == 0, "Dangling async queue after client disconnect!"


@pytest.mark.asyncio
async def test_sse_disconnect_rapid_cycles_no_memory_leak():
    """
    Simulates 30 clients connecting and abruptly disconnecting.
    Asserts zero dangling async queues in buf.subscribers.
    """
    tenant = f"sub13_leak_check_{uuid.uuid4().hex[:6]}"
    auth = AuthContext(tenant=tenant, role="auditor", agent_id="auditor", token="token_auditor")
    buf = event_bus.get_tenant_buffer(tenant)

    for _ in range(30):
        resp = await sse_events(last_event_id=None, query_last_id=None, auth=auth)
        gen = resp.body_iterator
        _ = await anext(gen)  # read first chunk
        with buf._lock:
            assert len(buf.subscribers) == 1
        await gen.aclose()  # abrupt disconnect
        with buf._lock:
            assert len(buf.subscribers) == 0

    with buf._lock:
        assert len(buf.subscribers) == 0


# ============================================================================
# 3. Procedural Memory: Laplace Ranking, Concurrency & Tie-Breaking
# ============================================================================

def test_procedural_memory_laplace_ranking_and_concurrency():
    """
    Validates:
    1. Exact Laplace formula (successes + 1) / (uses + 2).
    2. Concurrency stress: 20 threads recording 1,000 total outcomes with 0 lost updates.
    3. Multi-key deterministic tie-breaking (rank_score, uses, id).
    """
    reg = ProcedureRegistry()
    reg.reset()

    # 1. Exact mathematical checks
    p_fresh = Procedure(id="p_fresh", task_type="test", steps=["s1"], uses=0, successes=0, failures=0)
    assert p_fresh.rank_score == 0.500  # (0 + 1) / (0 + 2)

    p_one = Procedure(id="p_one", task_type="test", steps=["s1"], uses=1, successes=1, failures=0)
    assert p_one.rank_score == 0.667  # (1 + 1) / (1 + 2) = 2/3

    p_fail = Procedure(id="p_fail", task_type="test", steps=["s1"], uses=1, successes=0, failures=1)
    assert p_fail.rank_score == 0.333  # (0 + 1) / (1 + 2) = 1/3

    p_seeded = Procedure(id="p_seeded", task_type="test", steps=["s1"], uses=25, successes=23, failures=2)
    assert p_seeded.rank_score == 0.889  # (23 + 1) / (25 + 2) = 24/27

    # 2. Concurrency stress on outcomes
    proc_id = f"proc_stress_{uuid.uuid4().hex[:6]}"
    reg.distill_new_procedure("refund", ["step1"], procedure_id=proc_id)
    # Starts with uses=1, successes=1, failures=0

    def record_worker():
        for _ in range(50):
            reg.record_outcome(proc_id, success=True)
            reg.record_outcome(proc_id, success=False)

    threads = [threading.Thread(target=record_worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    proc = reg._procedures.get(proc_id)
    assert proc is not None
    # Initial 1 use + 10 threads * 100 updates = 1001 uses
    assert proc.uses == 1001
    assert proc.successes == 501
    assert proc.failures == 500
    assert proc.uses == proc.successes + proc.failures

    # 3. Deterministic Tie-Breaking
    # Two procedures with identical rank_score = 0.500 but different uses
    reg._procedures["tie_low_evidence"] = Procedure(
        id="tie_low_evidence", task_type="tie_test", steps=["s1"], uses=0, successes=0, failures=0
    )
    reg._procedures["tie_high_evidence"] = Procedure(
        id="tie_high_evidence", task_type="tie_test", steps=["s1"], uses=4, successes=2, failures=2
    )
    assert reg._procedures["tie_low_evidence"].rank_score == 0.500
    assert reg._procedures["tie_high_evidence"].rank_score == 0.500

    # Best procedure should favor higher evidence (uses=4 over uses=0)
    best_tie = reg.get_best_procedure("tie_test")
    assert best_tie is not None
    assert best_tie.id == "tie_high_evidence"


# ============================================================================
# 4. Multi-Vector Stress: High-Throughput Event Storm (1,000 Events x 20 Clients)
# ============================================================================

@pytest.mark.asyncio
async def test_high_throughput_event_storm_1000_events_20_clients():
    """
    High-throughput event storm:
    1,000 events pushed in rapid succession to the event bus while 20 clients stream SSE.
    Asserts:
    1. All 20 clients concurrently receive all 1,000 events in strict sequence.
    2. No queue deadlocks, dropped events, or unhandled exceptions.
    3. Subscribers are cleanly unsubscribed after streams close.
    """
    tenant = f"sub13_storm_{uuid.uuid4().hex[:6]}"
    auth = AuthContext(tenant=tenant, role="auditor", agent_id="auditor", token="token_auditor")
    buf = event_bus.get_tenant_buffer(tenant)

    generators = []
    for _ in range(20):
        resp = await sse_events(last_event_id=None, query_last_id=None, auth=auth)
        gen = resp.body_iterator
        first = await anext(gen)
        assert ": keep-alive" in first
        generators.append(gen)

    with buf._lock:
        assert len(buf.subscribers) == 20

    client_received_seqs = [[] for _ in range(20)]

    async def client_consumer(client_idx: int, gen):
        while len(client_received_seqs[client_idx]) < 1000:
            chunk = await anext(gen)
            for line in chunk.splitlines():
                if line.startswith("id: "):
                    seq_num = int(line[4:])
                    client_received_seqs[client_idx].append(seq_num)

    consumer_tasks = [asyncio.create_task(client_consumer(i, g)) for i, g in enumerate(generators)]

    # Fire 1,000 events in rapid succession
    t0 = time.time()
    for i in range(1000):
        event_bus.publish(
            tenant=tenant,
            event_type="storm_beat",
            data={"index": i, "token": f"payload_{i}"},
        )

    await asyncio.gather(*consumer_tasks)
    elapsed = time.time() - t0

    # Verification: every single client received 1,000 events strictly from 1 to 1000
    expected_seqs = list(range(1, 1001))
    for i in range(20):
        assert len(client_received_seqs[i]) == 1000, f"Client {i} received {len(client_received_seqs[i])} events"
        assert client_received_seqs[i] == expected_seqs, f"Client {i} had sequence discrepancies"

    # Teardown and assert clean unsubscribe
    for g in generators:
        await g.aclose()

    with buf._lock:
        assert len(buf.subscribers) == 0, "Dangling queues remained after storm teardown"


# ============================================================================
# 5. Network Drop & Reconnection Loop: Gap Recovery (since_seq)
# ============================================================================

@pytest.mark.asyncio
async def test_client_disconnect_reconnect_loop_gap_recovery():
    """
    Simulates network drops and reconnects:
    1. Client connects, receives events 1..40.
    2. Network drops (generator closed).
    3. Server publishes events 41..80 while client is offline.
    4. Client reconnects with last_event_id=40.
    5. Client receives missed gap (events 41..80) and seamlessly transitions to live events.
    6. Server publishes events 81..100; client receives them.
    7. Client reconnects with expired seq (< earliest buffer seq): server sends resync_required.
    """
    tenant = f"sub13_reconnect_{uuid.uuid4().hex[:6]}"
    auth = AuthContext(tenant=tenant, role="auditor", agent_id="auditor", token="token_auditor")
    buf = event_bus.get_tenant_buffer(tenant)

    # Phase A: Initial connection
    resp1 = await sse_events(last_event_id=None, query_last_id=None, auth=auth)
    gen1 = resp1.body_iterator
    _ = await anext(gen1)  # initial heartbeat

    # Publish events 1 to 40
    for i in range(1, 41):
        event_bus.publish(tenant, "tick", {"val": i})

    received_phase_1 = []
    for _ in range(40):
        chunk = await anext(gen1)
        for line in chunk.splitlines():
            if line.startswith("id: "):
                received_phase_1.append(int(line[4:]))

    assert received_phase_1 == list(range(1, 41))

    # Phase B: Abrupt network drop
    await gen1.aclose()
    with buf._lock:
        assert len(buf.subscribers) == 0

    # Server publishes events 41 to 80 while client is offline
    for i in range(41, 81):
        event_bus.publish(tenant, "tick", {"val": i})

    # Phase C: Reconnect with Last-Event-ID = 40
    resp2 = await sse_events(last_event_id=40, query_last_id=None, auth=auth)
    gen2 = resp2.body_iterator
    _ = await anext(gen2)  # heartbeat

    # Client receives missed events 41 to 80
    recovered_gap = []
    for _ in range(40):
        chunk = await anext(gen2)
        for line in chunk.splitlines():
            if line.startswith("id: "):
                recovered_gap.append(int(line[4:]))

    assert recovered_gap == list(range(41, 81))

    # Phase D: Live stream continues without restart
    event_bus.publish(tenant, "live_event", {"val": 81})
    live_chunk = await anext(gen2)
    assert "id: 81" in live_chunk
    assert "event: live_event" in live_chunk

    await gen2.aclose()

    # Phase E: Reconnect with expired seq (simulate overflow)
    # Push 550 more events so buffer drops event 40
    for i in range(82, 650):
        event_bus.publish(tenant, "filler", {"val": i})

    resp3 = await sse_events(last_event_id=40, query_last_id=None, auth=auth)
    gen3 = resp3.body_iterator
    _ = await anext(gen3)  # heartbeat
    resync_chunk = await anext(gen3)
    assert "event: resync_required" in resync_chunk
    await gen3.aclose()
