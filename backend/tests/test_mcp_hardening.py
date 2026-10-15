"""Header auth on /mcp, rate limiting, semantic ranking, passive fact capture, the backend tool catalog, the audit feed,
and agent read-state / vectors that survive a restart."""
import time
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import db, ratelimit
from app.config import settings
from app.graph import queries
from app.main import app
from app.models.common import NodeKind, Status
from app.models.graph import GraphNode
from app.services import context_pack as cp
from app.services import embeddings, mcp_tools as t, observer

client = TestClient(app)


@pytest.fixture()
def tenant():
    name = f"hd{uuid.uuid4().hex[:6]}"
    queries.forget_stores()
    yield name
    queries.forget_stores()


def H(tenant, role="human"):
    return {"Authorization": f"Bearer token_{tenant}_{role}"}


def fact(i, text, key=None, value=None):
    return GraphNode(id=i, kind=NodeKind.FACT, label=text[:60], status=Status.HEALTHY, stale=False, key=key, value=value, text=text)


# ---------- header auth and rate limits ----------
def test_mcp_rejects_a_bad_header_token_before_any_tool_runs():
    r = client.post("/mcp/", json={}, headers={"Authorization": "Bearer nonsense"})
    assert r.status_code == 401 and r.headers.get("www-authenticate") == "Bearer"


def test_mcp_header_can_be_made_mandatory(monkeypatch):
    monkeypatch.setattr(settings, "MCP_REQUIRE_HEADER", True)
    assert client.post("/mcp/", json={}).status_code == 401
    ok = client.post("/mcp/", json={}, headers={"Authorization": "Bearer token_human"})
    assert ok.status_code != 401  # past the guard (the route itself may 404 when MCP is not embedded in this test app)


def test_x_quorum_token_header_works_too(monkeypatch):
    monkeypatch.setattr(settings, "MCP_REQUIRE_HEADER", True)
    assert client.post("/mcp/", json={}, headers={"X-Quorum-Token": "token_human"}).status_code != 401


def test_http_rate_limit_answers_429_with_retry_after(monkeypatch, tenant):
    lim = ratelimit.RateLimiter(60, burst=3)
    monkeypatch.setattr("app.main.http_limiter", lim)
    codes = [client.get("/api/token-stats", headers=H(tenant)).status_code for _ in range(6)]
    assert codes[:3] == [200, 200, 200] and 429 in codes[3:]
    r = client.get("/api/token-stats", headers=H(tenant))
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1 and r.json()["error"]["code"] == "RATE_LIMITED"


def test_health_is_never_rate_limited(monkeypatch):
    monkeypatch.setattr("app.main.http_limiter", ratelimit.RateLimiter(60, burst=1))
    assert all(client.get("/api/health").status_code == 200 for _ in range(5))


def test_tool_rate_limit_is_per_token(monkeypatch, tenant):
    monkeypatch.setattr(ratelimit, "tool_limiter", ratelimit.RateLimiter(60, burst=2))
    g = t.guarded(t.get_map)
    tok = f"token_{tenant}_agent"
    outs = [g(token=tok) for _ in range(4)]
    assert [bool(o.get("rate_limited")) for o in outs] == [False, False, True, True]
    assert "Retry in" in outs[2]["error"]
    # another caller is not affected
    assert not g(token=f"token_{tenant}_auditor").get("rate_limited")


def test_limiter_refills():
    lim = ratelimit.RateLimiter(600, burst=1)  # ten per second
    assert lim.allow("a")[0] and not lim.allow("a")[0]
    time.sleep(0.25)
    assert lim.allow("a")[0]


def ctx_with(headers):
    return SimpleNamespace(request_context=SimpleNamespace(request=SimpleNamespace(headers=headers)))


def test_header_token_wins_and_the_token_argument_becomes_optional(tenant):
    g = t.guarded(t.get_map)
    out = g(ctx=ctx_with({"authorization": f"Bearer token_{tenant}_agent"}))  # no token argument at all
    assert "map" in out and out.get("success") is not False
    # a bad header is not rescued by a good argument: the connection's own proof is what counts
    bad = g(token=f"token_{tenant}_agent", ctx=ctx_with({"authorization": "Bearer wrong"}))
    assert bad.get("success") is False and "token" in bad["error"].lower()
    # and without either, the error says how to fix it
    none = g()
    assert none["success"] is False and "Authorization" in none["error"]


def test_stdio_default_token_comes_from_the_environment(monkeypatch, tenant):
    monkeypatch.setenv("QUORUM_MCP_TOKEN", f"token_{tenant}_agent")
    assert "map" in t.guarded(t.get_map)()


def test_guarded_signature_exposes_ctx_for_mcp_but_keeps_token_optional():
    import inspect

    sig = inspect.signature(t.guarded(t.retrieve_context))
    assert "ctx" in sig.parameters and sig.parameters["token"].default == ""


# ---------- semantic ranking ----------
CORPUS = [
    ("refund_eu", "Customers in the EU can request a refund within 21 days of delivery."),
    ("shipping_us", "Standard shipping to the US takes 5 business days."),
    ("password", "Passwords must be rotated every 90 days and never shared."),
    ("cancel", "Cancelling a contract in the first month carries no fee."),
    ("leave", "Employees receive 25 days of annual leave plus public holidays."),
    ("sev1", "A critical outage must be escalated to the on-call manager within 15 minutes."),
    ("backup", "Database snapshots are taken nightly and kept for 30 days."),
    ("vat_de", "German orders include 19 percent VAT at checkout."),
]


def corpus_nodes():
    return [fact(f"n_{k}", txt, key=k, value="x") for k, txt in CORPUS]


@pytest.mark.parametrize(
    "question,expected",
    [
        ("how long can shoppers get their money back in Europe", "n_refund_eu"),
        ("how often do I need to change my credentials", "n_password"),
        ("how much vacation do staff get", "n_leave"),
        ("who do I hand off a severe incident to", "n_sev1"),
        ("how long do we keep database copies", "n_backup"),
        ("german sales tax rate", "n_vat_de"),
    ],
)
def test_paraphrases_find_the_right_fact(question, expected):
    nodes = corpus_nodes()
    ranked = cp.rank(nodes, question, cp.idf_weights(nodes))
    assert ranked[0][0].id == expected and ranked[0][1] > 0


def test_keyword_only_would_have_missed_those():
    nodes = corpus_nodes()
    idf = cp.idf_weights(nodes)
    miss = 0
    for q, exp in [("how often do I need to change my credentials", "n_password"), ("how much vacation do staff get", "n_leave"), ("how long do we keep database copies", "n_backup")]:
        scores = {n.id: cp._relevance(n, set(cp._words(q)), idf) for n in nodes}
        miss += scores[exp] < max(scores.values()) or scores[exp] == 0
    assert miss >= 2


def test_unrelated_questions_return_nothing():
    nodes = corpus_nodes()
    idf = cp.idf_weights(nodes)
    for q in ("what is the weather like on mars", "who won the football world cup in 1998"):
        assert all(s == 0 for _, s, _ in cp.rank(nodes, q, idf))


def test_exact_keyword_matches_still_lead():
    nodes = corpus_nodes()
    r = cp.rank(nodes, "VAT at checkout in Germany", cp.idf_weights(nodes))
    assert r[0][0].id == "n_vat_de"


def test_local_vectors_are_deterministic_and_unit_length():
    import numpy as np

    a, b = embeddings.local_vector("refund within 21 days"), embeddings.local_vector("refund within 21 days")
    assert (a == b).all() and abs(float(np.linalg.norm(a)) - 1.0) < 1e-5
    assert float(a @ embeddings.local_vector("money back in three weeks")) > float(a @ embeddings.local_vector("database snapshot retention"))


def test_neural_provider_is_used_when_configured_and_degrades_when_it_fails(monkeypatch):
    import numpy as np

    calls = []

    class Fake:
        def embed(self, texts):
            calls.append(list(texts))
            return [np.eye(8, dtype=np.float32)[hash(x) % 8] for x in texts]

    monkeypatch.setattr(settings, "EMBED_PROVIDER", "openai")
    monkeypatch.setattr(settings, "EMBED_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setattr(embeddings, "_openai", Fake())
    monkeypatch.setattr(embeddings, "_cache", {})
    scores = embeddings.cosine_scores("anything", [fact("z1", "alpha beta")])
    assert calls and "z1" in scores

    class Down:
        def embed(self, texts):
            raise ConnectionError("down")

    monkeypatch.setattr(embeddings, "_openai", Down())
    monkeypatch.setattr(embeddings, "_cache", {})
    monkeypatch.setattr(embeddings, "_provider_failed_at", 0.0)
    out = embeddings.cosine_scores("refund policy", [fact("z2", "refund policy for customers")])
    assert out["z2"] > 0.3  # fell back to local vectors rather than failing the retrieval


def test_retrieve_context_uses_the_hybrid_ranking(tenant):
    store = queries.get_graph_store(tenant)
    for n in corpus_nodes():
        store.add_node(n)
    tok = f"token_{tenant}_agent"
    for fmt in ("full", "compact"):
        out = t.retrieve_context("how much vacation do staff get", tok, format=fmt)
        text = str(out.get("context") or out.get("packed"))
        assert "annual leave" in text


# ---------- passive fact capture ----------
TRANSCRIPT = """
Sure, let me check that for you.
The refund window in Chile is 21 days from delivery.
Maybe we could extend it? I think that is up to finance.
Support tickets from Norway must be answered within 4 hours.
```python
x = 42 days
```
Do you want me to open a ticket?
The production API key rotates every 90 days and is never shared outside the platform team.
Thanks!
"""


def test_extraction_keeps_facts_and_drops_chatter():
    c = observer.candidates(TRANSCRIPT)
    texts = [x["text"] for x in c]
    assert any("refund window in Chile" in x for x in texts)
    assert any("Norway" in x for x in texts) and any("90 days" in x for x in texts)
    assert not any("Maybe" in x or "Do you want" in x or "let me check" in x or "x = 42" in x for x in texts)
    assert all(x["confidence"] >= 0.75 for x in c)
    chile = next(x for x in c if "Chile" in x["text"])
    assert chile["value"] == "21 days" and chile["key"]


def test_observe_stores_facts_with_their_source_sentence(tenant):
    out = t.guarded(t.observe)(TRANSCRIPT, f"token_{tenant}_agent", "claude-code")
    assert out["success"] and out["stored"] >= 3
    store = queries.get_graph_store(tenant)
    chile = next(n for n in store.nodes.values() if "Chile" in (n.text or ""))
    assert chile.quote == chile.text and chile.origin == "observer:claude-code" and chile.status == Status.HEALTHY


def test_observe_is_idempotent_and_flags_a_conflicting_number(tenant):
    tok = f"token_{tenant}_agent"
    first = t.observe(TRANSCRIPT, tok)
    again = t.observe(TRANSCRIPT, tok)
    assert first["stored"] >= 3 and again["stored"] == 0 and len(again["duplicates"]) == first["stored"]
    changed = t.observe("The refund window in Chile is 45 days from delivery.", tok)
    assert changed["stored"] == 1 and changed["conflicts"], "a different number for the same thing must not silently replace the old one"


def test_observe_dry_run_writes_nothing(tenant):
    store = queries.get_graph_store(tenant)
    before = len(store.nodes)
    out = t.observe(TRANSCRIPT, f"token_{tenant}_agent", dry_run=True)
    assert out["candidates"] >= 3 and out["stored"] == 0 and len(store.nodes) == before


def test_observe_endpoint_and_roles(tenant):
    r = client.post("/api/observe", json={"text": TRANSCRIPT, "source": "hook"}, headers=H(tenant, "agent"))
    assert r.status_code == 200 and r.json()["stored"] >= 3
    assert client.post("/api/observe", json={"text": TRANSCRIPT}, headers=H(tenant, "auditor")).status_code == 403
    assert client.post("/api/observe", json={"text": ""}, headers=H(tenant, "agent")).status_code == 422


def test_observed_facts_are_retrievable(tenant):
    tok = f"token_{tenant}_agent"
    t.observe(TRANSCRIPT, tok)
    out = t.retrieve_context("how long do shoppers in Chile have to get their money back", tok, format="compact")
    assert "Chile" in out["packed"]


# ---------- catalog and audit ----------
def test_catalog_is_built_from_the_tools(tenant):
    r = client.get("/api/mcp/catalog", headers=H(tenant)).json()
    names = [x["name"] for x in r["tools"]]
    assert "observe" in names and "retrieve_context" in names and len(names) == len(set(names)) == 15
    rc = next(x for x in r["tools"] if x["name"] == "retrieve_context")
    assert [a["name"] for a in rc["args"]][:2] == ["query", "token"] and rc["args"][0]["required"] and not rc["args"][1]["required"]
    assert "ctx" not in str(r["tools"])
    from app.mcp_embed import TOOLS

    assert names == list(TOOLS)


def test_audit_reads_like_a_log_and_exports_csv(tenant):
    tok = f"token_{tenant}_agent"
    t.guarded(t.remember_fact)("refund_x", "30", "Refund window is 30 days.", tok)
    t.guarded(t.retrieve_context)("refund window", tok, format="compact")
    t.guarded(t.quarantine)("refund_x", f"token_{tenant}_agent", "oops")  # an agent may not: shows up as refused
    r = client.get("/api/audit", headers=H(tenant)).json()
    texts = [e["text"] for e in r["events"]]
    assert any(x.startswith("recorded a fact") for x in texts) and any("looked up" in x for x in texts)
    assert any(e["severity"] == "failed" for e in r["events"])
    assert r["summary"]["events"] == 3 and r["retention"]
    only_failed = client.get("/api/audit?severity=failed", headers=H(tenant)).json()
    assert all(e["severity"] == "failed" for e in only_failed["events"])
    csv_text = client.get("/api/audit?format=csv", headers=H(tenant))
    assert csv_text.headers["content-type"].startswith("text/csv") and csv_text.text.splitlines()[0].startswith("time_utc,who")


def test_audit_csv_neutralises_formulas(tenant):
    t.guarded(t.remember_fact)("k", "v", "=HYPERLINK(\"http://x\")", f"token_{tenant}_agent")
    body = client.get("/api/audit?format=csv", headers=H(tenant)).text
    assert "=HYPERLINK" not in body.replace("'=HYPERLINK", "")


# ---------- survive a restart (needs FalkorDB) ----------
@pytest.fixture()
def falkor(monkeypatch):
    monkeypatch.setattr(settings, "PERSISTENCE", "falkordb")
    db.reset_client()
    db._health["checked_at"] = 0.0
    if not db.check_health(force=True)["up"]:
        pytest.skip("FalkorDB is not running. Start it with: docker compose up -d falkordb")
    name = f"hr{uuid.uuid4().hex[:8]}"
    queries.forget_stores()
    yield name
    try:
        db.graph_for(name).delete()
    except Exception:
        pass
    queries.forget_stores()


def test_what_an_agent_has_read_survives_a_restart(falkor):
    store = queries.get_graph_store(falkor)
    store.add_node(fact("f1", "The refund window in Chile is 21 days.", "refund_chile", "21"))
    tok = f"token_{falkor}_agent"
    t.retrieve_context("refund window Chile", tok, format="compact")
    store.commit()
    queries.forget_stores()
    out = t.delta(tok)
    assert "nothing read yet" not in out["text"], out
    # and an invalidation after the restart is still reported
    again = queries.get_graph_store(falkor)
    from app.services.auditor import auditor_service

    auditor_service.quarantine(again, "f1", tenant=falkor)
    assert "no longer valid" in t.delta(tok)["text"]


def test_vectors_are_stored_in_falkordb_and_its_index_agrees(falkor):
    store = queries.get_graph_store(falkor)
    for n in corpus_nodes():
        store.add_node(n)
    store.commit()
    row = db.graph_for(falkor).query("MATCH (n:QNode {id:'n_leave'}) RETURN n.embedding, n.emb").result_set[0]
    assert row[0] and len(row[0]) == embeddings.DIM and row[1]
    h = {"Authorization": f"Bearer token_{falkor}_human"}
    r = client.get("/api/context/semantic-check", params={"query": "how much vacation do staff get"}, headers=h).json()
    assert r["falkordb_top"] and r["falkordb_top"][0]["id"] == "n_leave" and r["agrees"] is True, r


def test_stored_vectors_are_reused_after_a_restart(falkor, monkeypatch):
    store = queries.get_graph_store(falkor)
    for n in corpus_nodes():
        store.add_node(n)
    store.commit()
    queries.forget_stores()
    embeddings._cache.clear()
    calls = []
    real = embeddings.local_vector
    monkeypatch.setattr(embeddings, "local_vector", lambda x: (calls.append(x), real(x))[1])
    s2 = queries.get_graph_store(falkor)
    embeddings.cosine_scores("vacation", [n for n in s2.nodes.values()])
    assert calls == ["vacation"], "only the question is embedded; the 8 stored vectors were loaded, not recomputed"


def test_the_consoles_builtin_tool_list_is_the_real_catalog():
    """frontend/src/workspace/mcp.ts builds its fallback list from this file. Run scripts/gen_catalog_fixture.py when it fails."""
    import json
    from pathlib import Path

    from app.services.mcp_catalog import catalog

    fx = json.loads((Path(__file__).parent / "fixtures" / "mcp_catalog.json").read_text(encoding="utf8"))
    live = [{k: v for k, v in x.items() if k != "doc"} for x in catalog()]
    assert fx["tools"] == live


def test_sentences_that_look_like_credentials_are_never_kept():
    leaky = (
        "The staging password: hunter2hunter2 must be rotated within 30 days.\n"
        "Use api_key=sk-live-abcdef1234567890 for the vendor, valid for 90 days.\n"
        "Send the header Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123 within 5 minutes.\n"
        "The refund window in Chile is 21 days from delivery."
    )
    texts = [c["text"] for c in observer.candidates(leaky)]
    assert texts == ["The refund window in Chile is 21 days from delivery."]


def test_a_sentence_the_hook_redacted_is_dropped_not_stored():
    assert observer.candidates("My key is [redacted] and it expires in 30 days.") == []


def test_declarative_facts_are_kept_and_narration_is_not():
    text = (
        "1. Northwind employees are entitled to 15 days of paid vacation per calendar year.\n"
        "2. All customer support tickets must receive an initial response within 4 hours.\n"
        "3. Employee expense reports exceeding 500 dollars require manager approval before reimbursement.\n"
        "I ran the test suite and it took 12 seconds with 4 failures.\n"
        "The build completed in 31 seconds after I updated 3 files."
    )
    got = [c["text"] for c in observer.candidates(text)]
    assert len(got) == 3 and all("Northwind" in g or "tickets" in g or "expense" in g for g in got)


def test_audit_sentences_read_naturally(tenant):
    tok = f"token_{tenant}_agent"
    t.guarded(t.observe)("The refund window in Chile is 21 days from delivery.", tok, "claude-code")
    t.guarded(t.quarantine)("nope", tok, "x")
    texts = [e["text"] for e in client.get("/api/audit", headers=H(tenant)).json()["events"]]
    assert any(x.startswith("captured facts from text \u201cThe refund window in Chile") for x in texts)
    assert any(x.startswith("tried to quarantine nope and was refused: ") and "cannot quarantine facts" in x for x in texts)
