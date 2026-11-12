"""Corroboration must come from independent sources. A different origin LABEL is not independence: one claim can be counted twice."""
import uuid

import pytest

from app.graph import queries
from app.services import mcp_tools as t
from app.services.gate import quorum_gate


@pytest.fixture()
def tenant():
    name = f"ic{uuid.uuid4().hex[:6]}"
    queries.forget_stores()
    yield name
    queries.forget_stores()


def agent(tenant, who="agent"):
    return f"token_{tenant}_{who}"


def stage(tenant):
    return t.stage_action("Wire the refund", "payment", "irreversible", ["leaf"], agent(tenant))


def test_one_agent_cannot_corroborate_itself_by_relabelling_its_origin(tenant):
    a = agent(tenant)
    t.remember_fact("refund_window", "90", "Partner says refunds last 90 days.", a, id="leaf", origin="feedA")
    t.remember_fact("refund_window", "90", "A second note about refunds lasting ninety days.", a, id="echo", origin="feedB")
    r = stage(tenant)
    assert r["gate"] == "BLOCKED", "the same agent under two labels is one witness"


def test_two_copies_of_one_statement_under_different_labels_are_one_claim(tenant):
    t.remember_fact("refund_window", "90", "Partner says refunds last 90 days.", agent(tenant), id="leaf", origin="site-one")
    t.remember_fact("refund_window", "90", "Partner says refunds last 90 days.", agent(tenant, "human"), id="copy", origin="site-two")
    assert stage(tenant)["gate"] == "BLOCKED", "identical wording is a copy, not a second witness"


def test_the_same_source_under_a_writers_own_prefix_is_still_one_source(tenant):
    store = queries.get_graph_store(tenant)
    t.remember_fact("refund_window", "90", "Partner says refunds last 90 days.", agent(tenant), id="leaf", origin="partner-feed")
    t.remember_fact("refund_window", "90", "Refund window: ninety days, per the partner.", agent(tenant, "human"), id="other", origin="partner-feed")
    a, b = store.nodes["leaf"], store.nodes["other"]
    assert not quorum_gate.independent(a, b)
    assert stage(tenant)["gate"] == "BLOCKED"


def test_a_genuinely_independent_witness_still_corroborates(tenant):
    t.remember_fact("refund_window", "90", "Partner says refunds last 90 days.", agent(tenant), id="leaf", origin="partner-feed")
    t.remember_fact("refund_window", "90", "The signed contract grants a 90 day refund window.", agent(tenant, "human"), id="contract", origin="signed-contract")
    r = stage(tenant)
    assert r["gate"] == "PASSED", r


def test_the_gate_names_the_reason_for_review(tenant):
    t.remember_fact("refund_window", "90", "Partner says refunds last 90 days.", agent(tenant), id="leaf", origin="feedA")
    t.remember_fact("refund_window", "90", "Partner says refunds last 90 days.", agent(tenant, "human"), id="copy", origin="feedB")
    r = stage(tenant)
    assert r["gate"] == "BLOCKED" and "uncorroborated" in (r["blocked_reason"] or "")


def test_a_declared_copy_chain_is_one_claim(tenant):
    a, h = agent(tenant), agent(tenant, "human")
    t.remember_fact("refund_window", "90", "Partner says refunds last 90 days.", a, id="leaf", origin="partner-feed")
    t.remember_fact("note", "x", "A relay note.", agent(tenant, "auditor"), id="mid", origin="relay", copied_from="leaf")
    t.remember_fact("refund_window", "90", "As restated in the weekly notes: ninety days.", h, id="weekly", origin="weekly-notes", copied_from="mid")
    assert stage(tenant)["gate"] == "BLOCKED", "an echo that descends from the leaf through a chain of copies is not a second witness"


def test_a_lightly_edited_copy_is_not_a_second_witness(tenant):
    t.remember_fact("refund_window", "90", "Partner feed: refunds are allowed for 90 days after delivery.", agent(tenant), id="leaf", origin="feedA")
    t.remember_fact("refund_window", "90", "Source: refunds are allowed for 90 days after delivery.", agent(tenant, "human"), id="edited", origin="feedB")
    assert stage(tenant)["gate"] == "BLOCKED"


def test_two_pages_on_one_site_are_one_publisher(tenant):
    from app.models.common import NodeKind, SourceKind, Status
    from app.models.graph import GraphNode, SourceInfo

    store = queries.get_graph_store(tenant)
    for sid, url in (("srcA", "https://feeds.example.com/a"), ("srcB", "https://feeds.example.com/b")):
        store.add_node(GraphNode(id=sid, kind=NodeKind.SOURCE, label=sid, status=Status.HEALTHY, source=SourceInfo(kind=SourceKind.DOC, title=sid, url=url, version="1")))
    t.remember_fact("refund_window", "90", "Partner says refunds last 90 days.", agent(tenant), id="leaf", origin="a")
    t.remember_fact("refund_window", "90", "The second page agrees on ninety days.", agent(tenant, "human"), id="other", origin="b")
    store.nodes["leaf"].source_ref = "srcA"
    store.nodes["other"].source_ref = "srcB"
    assert not quorum_gate.independent(store.nodes["leaf"], store.nodes["other"], store)


def test_copied_from_is_validated_like_any_id(tenant):
    for bad in ("a b", "x" * 200, "n'}) DETACH DELETE n //"):
        r = t.remember_fact("k", "v", "A normal sentence about refunds here.", agent(tenant), copied_from=bad)
        assert r["success"] is False, bad
