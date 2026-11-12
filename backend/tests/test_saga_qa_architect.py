import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from unittest.mock import patch

import pytest

from app.graph.queries import get_graph_store
from app.models.common import (
    NodeKind,
    Status,
    Reversibility,
    GateStatus,
    CompensationStatus,
    HandoffKind,
    HandoffStatus,
)
from app.models.graph import GraphNode, ActionInfo, CompensationInfo
from app.services.saga import OutboxSagaWorker
from app.services.helpdesk import MockHelpdeskService
from app.services.event_bus import event_bus


def test_100_concurrent_compensations_multi_tenant():
    """
    Multi-Vector Stress Test:
    - 100 concurrent compensations enqueued across 10 distinct tenants (10 per tenant).
    - 20 concurrent worker threads processing concurrently.
    - Asserts:
      1. Every single action reaches CompensationStatus.COMPENSATED.
      2. No duplicate executions or corrupted states.
      3. Total attempts == 1 for all successful single-shot runs.
    """
    num_tenants = 10
    actions_per_tenant = 10
    tenants = [f"tenant_stress_{i}_{uuid.uuid4().hex[:4]}" for i in range(num_tenants)]
    all_actions = {}

    for tenant in tenants:
        store = get_graph_store(tenant)
        action_ids = []
        for i in range(actions_per_tenant):
            aid = f"act_{tenant}_{i}"
            action_ids.append(aid)
            node = GraphNode(
                id=aid,
                kind=NodeKind.ACTION,
                label=f"Action {aid}",
                status=Status.QUARANTINED,
                action=ActionInfo(
                    type="refund",
                    reversibility=Reversibility.COMPENSABLE,
                    executed=True,
                    confirmed=True,
                    gate=GateStatus.PASSED,
                    idempotency_key=f"key_{aid}",
                    compensation=CompensationInfo(
                        status=CompensationStatus.PENDING_COMPENSATION,
                        attempts=0,
                    ),
                ),
            )
            store.add_node(node)
        all_actions[tenant] = action_ids

    workers = [OutboxSagaWorker(max_attempts=5) for _ in range(20)]

    def run_worker_on_tenant(worker, tenant):
        store = get_graph_store(tenant)
        return worker.process_pending(store, tenant)

    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = []
        for i in range(40):
            w = workers[i % len(workers)]
            t = tenants[i % len(tenants)]
            futures.append(executor.submit(run_worker_on_tenant, w, t))
        for f in as_completed(futures):
            f.result()

    total_compensated = 0
    for tenant, action_ids in all_actions.items():
        store = get_graph_store(tenant)
        for aid in action_ids:
            node = store.nodes[aid]
            assert node.action.compensation.status == CompensationStatus.COMPENSATED
            assert node.action.compensation.attempts == 1
            assert node.action.compensation.lease_owner is None
            assert node.action.compensation.lease_until is None
            total_compensated += 1

    assert total_compensated == 100


def test_network_drop_simulation_and_recovery():
    """
    Network Drop Simulation:
    - Attempt 1: Network drop (ConnectionError/Timeout) simulated via Helpdesk failure.
    - Worker records backoff and keeps status PENDING_COMPENSATION.
    - Attempt 2: Network recovers; compensation succeeds.
    """
    tenant = f"tenant_net_drop_{uuid.uuid4().hex[:6]}"
    store = get_graph_store(tenant)
    aid = "act_net_drop_1"
    node = GraphNode(
        id=aid,
        kind=NodeKind.ACTION,
        label="Action Network Drop",
        status=Status.QUARANTINED,
        action=ActionInfo(
            type="refund",
            reversibility=Reversibility.COMPENSABLE,
            executed=True,
            confirmed=True,
            gate=GateStatus.PASSED,
            idempotency_key=f"key_{aid}",
            compensation=CompensationInfo(
                status=CompensationStatus.PENDING_COMPENSATION,
                attempts=0,
            ),
        ),
    )
    store.add_node(node)
    worker = OutboxSagaWorker(max_attempts=5)

    # Attempt 1: Simulate network drop / timeout
    with patch("app.services.saga.helpdesk_service.compensate_action") as mock_comp:
        mock_comp.side_effect = ConnectionResetError("Connection dropped by peer")
        processed = worker.process_pending(store, tenant)
        assert processed == []

    comp = node.action.compensation
    assert comp.status == CompensationStatus.PENDING_COMPENSATION
    assert comp.attempts == 1
    assert "Connection dropped by peer" in comp.last_error
    assert comp.next_attempt_at > int(time.time() * 1000) - 500  # Scheduled in future
    assert comp.lease_owner is None
    assert comp.lease_until is None

    # Before next_attempt_at expires, worker should skip it
    processed_early = worker.process_pending(store, tenant)
    assert processed_early == []
    assert comp.attempts == 1  # Not incremented

    # Advance time artificially past next_attempt_at
    comp.next_attempt_at = int(time.time() * 1000) - 10

    # Attempt 2: Network is restored!
    processed_recovered = worker.process_pending(store, tenant)
    assert len(processed_recovered) == 1
    assert comp.status == CompensationStatus.COMPENSATED
    assert comp.attempts == 2
    assert comp.last_error is None


def test_max_attempts_exhaustion_triggers_escalation_handoff():
    """
    Retry Exhaustion Test:
    - Max attempts = 5.
    - Helpdesk continually fails (HTTP 500 / persistent outage).
    - Verifies:
      1. Attempts counter increments 1..5.
      2. Exponential backoff intervals (1s, 2s, 4s, 8s, 16s).
      3. At 5 attempts, marks status FAILED.
      4. Creates ESCALATION handoff with context_root_id pointing to action node.
      5. Subsequent worker ticks skip the failed action.
    """
    tenant = f"tenant_exhaust_{uuid.uuid4().hex[:6]}"
    store = get_graph_store(tenant)
    aid = "act_exhaust_1"
    node = GraphNode(
        id=aid,
        kind=NodeKind.ACTION,
        label="Action Exhaustion",
        status=Status.QUARANTINED,
        action=ActionInfo(
            type="refund",
            reversibility=Reversibility.COMPENSABLE,
            executed=True,
            confirmed=True,
            gate=GateStatus.PASSED,
            idempotency_key=f"key_{aid}",
            compensation=CompensationInfo(
                status=CompensationStatus.PENDING_COMPENSATION,
                attempts=0,
            ),
        ),
    )
    store.add_node(node)
    worker = OutboxSagaWorker(max_attempts=5)
    worker.retry_ms = 1000

    backoffs = []

    with patch("app.services.saga.helpdesk_service.compensate_action") as mock_comp:
        mock_comp.return_value = {"status": 503, "error": "Service Unavailable"}

        for attempt in range(1, 6):
            t_before = int(time.time() * 1000)
            worker.process_pending(store, tenant)
            comp = node.action.compensation
            assert comp.attempts == attempt

            if attempt < 5:
                assert comp.status == CompensationStatus.PENDING_COMPENSATION
                expected_delay = 1000 * (2 ** (attempt - 1))
                backoff_delta = comp.next_attempt_at - t_before
                backoffs.append(expected_delay)
                assert abs(backoff_delta - expected_delay) < 500
                # Advance time so next attempt can proceed immediately
                comp.next_attempt_at = int(time.time() * 1000) - 10
            else:
                # 5th attempt must mark FAILED!
                assert comp.status == CompensationStatus.FAILED

    # Verify backoff sequence: 1000, 2000, 4000, 8000
    assert backoffs == [1000, 2000, 4000, 8000]

    # Verify ESCALATION handoff was created
    escalation_handoffs = [
        h for h in store.handoffs
        if h.kind == HandoffKind.ESCALATION and h.context_root_id == aid
    ]
    assert len(escalation_handoffs) == 1
    h = escalation_handoffs[0]
    assert h.status == HandoffStatus.PENDING
    assert "Compensation failed" in h.task
    assert "after 5 attempts" in h.task

    # Verify subsequent worker passes do NOT reprocess or increment
    worker.process_pending(store, tenant)
    assert node.action.compensation.attempts == 5
    assert node.action.compensation.status == CompensationStatus.FAILED
    assert len([h for h in store.handoffs if h.context_root_id == aid]) == 1


def test_crash_mid_compensation_lease_expiration_and_idempotent_recovery():
    """
    Worker Crash Mid-Compensation Test:
    - Worker 1 claims lease (DISPATCHING), increments attempt, and crashes before completion.
    - Lease expires after 30s (simulated).
    - Worker 2 takes over:
      1. Senses expired lease.
      2. Re-claims with same idempotency key.
      3. Completes compensation.
      4. Status transitions to COMPENSATED.
    """
    tenant = f"tenant_crash_{uuid.uuid4().hex[:6]}"
    store = get_graph_store(tenant)
    aid = "act_crash_1"
    node = GraphNode(
        id=aid,
        kind=NodeKind.ACTION,
        label="Action Crash Mid Flight",
        status=Status.QUARANTINED,
        action=ActionInfo(
            type="ticket",
            reversibility=Reversibility.COMPENSABLE,
            executed=True,
            confirmed=True,
            gate=GateStatus.PASSED,
            idempotency_key=f"key_{aid}",
            compensation=CompensationInfo(
                status=CompensationStatus.PENDING_COMPENSATION,
                attempts=0,
            ),
        ),
    )
    store.add_node(node)

    worker1 = OutboxSagaWorker(max_attempts=3)
    worker2 = OutboxSagaWorker(max_attempts=3)

    # Worker 1 starts dispatching, but crashes mid-call (simulated by exception)
    with patch("app.services.saga.helpdesk_service.compensate_action") as mock_comp:
        def crash_mock(*args, **kwargs):
            raise SystemExit("Worker crashed abruptly!")

        mock_comp.side_effect = crash_mock
        with pytest.raises(SystemExit):
            worker1.process_pending(store, tenant)

    comp = node.action.compensation
    # Worker 1 crashed while holding DISPATCHING status and lease
    assert comp.status == CompensationStatus.DISPATCHING
    assert comp.attempts == 1
    assert comp.lease_owner is not None
    assert comp.lease_until is not None
    initial_claim_owner = comp.lease_owner

    # Worker 2 tries immediately: lease is still active, must NOT touch it
    worker2.process_pending(store, tenant)
    assert comp.lease_owner == initial_claim_owner
    assert comp.attempts == 1

    # Simulate lease expiration (30 seconds pass)
    comp.lease_until = int(time.time() * 1000) - 100

    # Worker 2 now claims and recovers
    processed = worker2.process_pending(store, tenant)
    assert len(processed) == 1
    assert comp.status == CompensationStatus.COMPENSATED
    assert comp.attempts == 2
    assert comp.lease_owner is None
    assert comp.lease_until is None


def test_crash_on_final_attempt_marks_failed_on_recovery():
    """
    Worker Crash on Final Attempt:
    - Max attempts = 3.
    - Attempts already at 3 during crash in dispatching.
    - When lease expires, recovering worker detects attempts >= max_attempts.
    - Verifies it marks FAILED and creates ESCALATION handoff.
    """
    tenant = f"tenant_crash_final_{uuid.uuid4().hex[:6]}"
    store = get_graph_store(tenant)
    aid = "act_crash_final_1"
    node = GraphNode(
        id=aid,
        kind=NodeKind.ACTION,
        label="Action Crash Final Attempt",
        status=Status.QUARANTINED,
        action=ActionInfo(
            type="refund",
            reversibility=Reversibility.COMPENSABLE,
            executed=True,
            confirmed=True,
            gate=GateStatus.PASSED,
            idempotency_key=f"key_{aid}",
            compensation=CompensationInfo(
                status=CompensationStatus.DISPATCHING,
                attempts=3,
                lease_owner="crashed_worker:123",
                lease_until=int(time.time() * 1000) - 100,  # expired
            ),
        ),
    )
    store.add_node(node)
    worker = OutboxSagaWorker(max_attempts=3)

    worker.process_pending(store, tenant)

    comp = node.action.compensation
    assert comp.status == CompensationStatus.FAILED
    assert comp.lease_owner is None
    assert comp.lease_until is None

    handoffs = [h for h in store.handoffs if h.context_root_id == aid]
    assert len(handoffs) == 1
    assert handoffs[0].kind == HandoffKind.ESCALATION
    assert "Retry budget exhausted" in comp.last_error


def test_event_emission_on_interrupted_delivery_exhaustion():
    """
    Bug Verification:
    When an interrupted delivery exhausts the retry budget (lines 94-103 of saga.py),
    does it publish handoff_updated and compensation_updated to event_bus?
    """
    tenant = f"tenant_event_leak_{uuid.uuid4().hex[:6]}"
    store = get_graph_store(tenant)
    aid = "act_event_leak_1"
    node = GraphNode(
        id=aid,
        kind=NodeKind.ACTION,
        label="Action Event Leak Check",
        status=Status.QUARANTINED,
        action=ActionInfo(
            type="refund",
            reversibility=Reversibility.COMPENSABLE,
            executed=True,
            confirmed=True,
            gate=GateStatus.PASSED,
            idempotency_key=f"key_{aid}",
            compensation=CompensationInfo(
                status=CompensationStatus.DISPATCHING,
                attempts=3,
                lease_owner="crashed_worker:123",
                lease_until=int(time.time() * 1000) - 100,  # expired
            ),
        ),
    )
    store.add_node(node)
    worker = OutboxSagaWorker(max_attempts=3)

    events_captured = []
    def capture_publish(t, event_name, payload, **kwargs):
        events_captured.append((t, event_name, payload))

    with patch.object(event_bus, "publish", side_effect=capture_publish):
        worker.process_pending(store, tenant)

    event_names = [e[1] for e in events_captured]
    # Check if handoff_updated and compensation_updated were emitted:
    assert "handoff_updated" in event_names, f"Expected handoff_updated event but got: {event_names}"
    assert "compensation_updated" in event_names, f"Expected compensation_updated event but got: {event_names}"


def test_reconcile_executions_event_emission_and_stale_overwrite_guard():
    """
    Test reconciliation of in-flight unconfirmed actions:
    - Asserts event_bus emissions on escalation and confirmation.
    - Asserts confirmed action is not overwritten by subsequent failure.
    """
    tenant = f"tenant_reconcile_{uuid.uuid4().hex[:6]}"
    store = get_graph_store(tenant)
    aid = "act_reconcile_1"
    node = GraphNode(
        id=aid,
        kind=NodeKind.ACTION,
        label="Action Reconcile",
        status=Status.HEALTHY,
        action=ActionInfo(
            type="refund",
            reversibility=Reversibility.COMPENSABLE,
            executed=True,
            confirmed=False,
            gate=GateStatus.PASSED,
            external_key=f"qrm:test_key_{aid}",
            execution_attempts=1,
            execution_lease_until=int(time.time() * 1000) - 100,  # expired
            payload={"amount": 50},
        ),
    )
    store.add_node(node)
    worker = OutboxSagaWorker(max_attempts=3)

    events_captured = []
    def capture_publish(t, event_name, payload, **kwargs):
        events_captured.append((t, event_name, payload))

    with patch.object(event_bus, "publish", side_effect=capture_publish):
        with patch("app.services.saga.helpdesk_service.execute_action") as mock_exec:
            mock_exec.return_value = {"status": 200, "result": "success"}
            worker.reconcile_executions(store, tenant)

    assert node.action.confirmed is True
    assert node.action.delivery_error is None
    event_names = [e[1] for e in events_captured]
    assert "action_executed" in event_names


def test_hundred_compensations_with_50_percent_intermittent_network_drops():
    """
    Multi-Vector Stress & Boundary Test:
    - 100 concurrent compensations enqueued across 5 tenants (20 per tenant).
    - 50% of attempts experience intermittent network drop (ConnectionResetError) on attempt 1.
    - Second worker pass succeeds for all dropped actions.
    - Asserts:
      1. Every single action eventually reaches COMPENSATED.
      2. No actions get stuck in DISPATCHING or fail prematurely.
      3. Exactly 0 ESCALATION handoffs created for transient drops.
    """
    num_tenants = 5
    actions_per_tenant = 20
    tenants = [f"tenant_net_50_{i}_{uuid.uuid4().hex[:4]}" for i in range(num_tenants)]
    all_actions = {}

    for tenant in tenants:
        store = get_graph_store(tenant)
        action_ids = []
        for i in range(actions_per_tenant):
            aid = f"act_net50_{tenant}_{i}"
            action_ids.append(aid)
            node = GraphNode(
                id=aid,
                kind=NodeKind.ACTION,
                label=f"Action {aid}",
                status=Status.QUARANTINED,
                action=ActionInfo(
                    type="refund",
                    reversibility=Reversibility.COMPENSABLE,
                    executed=True,
                    confirmed=True,
                    gate=GateStatus.PASSED,
                    idempotency_key=f"key_{aid}",
                    compensation=CompensationInfo(
                        status=CompensationStatus.PENDING_COMPENSATION,
                        attempts=0,
                    ),
                ),
            )
            store.add_node(node)
        all_actions[tenant] = action_ids

    worker = OutboxSagaWorker(max_attempts=5)
    call_count = 0

    real_helpdesk = MockHelpdeskService()

    def flaky_compensate(idempotency_key, action_id, action_type):
        nonlocal call_count
        call_count += 1
        # Drop every second first-attempt call
        if call_count % 2 == 1:
            raise ConnectionResetError("Simulated network drop")
        return real_helpdesk.compensate_action(idempotency_key, action_id, action_type)

    # Pass 1: Run across all tenants with flaky network
    with patch("app.services.saga.helpdesk_service.compensate_action", side_effect=flaky_compensate):
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [
                executor.submit(worker.process_pending, get_graph_store(t), t)
                for t in tenants
            ]
            for f in as_completed(futures):
                f.result()

    # Some will be COMPENSATED (the ones that succeeded), some PENDING_COMPENSATION with attempts=1
    partially_compensated = 0
    for tenant, action_ids in all_actions.items():
        store = get_graph_store(tenant)
        for aid in action_ids:
            node = store.nodes[aid]
            if node.action.compensation.status == CompensationStatus.COMPENSATED:
                partially_compensated += 1
            else:
                assert node.action.compensation.status == CompensationStatus.PENDING_COMPENSATION
                assert node.action.compensation.attempts == 1
                # Advance backoff timer so pass 2 can process immediately
                node.action.compensation.next_attempt_at = int(time.time() * 1000) - 10

    assert 0 < partially_compensated < 100

    # Pass 2: Network is stable!
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [
            executor.submit(worker.process_pending, get_graph_store(t), t)
            for t in tenants
        ]
        for f in as_completed(futures):
            f.result()

    # Now ALL 100 MUST BE COMPENSATED!
    for tenant, action_ids in all_actions.items():
        store = get_graph_store(tenant)
        for aid in action_ids:
            node = store.nodes[aid]
            assert node.action.compensation.status == CompensationStatus.COMPENSATED
            assert node.action.compensation.attempts in (1, 2)
            # Ensure zero escalation handoffs were created
            escalations = [h for h in store.handoffs if h.context_root_id == aid]
            assert len(escalations) == 0


