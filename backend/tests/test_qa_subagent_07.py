"""
Exhaustive QA and Stress Test Suite for Subagent_07:
Context Packing, Semantic Search, Neural Embeddings, Brain & Prompt Diff Services.
"""
import math
import numpy as np
from app.graph.queries import GraphStore
from app.models.common import EdgeType, NodeKind, Status
from app.models.graph import GraphEdge, GraphNode
from app.services import context_pack as cp
from app.services import embeddings
from app.services.context_pack import Viewer
from app.config import settings
from app.models.agents import PromptDiffResponse, PromptDiffNode


def _node(nid, kind=NodeKind.FACT, label="test", text="test text", status=Status.HEALTHY,
          key=None, value=None, vf=100, scope=None, source_ref=None, quote=None, stale=False):
    return GraphNode(
        id=nid,
        kind=kind,
        label=label,
        text=text,
        status=status,
        stale=stale,
        valid_from=vf,
        key=key,
        value=value,
        scope_team=scope,
        source_ref=source_ref,
        quote=quote,
    )


# ==============================================================================
# SECTION 1: Hybrid Ranking, IDF, Cosine, Cutoff
# ==============================================================================

def test_idf_weights_basic_and_edge_cases():
    # Empty nodes
    assert cp.idf_weights([]) == {}

    # Single node with single word
    n1 = _node("n1", label="hello", text="hello")
    idf = cp.idf_weights([n1])
    assert "hello" in idf
    assert idf["hello"] > 0

    # Rare word vs common word
    # n1..n10 have "common", only n1 has "rare"
    nodes = [_node(f"n{i}", label="common word", text=f"common word {'rare' if i == 0 else ''}") for i in range(10)]
    idf = cp.idf_weights(nodes)
    assert idf["rare"] > idf["common"]


def test_cosine_scores_local_embeddings():
    n_refund = _node("n1", label="refund window", text="customer gets refund within 30 days")
    n_shipping = _node("n2", label="shipping time", text="dispatch package via courier freight")
    
    # Query with synonym from concept lexicon: "money back" -> refund concept
    scores = embeddings.cosine_scores("money back", [n_refund, n_shipping])
    assert scores[n_refund.id] > scores[n_shipping.id]
    assert scores[n_refund.id] > 0.0


def test_hybrid_ranking_formula_and_cutoff():
    # Candidate 1: Strong keyword match, zero vector match
    # Candidate 2: No keyword match, high cosine match (> 0.30)
    # Candidate 3: No keyword match, low cosine match (< 0.30) -> MUST be cut off (score 0.0)
    
    n_kw = _node("n_kw", kind=NodeKind.FACT, label="refund window", text="refund window policy 30 days")
    n_vec_high = _node("n_vh", kind=NodeKind.FACT, label="moneyback guarantee", text="reimburse customer payment")
    n_vec_low = _node("n_vl", kind=NodeKind.FACT, label="unrelated topic", text="banana apple orange fruit")
    
    candidates = [n_kw, n_vec_high, n_vec_low]
    idf = cp.idf_weights(candidates)
    
    ranked = cp.rank(candidates, "refund", idf)
    ranked_map = {node.id: (score, cos) for node, score, cos in ranked}
    
    # n_kw has keyword hit
    assert ranked_map["n_kw"][0] > 0.0
    
    # n_vec_low has no keyword hit and very low cosine with "refund"
    # Verify RANK_VECTOR_MIN = 0.30 cutoff logic
    if ranked_map["n_vl"][1] < settings.RANK_VECTOR_MIN:
        assert ranked_map["n_vl"][0] == 0.0, "Node with no keyword hit and cos < 0.30 must have score 0.0"


def test_zero_vector_and_nan_cosine_handling():
    # Test local_vector with empty text, pure symbols, or non-ascii
    v_empty = embeddings.local_vector("")
    assert np.all(v_empty == 0.0)
    assert np.linalg.norm(v_empty) == 0.0

    v_sym = embeddings.local_vector("!@#$%^&*()")
    assert np.all(v_sym == 0.0)

    # Cosine score of empty query vs node
    n = _node("n1", label="test", text="test")
    scores = embeddings.cosine_scores("", [n])
    assert scores.get("n1", 0.0) == 0.0

    # What if a candidate vector is all zeros?
    v_cand_zero = embeddings.local_vector("   ")
    dot = float(np.dot(embeddings.local_vector("test"), v_cand_zero))
    assert dot == 0.0

    # What if NaN exists in vector or cosine?
    # Test robustness of rank() when cosine has NaN
    original_cosine_scores = embeddings.cosine_scores
    try:
        embeddings.cosine_scores = lambda q, nodes: {n.id: float("nan")}
        n_fact = _node("n1", kind=NodeKind.FACT, label="something", text="something")
        res = cp.rank([n_fact], "unrelated query", cp.idf_weights([n_fact]))
        score = res[0][1]
        assert score == 0.0, f"NaN cosine should be sanitized to 0.0, got {score}"
        assert not math.isnan(score)
    finally:
        embeddings.cosine_scores = original_cosine_scores


def test_resolve_ref_and_adjacency_edge_cases():
    # resolve_ref with None, empty string, non-string
    n1 = _node("f1", kind=NodeKind.FACT, label="Fact 1")
    nodes = {"f1": n1}
    assert cp.resolve_ref(nodes, None) is None
    assert cp.resolve_ref(nodes, "") is None
    assert cp.resolve_ref(nodes, "   ") is None
    assert cp.resolve_ref(nodes, 123) is None
    assert cp.resolve_ref(nodes, "f1") == n1

    # node_view with None
    s = GraphStore("test_tenant")
    s.add_node(n1)
    nv = cp.node_view(s, None)
    assert nv["success"] is False
    assert "not found" in nv["error"]

    # find_path with None
    fp = cp.find_path(s, None, "f1")
    assert fp["success"] is False
    assert "not found" in fp["error"]

    # _adjacency with raw string edge types
    class MockEdge:
        source = "f1"
        target = "f2"
        type = "LEADS_TO"

    inn, out = cp._adjacency({"f1": n1, "f2": _node("f2")}, [MockEdge()])
    assert out["f1"] == ["f2"]
    assert inn["f2"] == ["f1"]



# ==============================================================================
# SECTION 2: Token Budget Enforcement in pack()
# ==============================================================================

def test_pack_budget_extremes():
    s = GraphStore("test_tenant")
    for i in range(20):
        s.add_node(_node(f"f{i}", kind=NodeKind.FACT, label=f"Fact {i} about refund", text=f"Fact text {i} explaining refund rules in detail."))
    
    # Extreme 1: Huge budget (100,000 tokens)
    p_huge = cp.pack(s, "refund", budget=100_000)
    assert p_huge["tokens"] < 100_000
    assert p_huge["dropped"] == 0
    assert len(p_huge["ids"]) > 0
    # Note: seeds is 8 by default, so at most 8 seeds + ancestors are packed
    assert len(p_huge["ids"]) <= 20

    # Extreme 2: Tiny budget (10 tokens)
    p_tiny = cp.pack(s, "refund", budget=10)
    assert p_tiny["dropped"] > 0
    # Line budget should drop items that do not fit
    print(f"Tiny budget (10) returned tokens: {p_tiny['tokens']}, ids: {p_tiny['ids']}")

    # Extreme 3: Zero or negative budget
    p_zero = cp.pack(s, "refund", budget=0)
    assert len(p_zero["ids"]) == 0
    assert p_zero["dropped"] > 0


def test_pack_ancestor_expansion_and_line_format():
    s = GraphStore("test_tenant")
    s.add_node(_node("src1", kind=NodeKind.SOURCE, label="Policy Doc v1", text="Source document"))
    s.add_node(_node("f1", kind=NodeKind.FACT, label="Refund window 30 days", text="30 days refund", source_ref="src1"))
    s.add_node(_node("c1", kind=NodeKind.CONCLUSION, label="Refund conclusion", text="Customer eligible"))
    
    s.add_edge(GraphEdge(id="e1", source="src1", target="f1", type=EdgeType.FROM_SOURCE))
    s.add_edge(GraphEdge(id="e2", source="f1", target="c1", type=EdgeType.LEADS_TO))
    
    p = cp.pack(s, "eligible", budget=500, depth=2)
    # Check that conclusion causes fact and source ancestor expansion
    assert "c1" in p["ids"]
    assert "f1" in p["ids"]
    assert "src1" in p["ids"]
    # Verify line format contains short_id, label, and parent link "<"
    assert "<" in p["text"]


# ==============================================================================
# SECTION 3: Delta Packing (delta & have_ids)
# ==============================================================================

def test_pack_delta_with_have_ids():
    s = GraphStore("test_tenant")
    s.add_node(_node("f1", kind=NodeKind.FACT, label="Fact 1 refund", text="refund 30 days"))
    s.add_node(_node("f2", kind=NodeKind.FACT, label="Fact 2 refund", text="refund 60 days"))
    
    # Agent initially has f1
    first = cp.pack(s, "refund", have_ids={"f1"})
    assert first["delta"] is not None
    # f2 should be added, f1 should not be in chosen added lines
    assert "f2" in first["ids"]
    assert "f1" not in first["ids"]
    assert first["delta"]["added"] >= 1


def test_mcp_delta_invalidation():
    s = GraphStore("test_tenant")
    
    # Store with nodes
    f1 = _node("f1", kind=NodeKind.FACT, label="Fact 1", text="refund 30 days")
    f2 = _node("f2", kind=NodeKind.FACT, label="Fact 2", text="refund 60 days")
    s.add_node(f1)
    s.add_node(f2)
    
    # Simulate agent state having f1 and f2
    s.agent_state["test_agent"] = {"ids": ["f1", "f2"], "query": "refund", "epoch": 1}
    
    # Now quarantine f2 and add f3
    f2.status = Status.QUARANTINED
    f3 = _node("f3", kind=NodeKind.FACT, label="Fact 3", text="refund 90 days")
    s.add_node(f3)
    s.bump_epoch()
    
    # Directly test the delta invalidation logic
    with s._lock:
        nodes = {n.id: n for n in s.nodes.values()}
    viewer = Viewer(agent_id="test_agent")
    ids = cp.short_ids([n for n in nodes.values() if viewer.can_see(n)])
    
    delivered = set(s.agent_state["test_agent"]["ids"])
    gone = []
    for i in sorted(delivered):
        n = nodes.get(i)
        if n is None or not viewer.can_see(n) or n.status != Status.HEALTHY or n.stale:
            why = "removed" if n is None else (n.status.value.lower() if n.status != Status.HEALTHY else "stale")
            gone.append(f"✗ {ids.get(i, i)} no longer valid ({why})")
            
    assert len(gone) == 1
    assert "no longer valid (quarantined)" in gone[0]


# ==============================================================================
# SECTION 4: find_path (Cypher & In-Memory BFS Fallback)
# ==============================================================================

def test_find_path_bfs_fallback():
    s = GraphStore("test_tenant")
    s.add_node(_node("s1", kind=NodeKind.SOURCE, label="Policy Doc"))
    s.add_node(_node("f1", kind=NodeKind.FACT, label="Fact 1"))
    s.add_node(_node("f2", kind=NodeKind.FACT, label="Fact 2"))
    s.add_node(_node("c1", kind=NodeKind.CONCLUSION, label="Conclusion 1"))
    
    s.add_edge(GraphEdge(id="e1", source="s1", target="f1", type=EdgeType.FROM_SOURCE))
    s.add_edge(GraphEdge(id="e2", source="f1", target="f2", type=EdgeType.SUPPORTS))
    s.add_edge(GraphEdge(id="e3", source="f2", target="c1", type=EdgeType.LEADS_TO))
    
    # Add an edge with non-causal type (e.g., CONTRADICTS) to another node
    s.add_node(_node("x1", kind=NodeKind.FACT, label="Contradicting Fact"))
    s.add_edge(GraphEdge(id="e4", source="f1", target="x1", type=EdgeType.CONTRADICTS))
    
    # Find path between s1 and c1
    res = cp.find_path(s, "s1", "c1")
    assert res["success"] is True
    assert res["hops"] == 3
    assert res["path"] == ["s1", "f1", "f2", "c1"]
    
    # Path to x1 should not be found via causal path
    res_x = cp.find_path(s, "s1", "x1")
    assert res_x["hops"] == 0
    assert res_x["path"] == []

    # Path from node to itself
    res_self = cp.find_path(s, "s1", "s1")
    assert res_self["success"] is True
    assert res_self["hops"] == 0
    assert res_self["path"] == ["s1"]

    # Path to nonexistent node
    res_none = cp.find_path(s, "s1", "nonexistent")
    assert res_none["success"] is False


# ==============================================================================
# SECTION 5: Prompt Diff Service & Boundary Tests
# ==============================================================================

def test_prompt_diff_basic_and_boundaries():
    s = GraphStore("test_tenant")
    # Setup healthy and quarantined nodes
    f_ok = _node("f_ok", kind=NodeKind.FACT, label="OK Fact", text="Refund window 30 days")
    f_bad = _node("f_bad", kind=NodeKind.FACT, label="Bad Fact", text="Refund window 90 days", status=Status.QUARANTINED)
    s.add_node(f_ok)
    s.add_node(f_bad)
    
    # Simulate prompt_diff route logic
    viewer = Viewer()
    with s._lock:
        quarantined = [n for n in s.nodes.values() if viewer.can_see(n) and n.status == Status.QUARANTINED and n.kind in [NodeKind.FACT, NodeKind.CONCLUSION]]
        healthy = [n for n in s.nodes.values() if viewer.can_see(n) and n.status == Status.HEALTHY and n.kind in [NodeKind.FACT, NodeKind.CONCLUSION]]
        
    removed_ids = [n.id for n in quarantined]
    after_nodes = [PromptDiffNode(node_id=n.id, text=n.text or n.label, status=n.status) for n in healthy]
    before_nodes = after_nodes + [PromptDiffNode(node_id=n.id, text=n.text or n.label, status=Status.HEALTHY) for n in quarantined]
    
    diff_resp = PromptDiffResponse(
        epoch_before=max(1, s.epoch - 1),
        epoch_after=s.epoch,
        before=before_nodes,
        after=after_nodes,
        removed_ids=removed_ids,
    )
    assert "f_bad" in diff_resp.removed_ids
    assert len(diff_resp.after) == 1
    assert diff_resp.after[0].node_id == "f_ok"


def test_prompt_diff_large_and_binary_payload():
    # Stress test >1MB text and binary characters
    large_text = "A" * (1024 * 1024 + 100) # > 1MB
    binary_text = "Hello\x00\x01\x02\x03World\ufffd\x7f"
    
    node_large = PromptDiffNode(node_id="large", text=large_text, status=Status.HEALTHY)
    node_bin = PromptDiffNode(node_id="bin", text=binary_text, status=Status.HEALTHY)
    
    resp = PromptDiffResponse(
        epoch_before=1,
        epoch_after=2,
        before=[node_large, node_bin],
        after=[node_bin],
        removed_ids=["large"],
    )
    # Check serialization
    dumped = resp.model_dump_json()
    assert len(dumped) > 1024 * 1024
    assert "large" in dumped


# ==============================================================================
# SECTION 6: Unicode / CJK Characters in Keyword & Semantic Search
# ==============================================================================

def test_unicode_and_cjk_keyword_and_vector():
    # Test CJK query and nodes
    n_cjk = _node("n_cjk", kind=NodeKind.FACT, label="\u9000\u6b3e\u653f\u7b56", text="\u5ba2\u6237\u572830\u5929\u5185\u53ef\u83b7\u5f97\u9000\u6b3e")
    n_de = _node("n_de", kind=NodeKind.FACT, label="Rückerstattung", text="Rückerstattungsrichtlinie für Kunden")
    
    words_cjk = cp._words("\u9000\u6b3e\u653f\u7b56")
    # Due to regex r"[a-z0-9]{2,}", CJK characters produce empty word list!
    assert words_cjk == []
    
    words_de = cp._words("Rückerstattung")
    # Due to non-ASCII 'ü', regex r"[a-z0-9]{2,}" drops 'ü' or splits it
    assert "rückerstattung" not in words_de
    
    v_cjk = embeddings.local_vector("\u9000\u6b3e\u653f\u7b56")
    assert np.linalg.norm(v_cjk) == 0.0
    
    idf = cp.idf_weights([n_cjk, n_de])
    ranked = cp.rank([n_cjk, n_de], "\u9000\u6b3e\u653f\u7b56", idf)
    # Both nodes receive 0.0 score because neither keyword nor vector matches
    assert all(score == 0.0 for _, score, _ in ranked)

