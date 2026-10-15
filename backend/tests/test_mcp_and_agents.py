import os
import sys
import pytest

# Ensure repo root and backend are in path
root_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if root_path not in sys.path:
    sys.path.insert(0, root_path)

from app.services.mcp_tools import (
    remember_fact,
    derive,
    claim_handoff,
    quarantine,
)
from agents.runner import runner
from agents.procedures import procedure_registry
from app.graph.queries import get_graph_store
from app.services.helpdesk import helpdesk_service


@pytest.fixture(autouse=True)
def reset_state():
    store = get_graph_store("acme")
    store.nodes.clear()
    store.edges.clear()
    store.epoch = 1
    helpdesk_service.reset()


def test_mcp_remember_fact_and_epoch():
    res = remember_fact(
        key="refund_window",
        value="30",
        text="Refund window is 30 days.",
        token="token_agent_scout",
        origin="doc:policy",
    )
    assert res["status"] == "HEALTHY"
    assert res["epoch"] == 1
    assert "fact_id" in res


def test_mcp_derive_with_atomic_guard():
    # Attempting to derive with nonexistent parent fails
    res_fail = derive(
        text="Order eligible",
        parent_ids=["fact_missing"],
        token="token_agent_analyst",
    )
    assert res_fail["success"] is False
    assert "Atomic derivation check failed" in res_fail["error"]

    # Now create valid healthy parent
    remember_fact(
        id="fact_valid",
        key="refund_window",
        value="30",
        text="Refund 30 days",
        token="token_agent_scout",
    )
    res_ok = derive(
        text="Order eligible",
        parent_ids=["fact_valid"],
        token="token_agent_analyst",
    )
    assert res_ok["success"] is True
    assert "conc_" in res_ok["conclusion_id"]


def test_mcp_claim_handoff_role_restriction():
    from handoff_compat import _handoff_store
    from app.models.handoffs import Handoff, HandoffKind, HandoffStatus

    # Insert a REVIEW handoff into store
    review_h = Handoff(id="h_review_test", kind=HandoffKind.REVIEW, task="Review needed", status=HandoffStatus.PENDING)
    _handoff_store.append(review_h)

    # Agent token cannot claim REVIEW handoff
    res_agent = claim_handoff("h_review_test", token="token_agent_refunder")
    assert res_agent["success"] is False
    assert "cannot claim" in res_agent["error"] and "Contributor" in res_agent["error"] and "Team page" in res_agent["error"]

    # Human token can claim it
    res_human = claim_handoff("h_review_test", token="token_human")
    assert res_human["success"] is True
    assert res_human["status"] == "IN_PROGRESS"


def test_mcp_quarantine_bumps_epoch():
    remember_fact(
        id="fact_tainted",
        key="test_key",
        value="tainted",
        text="Bad claim",
        token="token_agent_scout",
    )
    q_res = quarantine("fact_tainted", token="token_auditor")
    assert q_res["success"] is True
    assert q_res["epoch"] > 1  # Epoch bumped!


def test_agent_runner_replay_mode_determinism():
    """Replay mode must yield byte-identical results three runs in a row."""
    run1 = runner.run_step("inject_poison", seed="default")
    run2 = runner.run_step("inject_poison", seed="default")
    run3 = runner.run_step("inject_poison", seed="default")

    assert run1 == run2 == run3
    assert "scout" in run1["agent_results"]
    assert len(run1["agent_results"]["scout"]["calls"]) == 1


def test_procedure_laplace_smoothed_ranking():
    proc = procedure_registry.get_best_procedure("refund")
    assert proc is not None
    initial_score = proc.rank_score

    # Record failure
    procedure_registry.record_outcome(proc.id, success=False)
    updated_proc = procedure_registry.get_best_procedure("refund")
    assert updated_proc.rank_score < initial_score


def test_two_agents_racing_for_handoff():
    from handoff_compat import _handoff_store
    from app.models.handoffs import Handoff, HandoffKind, HandoffStatus

    race_handoff = Handoff(id="h_race_task", kind=HandoffKind.TASK, task="Process payment", status=HandoffStatus.PENDING)
    _handoff_store.append(race_handoff)

    race_result = runner.simulate_claim_race("h_race_task")
    assert race_result["single_winner"] is True
    assert race_result["first_result"]["success"] is True
