"""
Test Suite: Mock Helpdesk Service & Tombstone Idempotency Rule
"""

from fastapi.testclient import TestClient
from mock_helpdesk.main import app, tombstones, executed_actions, compensations

client = TestClient(app)

def setup_function():
    tombstones.clear()
    executed_actions.clear()
    compensations.clear()


def test_normal_execute_and_compensate():
    # Execute an action
    res_exec = client.post("/api/helpdesk/execute", json={
        "idempotency_key": "idem_101",
        "action_type": "REFUND",
        "amount": 50.0
    })
    assert res_exec.status_code == 200
    assert res_exec.json()["status"] == "executed"

    # Compensate the executed action
    res_comp = client.post("/api/helpdesk/compensate", json={
        "idempotency_key": "idem_101",
        "reason": "Policy violation quarantine"
    })
    assert res_comp.status_code == 200
    assert res_comp.json()["status"] == "compensated"


def test_tombstone_rule_rejects_late_execution():
    """
    CRITICAL TOMBSTONE INVARIANT:
    A compensation for an unseen key records a TOMBSTONE.
    A later execute with that key is REJECTED (409 Conflict),
    preventing in-flight race conditions from executing after compensation.
    """
    unseen_key = "idem_unseen_race_999"

    # 1. Compensation arrives first for an unseen key
    res_comp = client.post("/api/helpdesk/compensate", json={
        "idempotency_key": unseen_key,
        "reason": "Quarantine cascade arrived before execute returned"
    })
    assert res_comp.status_code == 200
    assert res_comp.json()["status"] == "tombstoned_unseen_key"
    assert unseen_key in tombstones

    # 2. Late execute attempt with that key MUST BE REJECTED with 409
    res_exec = client.post("/api/helpdesk/execute", json={
        "idempotency_key": unseen_key,
        "action_type": "REFUND",
        "amount": 99.0
    })
    assert res_exec.status_code == 409
    assert "tombstoned" in res_exec.json()["detail"]
