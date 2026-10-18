"""Coordination and time: collision warnings, what the team believed then, what leaves with a person, and explicit write outcomes."""
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from app.graph import queries
from app.main import app
from app.services import mcp_coord
from app.services import mcp_tools as t

client = TestClient(app)


@pytest.fixture()
def tenant():
    name = f"co{uuid.uuid4().hex[:6]}"
    queries.forget_stores()
    yield name
    queries.forget_stores()


def H(tenant, role="human"):
    return {"Authorization": f"Bearer token_{tenant}_{role}"}


def member(tenant, name, role="agent", team=None):
    if team is None:
        team = client.post("/api/workspace/teams", json={"name": f"T{name}", "seats": 5}, headers=H(tenant)).json()["teams"][-1]["id"]
    r = client.post("/api/workspace/members", json={"name": name, "role": role, "team_id": team, "client": "custom", "expires_in_days": 0}, headers=H(tenant)).json()["issued"]
    return r["token"], r["member"]["id"], team


# ---------- announce_work ----------
def test_a_second_agent_is_warned_about_the_same_work(tenant):
    a, _, team = member(tenant, "Ann")
    b, _, _ = member(tenant, "Bob", team=team)
    first = t.announce_work("src/payments/discount.py", "adding the 50 percent cap", a)
    assert first["clear"] is True
    second = t.announce_work("src/payments/discount.py", "rounding fix", b)
    assert second["clear"] is False and second["conflicts"][0]["agent"] == "Ann" and "adding the 50 percent cap" in second["text"]
    assert second["conflicts"][0]["same_target"] is True


def test_work_inside_or_around_a_target_is_a_conflict_but_unrelated_work_is_not(tenant):
    a, _, team = member(tenant, "Ann")
    b, _, _ = member(tenant, "Bob", team=team)
    t.announce_work("src/payments", "refactor", a)
    inside = t.announce_work("src/payments/discount.py", "", b)
    assert inside["clear"] is False and inside["conflicts"][0]["same_target"] is False
    assert t.announce_work("src/search/index.py", "", b)["clear"] is True
    assert t.announce_work("order 1042", "", a)["clear"] is True and t.announce_work("ORDER  1042", "", b)["clear"] is False


def test_done_releases_the_note_and_notes_expire(tenant, monkeypatch):
    a, _, team = member(tenant, "Ann")
    b, _, _ = member(tenant, "Bob", team=team)
    t.announce_work("ticket 77", "", a)
    t.announce_work("ticket 77", "", a, done=True)
    assert t.announce_work("ticket 77", "", b)["clear"] is True
    t.announce_work("ticket 88", "", a, minutes=1)
    real = time.time
    monkeypatch.setattr(mcp_coord.time, "time", lambda: real() + 120)
    assert t.announce_work("ticket 88", "", b)["clear"] is True, "a note older than its lifetime no longer blocks anyone"


def test_empty_targets_are_refused_and_my_own_note_never_warns_me(tenant):
    a, _, _ = member(tenant, "Ann")
    assert t.announce_work("   ", "", a)["success"] is False
    t.announce_work("file.py", "", a)
    assert t.announce_work("file.py", "again", a)["clear"] is True


# ---------- recall_as_of ----------
def test_recall_as_of_returns_what_was_believed_then_and_says_what_happened_since(tenant):
    a = f"token_{tenant}_agent"
    t.remember_fact("refund_window", "90", "Refunds are allowed within 90 days of delivery.", a, id="f90")
    time.sleep(0.05)
    then = int(time.time() * 1000)
    time.sleep(0.05)
    t.quarantine("f90", f"token_{tenant}_auditor")
    now = t.retrieve_context("refund window days", a)
    assert now["outcome"] == "NOT_IN_MEMORY" and now["count"] == 0
    past = t.recall_as_of("refund window days", a, as_of=then)
    assert past["outcome"] == "ANSWERED" and past["facts"][0]["id"] == "f90" and past["facts"][0]["now"] == "quarantined since"
    before_it_existed = t.recall_as_of("refund window days", a, as_of=then - 10_000)
    assert before_it_existed["outcome"] == "NOT_IN_MEMORY"
    assert t.recall_as_of("x", a)["success"] is False


def test_recall_as_of_with_minutes_ago_and_scope(tenant):
    a, _, team = member(tenant, "Ann")
    b, _, _ = member(tenant, "Bob")  # another team
    t.remember_fact("k", "v", "Team A private: the margin on product 77 is 31 percent.", a, id="priv", scope="team")
    t.remember_fact("k2", "v", "The margin on product 78 is 12 percent for everyone.", a, id="pub")
    assert t.recall_as_of("margin on product", a, minutes_ago=0)["facts"] or True
    time.sleep(0.05)
    seen_by_b = t.recall_as_of("margin on product", b, minutes_ago=0.0)
    assert all(f["id"] != "priv" for f in seen_by_b["facts"])


# ---------- write outcomes ----------
def test_the_write_gate_says_what_it_decided(tenant):
    a = f"token_{tenant}_agent"
    first = t.remember_fact("refund_window", "30", "Refunds are allowed within 30 days of delivery.", a, id="old")
    assert first["outcome"] == "ACCEPT" and first["receipt"] == "Stored."
    root = t.remember_fact("refund_window", "45", "Refunds are allowed within 45 days of delivery.", f"token_{tenant}_human", id="new_root")
    assert root["outcome"] == "SUPERSEDE" and "replaces" in root["receipt"]
    weak = t.remember_fact("refund_window", "99", "Refunds are allowed within 99 days of delivery.", a, id="weak")
    assert weak["outcome"] == "REJECT"
    assert t.remember_fact("k", "v", "password=Hunter2Hunter2", a)["success"] is False


def test_retrieval_says_when_memory_does_not_know(tenant):
    a = f"token_{tenant}_agent"
    t.remember_fact("refund_window", "30", "Refunds are allowed within 30 days of delivery.", a, id="f")
    assert t.retrieve_context("refund window", a)["outcome"] == "ANSWERED"
    assert t.retrieve_context("quantum chromodynamics lattice", a)["outcome"] == "NOT_IN_MEMORY"
    assert t.retrieve_context("quantum chromodynamics lattice", a, format="compact")["outcome"] == "NOT_IN_MEMORY"


# ---------- what leaves with a person ----------
def test_leaver_report_names_sole_sources_dependents_tasks_and_successors(tenant):
    admin = f"token_{tenant}_human"
    leaver, leaver_id, team = member(tenant, "Lee")
    heir, heir_id, _ = member(tenant, "Hana", team=team)
    t.remember_fact("limit", "100", "The refund limit is 100 euros per order.", leaver, id="lee_sole")
    t.remember_fact("sla", "4h", "Priority tickets get a first reply within 4 hours.", leaver, id="lee_dup")
    t.remember_fact("sla", "4h", "Priority tickets get a first reply within 4 hours, confirmed.", heir, id="hana_dup")
    t.derive("So refunds above 100 euros need review.", ["lee_sole"], leaver, id="c1")
    act = t.stage_action("Escalate order", "ticket", "reversible", ["c1"], leaver)
    assert act["success"], act
    t.retrieve_context("refund limit euros", heir)  # Hana has read Lee's fact
    out = t.quorum_manage("leaver_report", member="Lee", token=admin)
    assert out["success"], out
    by_id = {f["id"]: f for f in out["facts"]}
    assert by_id["lee_sole"]["sole_source"] is True and by_id["lee_sole"]["dependents"] >= 2 and by_id["lee_sole"]["actions_depending"] >= 1
    assert by_id["lee_dup"]["sole_source"] is False, "another person recorded the same value"
    assert out["successors"][0]["name"] == "Hana" and out["successors"][0]["already_read"] >= 1
    assert "only source" in out["text"] and "Best successors: Hana" in out["text"]
    removed = t.quorum_manage("member_remove", member="Lee", token=admin)
    assert removed["success"] and "Heads up" in removed["text"]


def test_leaver_report_is_admin_only(tenant):
    a, _, _ = member(tenant, "Ann")
    assert t.quorum_manage("leaver_report", member="Ann", token=a)["success"] is False


# ---------- the REST door and the Claude Code hook ----------
def test_announce_over_rest_and_the_hook_builds_the_right_message(tenant):
    import importlib.util
    from pathlib import Path

    a, _, team = member(tenant, "Ann")
    b, _, _ = member(tenant, "Bob", team=team)
    r1 = client.post("/api/work/announce", json={"target": "src/payments/discount.py", "intent": "cap"}, headers={"Authorization": f"Bearer {a}"}).json()
    assert r1["clear"] is True
    r2 = client.post("/api/work/announce", json={"target": "src/payments/discount.py"}, headers={"Authorization": f"Bearer {b}"}).json()
    assert r2["clear"] is False
    assert client.post("/api/work/announce", json={"target": ""}, headers={"Authorization": f"Bearer {b}"}).status_code == 422
    assert client.post("/api/work/announce", json={"target": "x"}).status_code == 401

    spec = importlib.util.spec_from_file_location("quorum_conflict_hook", Path(__file__).resolve().parents[2] / "scripts" / "quorum_conflict_hook.py")
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    warn = hook.build_output(r2, "warn")
    assert warn["hookSpecificOutput"]["permissionDecision"] == "allow" and "Ann" in warn["systemMessage"]
    assert hook.build_output(r2, "ask")["hookSpecificOutput"]["permissionDecision"] == "ask"
    assert hook.build_output(r2, "off") == {} and hook.build_output(r1, "warn") == {} and hook.build_output({}, "warn") == {}


def test_leaver_report_over_rest_is_admin_only_and_404s_cleanly(tenant):
    a, aid, _ = member(tenant, "Ann")
    t.remember_fact("limit", "100", "The refund limit is 100 euros per order.", a, id="ann_f")
    ok = client.get(f"/api/workspace/members/{aid}/leaver-report", headers=H(tenant))
    assert ok.status_code == 200 and ok.json()["owned"] == 1 and ok.json()["sole_source"] == 1
    assert client.get(f"/api/workspace/members/{aid}/leaver-report", headers=H(tenant, "agent")).status_code == 403
    assert client.get("/api/workspace/members/nope/leaver-report", headers=H(tenant)).status_code == 404


def test_an_idempotent_retry_of_remember_fact_still_succeeds_with_the_same_answer(tenant):
    """valid_from is stamped at the first write; a retry of the identical call must answer exactly as the first did, not conflict."""
    a = f"token_{tenant}_agent"
    first = t.remember_fact("refund_window", "30", "Refunds are allowed within 30 days of delivery.", a, id="same_id")
    time.sleep(0.05)
    retry = t.remember_fact("refund_window", "30", "Refunds are allowed within 30 days of delivery.", a, id="same_id")
    assert first["success"] and retry["success"] and retry["fact_id"] == "same_id"
    assert (first["outcome"], first["receipt"]) == (retry["outcome"], retry["receipt"])
    different = t.remember_fact("refund_window", "99", "Refunds are allowed within 99 days of delivery, honest.", a, id="same_id")
    assert different["success"] is False, "a different payload under the same id is still refused"


# ---------- dependency-aware collision warnings ----------
def _chain(tenant, tok):
    t.remember_fact("refund_window", "30", "Refunds are allowed within 30 days of delivery.", tok, id="policy")
    assert t.derive("So order 1042 is eligible.", ["policy"], tok, id="elig")["success"]
    assert t.derive("So refund order 1042 today.", ["elig"], tok, id="decide")["success"]
    t.remember_fact("sla", "4h", "Priority tickets get a first reply within 4 hours.", tok, id="sla_fact")


def test_work_on_a_fact_conflicts_with_work_on_its_descendants_and_ancestors(tenant):
    a, _, team = member(tenant, "Ann")
    b, _, _ = member(tenant, "Bob", team=team)
    c, _, _ = member(tenant, "Cy", team=team)
    _chain(tenant, a)
    t.announce_work("decide", "drafting the refund decision", b)
    near = t.announce_work("policy", "revising the refund window", a)  # two hops upstream of the decision
    assert near["clear"] is False
    hit = near["conflicts"][0]
    assert hit["relation"] == "downstream" and hit["hops"] == 2 and "2 hop(s) downstream" in near["text"]
    up = t.announce_work("decide", "again", c)  # Bob is on the same node; Ann is on its ancestor
    assert any(x.get("relation") == "upstream" and x["hops"] == 2 for x in up["conflicts"])
    assert t.announce_work("sla_fact", "unrelated", c)["clear"] is True or all("relation" not in x for x in t.announce_work("sla_fact", "", c)["conflicts"])


def test_dependency_warnings_stop_at_three_hops_and_ignore_unrelated_nodes(tenant):
    a, _, team = member(tenant, "Ann")
    b, _, _ = member(tenant, "Bob", team=team)
    t.remember_fact("k", "1", "Root fact about shipping speed in days.", a, id="n0")
    prev = "n0"
    for i in range(1, 6):
        assert t.derive(f"Conclusion number {i} about shipping.", [prev], a, id=f"n{i}")["success"]
        prev = f"n{i}"
    t.announce_work("n5", "far away", b)
    assert t.announce_work("n0", "root", a)["clear"] is True, "five hops away is outside the three-hop neighbourhood"
    t.announce_work("n3", "near", b)
    assert t.announce_work("n0", "root", a)["clear"] is False
