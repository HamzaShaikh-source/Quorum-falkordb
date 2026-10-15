"""The token-saving context builder: ranking, budget, scope, map, node, path, delta, and parity with the TypeScript twin."""
import json
from pathlib import Path


from app.graph.queries import GraphStore
from app.models.common import EdgeType, NodeKind, Status
from app.models.graph import GraphEdge, GraphNode
from app.services import context_pack as cp

PARITY = Path(__file__).parent / "fixtures" / "token_parity.json"


def _n(i, kind, label, text=None, status=Status.HEALTHY, scope=None, vf=0, key=None, value=None):
    return GraphNode(id=i, kind=kind, label=label, text=text, status=status, stale=False, scope_team=scope, valid_from=vf, key=key, value=value)


def _store(n=30):
    s = GraphStore("t")
    topics = [("Refund policy", "refund window"), ("Shipping SLA", "delivery window"), ("Warranty terms", "warranty length")]
    k = 0
    for ti, (doc, fact) in enumerate(topics):
        s.add_node(_n(f"s{ti}", NodeKind.SOURCE, f"{doc} v1", f"Official {doc.lower()}", vf=ti))
        for j in range(n // 3):
            fid = f"f{ti}_{j}"
            s.add_node(_n(fid, NodeKind.FACT, f"{fact} {j}: {10 + j} days", f"The {fact} is {10 + j} days for region R{j}, confirmed in a ticket.", vf=100 + k, key=fact.replace(" ", "_"), value=str(10 + j)))
            s.add_edge(GraphEdge(id=f"e{k}", source=f"s{ti}", target=fid, type=EdgeType.FROM_SOURCE))
            k += 1
        s.add_node(_n(f"c{ti}", NodeKind.CONCLUSION, f"Standard {fact} rule", f"Agents decided the standard {fact} rule follows.", vf=500 + ti))
        s.add_edge(GraphEdge(id=f"ec{ti}", source=f"f{ti}_0", target=f"c{ti}", type=EdgeType.LEADS_TO))
    return s


def test_tokens_match_the_typescript_estimator():
    cases = json.loads(PARITY.read_text(encoding="utf-8"))
    assert len(cases) >= 20
    for c in cases:
        assert cp.estimate_tokens(c["text"]) == c["tokens"], c["text"][:60]


def test_pack_is_ranked_and_respects_the_budget():
    s = _store(60)
    p = cp.pack(s, "warranty length", budget=120)
    assert p["tokens"] <= 150
    assert p["truncated"] is True
    assert "warranty" in p["text"].lower()
    big = cp.pack(s, "warranty length", budget=1500)
    assert big["tokens"] > p["tokens"]


def test_pack_never_returns_quarantined_nodes():
    s = _store(30)
    s.cascade_quarantine("f0_0")
    p = cp.pack(s, "refund window", budget=800)
    assert "f0_0" not in p["ids"] and "c0" not in p["ids"]
    assert all(s.nodes[i].status == Status.HEALTHY for i in p["ids"])


def test_pack_includes_provenance():
    s = _store(30)
    p = cp.pack(s, "refund window", budget=800)
    assert any(i.startswith("s") for i in p["ids"])
    assert "<S" in p["text"]  # a parent reference, e.g. F2|...|<S1


def test_pack_is_cheaper_than_a_top8_retriever_on_a_real_sized_graph():
    s = _store(150)
    p = cp.pack(s, "refund window", budget=400)
    assert p["tokens"] < p["tokens_baseline"] * 0.7


def test_short_ids_are_stable_when_nodes_are_added_later():
    s = _store(12)
    before = cp.short_ids(s.nodes.values())
    s.add_node(_n("late", NodeKind.FACT, "A later fact", vf=10_000))
    after = cp.short_ids(s.nodes.values())
    assert all(after[i] == before[i] for i in before)


def test_map_is_small_and_names_the_areas():
    s = _store(90)
    m = cp.build_map(s)
    assert "## areas" in m["text"] and "## hubs" in m["text"]
    assert m["tokens"] < m["tokens_baseline"] / 5
    assert "retrieve_context" in m["text"]


def test_node_view_by_short_id_and_full_id():
    s = _store(12)
    ids = cp.short_ids(s.nodes.values())
    by_short = cp.node_view(s, ids["f0_1"])
    by_full = cp.node_view(s, "f0_1")
    assert by_short["success"] and by_short["id"] == by_full["id"] == "f0_1"
    assert "↑" in by_short["text"]
    assert cp.node_view(s, "nope")["success"] is False


def test_find_path_between_a_source_and_a_conclusion():
    s = _store(12)
    r = cp.find_path(s, "s0", "c0")
    assert r["success"] and r["hops"] == 2 and r["path"][0] == "s0" and r["path"][-1] == "c0"
    assert cp.find_path(s, "s0", "c2")["path"] == []


def test_scope_hides_private_facts_from_other_teams_and_shows_them_to_linked_teams():
    s = _store(6)
    s.add_node(_n("priv", NodeKind.FACT, "Private pricing floor is 40", "Support-only pricing floor is 40 percent", scope="team_a", vf=900))
    other = cp.Viewer(agent_id="x", team_id="team_b", scoped=True)
    mine = cp.Viewer(agent_id="y", team_id="team_a", scoped=True)
    linked = cp.Viewer(agent_id="z", team_id="team_b", linked_teams={"team_a"}, scoped=True)
    assert "priv" not in cp.pack(s, "pricing floor", viewer=other)["ids"]
    assert "priv" in cp.pack(s, "pricing floor", viewer=mine)["ids"]
    assert "priv" in cp.pack(s, "pricing floor", viewer=linked)["ids"]
    assert cp.node_view(s, "priv", other)["success"] is False
    assert "priv" not in {i for i in cp.build_map(s, other)["text"].split()}  # not named anywhere in the map


def test_delta_logic_via_pack_have_ids():
    s = _store(12)
    first = cp.pack(s, "refund window", budget=800)
    s.add_node(_n("new1", NodeKind.FACT, "refund window update 99 days", "The refund window update says 99 days", vf=9000))
    d = cp.pack(s, "refund window", budget=800, have_ids=set(first["ids"]))
    assert d["delta"]["added"] >= 1
    assert d["tokens"] < first["tokens"]


def test_compare_reports_every_baseline():
    s = _store(60)
    c = cp.compare(s, "refund window", 400)
    assert c["raw_json_tokens"] > c["prose_tokens"] > c["top8_tokens"] > 0
    assert c["packed_tokens"] < c["top8_tokens"]
    assert [x["label"] for x in c["stages"]][0] == "Raw graph JSON"


def _bench_store():
    """Same shape as scripts/token_bench.mjs: 8 topics, a unique regional fact each, plus distractor exceptions."""
    topics = [("refund window", "refunds"), ("shipping time", "shipments"), ("warranty length", "claims"), ("data retention period", "exports")]
    s = GraphStore("bench")
    targets = {}
    k = 0
    for ti, (label, thing) in enumerate(topics):
        ids = []
        for region in range(5):
            days = 20 + ti * 7 + region
            fid = f"r_{ti}_{region}"
            s.add_node(_n(fid, NodeKind.FACT, f"{label} {thing} R{region}: {days} days", f"The {label} for {thing} in region R{region} is {days} days. Confirmed by the finance team.", vf=k, key=f"{label}_R{region}", value=str(days)))
            targets[(ti, region)] = days
            ids.append(fid)
            k += 1
        for j in range(15):
            fid = f"x_{ti}_{j}"
            s.add_node(_n(fid, NodeKind.FACT, f"{label} exception C{j}", f"Customer C{j} has an exception to the {label} for {thing}: {70 + j} days, ticket {3000 + k}.", vf=k, key=f"{label}_ex{j}", value=str(70 + j)))
            ids.append(fid)
            k += 1
        s.add_node(_n(f"c_{ti}", NodeKind.CONCLUSION, f"Standard {label} rule", f"Standard {label} rule for {thing}: follow the regional figure.", vf=1000 + ti))
        for pid in ids[:3]:
            s.add_edge(GraphEdge(id=f"e_{ti}_{pid}", source=pid, target=f"c_{ti}", type=EdgeType.LEADS_TO))
    return s, topics, targets


def test_compact_context_finds_the_right_fact_within_budget_for_region_specific_questions():
    s, topics, targets = _bench_store()
    hits = 0
    for (ti, region), days in targets.items():
        label, thing = topics[ti]
        p = cp.pack(s, f"What is the {label} for {thing} in region R{region}? (answer in days)", budget=400)
        assert p["tokens"] <= 430
        hits += f"{days} days" in p["text"]
    assert hits == len(targets), f"only {hits}/{len(targets)} questions had their answer in the compact context"


def test_full_format_ranks_the_right_fact_inside_the_top_eight():
    from app.services import mcp_tools as t

    s, topics, targets = _bench_store()
    from app.graph import queries

    queries.forget_stores()
    store = queries.get_graph_store("bench")
    store.nodes.update(s.nodes)
    store.edges.extend(s.edges)
    hits = 0
    for (ti, region), days in targets.items():
        label, thing = topics[ti]
        out = t.retrieve_context(f"What is the {label} for {thing} in region R{region}? (answer in days)", "token_bench_agent", k=8)
        hits += any(c["value"] == str(days) for c in out["context"])
    queries.forget_stores()
    assert hits == len(targets)
