"""
Exhaustive Bug Hunt, Parameter Validation, Multi-Vector Stress & Boundary Test Suite
for MCP Tool Handlers & Write Safety Guards (Subagent 12)
"""
import sys
import os
import traceback

os.environ["QUORUM_ENV"] = "development"
os.environ["QUORUM_DEMO"] = "1"

# Ensure backend is on sys.path
backend_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend"))
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)

from app.config import settings
settings.ENV = "development"
from app.graph import queries
from app.models.common import HandoffKind, HandoffStatus
from app.models.handoffs import Handoff
from app.services import mcp_tools as t
from app.services import mcp_guard

def run_tests():
    tenant = "subagent12_test"
    queries.forget_stores()
    store = queries.get_graph_store(tenant)

    agent_tok = f"token_{tenant}_agent"
    auditor_tok = f"token_{tenant}_auditor"
    human_tok = f"token_{tenant}_human"

    print("=== STARTING EXHAUSTIVE MCP AUDIT & STRESS TESTS ===")
    findings = []

    # -------------------------------------------------------------
    # 1. MCP_GUARD AUDIT: TOKEN RESOLUTION & MCP_REQUIRE_HEADER
    # -------------------------------------------------------------
    print("\n[TEST 1] Token resolution priority: header vs arg vs env")
    # Reset header token contextvar
    mcp_guard._header_token.set("")
    
    # Arg token
    assert mcp_guard.effective_token("tok_arg") == "tok_arg", "Arg token should resolve"
    
    # Env token fallback
    os.environ["QUORUM_MCP_TOKEN"] = "tok_env"
    assert mcp_guard.effective_token("") == "tok_env", "Env token should resolve when arg is empty"
    
    # Arg beats env
    assert mcp_guard.effective_token("tok_arg") == "tok_arg", "Arg token should beat env token"
    
    # Header beats arg and env
    mcp_guard._header_token.set("tok_hdr")
    assert mcp_guard.effective_token("tok_arg") == "tok_hdr", "Header token should beat arg"
    assert mcp_guard.effective_token("") == "tok_hdr", "Header token should beat env"
    mcp_guard._header_token.set("")
    del os.environ["QUORUM_MCP_TOKEN"]

    # AUDIT: What happens when MCP_REQUIRE_HEADER is set?
    print("\n[TEST 2] Audit MCP_REQUIRE_HEADER enforcement in mcp_guard")
    old_req = settings.MCP_REQUIRE_HEADER
    try:
        settings.MCP_REQUIRE_HEADER = True
        # If header is required, does effective_token() or guarded() or _get_auth() reject arg token?
        tok_resolved = mcp_guard.effective_token("tok_arg_only")
        print(f"When MCP_REQUIRE_HEADER=True, effective_token('tok_arg_only') returned: '{tok_resolved}'")
        if tok_resolved == "tok_arg_only":
            findings.append({
                "severity": "HIGH",
                "file": "backend/app/services/mcp_guard.py",
                "line": 37,
                "issue": "mcp_guard.effective_token() does not respect settings.mcp_header_required(). When MCP_REQUIRE_HEADER is True, argument tokens are still accepted unless blocked by HTTP gateway. Standalone/embedded FastMCP tools called without HTTP gateway or direct function calls bypass header enforcement."
            })
    finally:
        settings.MCP_REQUIRE_HEADER = old_req

    # -------------------------------------------------------------
    # 2. ROLE SPOOFING & ACCESS CONTROL
    # -------------------------------------------------------------
    print("\n[TEST 3] Role Enforcement on Admin & Review Tools")
    
    # Seed a fact
    res = t.guarded(t.remember_fact)("refund_policy", "30", "Refund window is 30 days.", token=agent_tok)
    assert res["success"] is True
    fact_id = res["fact_id"]

    # 2.1 Agent calling quarantine
    q_res = t.guarded(t.quarantine)(fact_id, token=agent_tok)
    print(f"Agent quarantine result: success={q_res.get('success')}, error={q_res.get('error')}")
    assert q_res.get("success") is False, "Agent MUST NOT be allowed to quarantine"
    assert "cannot quarantine facts" in q_res.get("error", "").lower()

    # 2.2 Agent calling restore
    # First quarantine as auditor
    q_aud = t.guarded(t.quarantine)(fact_id, token=auditor_tok)
    assert q_aud.get("success") is True, f"Auditor should quarantine: {q_aud}"
    
    r_res = t.guarded(t.restore)(fact_id, token=agent_tok)
    print(f"Agent restore result: success={r_res.get('success')}, error={r_res.get('error')}")
    assert r_res.get("success") is False, "Agent MUST NOT be allowed to restore"
    assert "cannot restore facts" in r_res.get("error", "").lower()
    
    # Restore as auditor
    r_aud = t.guarded(t.restore)(fact_id, token=auditor_tok)
    assert r_aud.get("success") is True

    # 2.3 Agent calling review_handoff
    # Add a handoff
    ho = Handoff(
        id="ho_test_01",
        kind=HandoffKind.REVIEW,
        status=HandoffStatus.PENDING,
        context_root_id=fact_id,
        task="Review needed"
    )
    store.add_handoff(ho)

    rev_agent = t.guarded(t.review_handoff)("ho_test_01", "approve", token=agent_tok)
    print(f"Agent review_handoff result: success={rev_agent.get('success')}, error={rev_agent.get('error')}")
    assert rev_agent.get("success") is False, "Agent MUST NOT be allowed to review handoff"
    assert "cannot approve or reject reviews" in rev_agent.get("error", "").lower()

    # 2.4 Auditor calling review_handoff
    rev_aud = t.guarded(t.review_handoff)("ho_test_01", "approve", token=auditor_tok)
    print(f"Auditor review_handoff result: success={rev_aud.get('success')}, error={rev_aud.get('error')}")
    assert rev_aud.get("success") is False, "Auditor MUST NOT be allowed to review handoff (Admin/Human only)"
    assert "cannot approve or reject reviews" in rev_aud.get("error", "").lower()

    # 2.5 Agent claiming REVIEW handoff
    claim_rev = t.guarded(t.claim_handoff)("ho_test_01", token=agent_tok)
    print(f"Agent claim REVIEW handoff result: success={claim_rev.get('success')}, error={claim_rev.get('error')}")
    assert claim_rev.get("success") is False, "Agent MUST NOT be allowed to claim REVIEW handoffs"

    # 2.6 Missing token altogether
    print("\n[TEST 4] Missing token across all 15 tools")
    tools_to_test = [
        ("remember_fact", lambda: t.guarded(t.remember_fact)("k", "v", "txt")),
        ("derive", lambda: t.guarded(t.derive)("txt", ["f1"])),
        ("retrieve_context", lambda: t.guarded(t.retrieve_context)("query")),
        ("stage_action", lambda: t.guarded(t.stage_action)("act", "email", "reversible", ["f1"])),
        ("execute_action", lambda: t.guarded(t.execute_action)("a1")),
        ("claim_handoff", lambda: t.guarded(t.claim_handoff)("h1")),
        ("quarantine", lambda: t.guarded(t.quarantine)("f1")),
        ("restore", lambda: t.guarded(t.restore)("f1")),
        ("explain_blast_radius", lambda: t.guarded(t.explain_blast_radius)("f1")),
        ("review_handoff", lambda: t.guarded(t.review_handoff)("h1", "approve")),
        ("get_map", lambda: t.guarded(t.get_map)()),
        ("get_node", lambda: t.guarded(t.get_node)("f1")),
        ("find_path", lambda: t.guarded(t.find_path)("f1", "f2")),
        ("delta", lambda: t.guarded(t.delta)()),
        ("observe", lambda: t.guarded(t.observe)("some text")),
    ]
    for name, call in tools_to_test:
        out = call()
        assert out.get("success") is False, f"Tool {name} should fail with missing token"
        assert "token" in out.get("error", "").lower() or "authorization" in out.get("error", "").lower(), f"{name} error should mention token"
    print("All 15 tools properly reject calls with missing tokens.")

    # -------------------------------------------------------------
    # 3. BOUNDARY & STRESS TESTS ACROSS ALL 15 TOOLS
    # -------------------------------------------------------------
    print("\n[TEST 5] Multi-vector stress & boundary tests")

    # 3.1 Non-existent node IDs
    print("Testing non-existent node IDs...")
    ne_blast = t.guarded(t.explain_blast_radius)("missing_node_999", token=auditor_tok)
    assert ne_blast.get("success") is False, f"explain_blast_radius missing node failed: {ne_blast}"

    ne_get = t.guarded(t.get_node)("missing_node_999", token=agent_tok)
    assert ne_get.get("success") is False, f"get_node missing node failed: {ne_get}"

    ne_path = t.guarded(t.find_path)("missing_1", "missing_2", token=agent_tok)
    assert ne_path.get("success") is False, f"find_path missing nodes failed: {ne_path}"

    ne_exec = t.guarded(t.execute_action)("missing_act_999", token=agent_tok)
    assert ne_exec.get("success") is False, f"execute_action missing action failed: {ne_exec}"

    ne_claim = t.guarded(t.claim_handoff)("missing_ho_999", token=agent_tok)
    assert ne_claim.get("success") is False, f"claim_handoff missing handoff failed: {ne_claim}"

    ne_review = t.guarded(t.review_handoff)("missing_ho_999", "approve", token=human_tok)
    assert ne_review.get("success") is False, f"review_handoff missing handoff failed: {ne_review}"

    ne_quar = t.guarded(t.quarantine)("missing_fact_999", token=auditor_tok)
    assert ne_quar.get("success") is False, f"quarantine missing fact failed: {ne_quar}"

    ne_rest = t.guarded(t.restore)("missing_fact_999", token=auditor_tok)
    assert ne_rest.get("success") is False, f"restore missing fact failed: {ne_rest}"

    # 3.2 Non-string / integer node IDs in resolve_ref (derive, stage_action, get_node, find_path)
    print("Testing integer / non-string arguments in resolve_ref...")
    try:
        int_node = t.guarded(t.get_node)(123, token=agent_tok) # type: ignore
        print(f"get_node(123) returned: {int_node}")
    except Exception as e:
        print(f"CRASH on get_node(123): {type(e).__name__}: {e}")
        traceback.print_exc()
        findings.append({
            "severity": "MEDIUM",
            "file": "backend/app/services/context_pack.py",
            "line": 97,
            "issue": f"resolve_ref crashes with AttributeError when ref is not a string (e.g. integer 123): {e}"
        })

    try:
        int_derive = t.guarded(t.derive)("conclusion", [123], token=agent_tok) # type: ignore
        print(f"derive with [123] returned: {int_derive}")
    except Exception as e:
        print(f"CRASH on derive('...', [123]): {type(e).__name__}: {e}")
        traceback.print_exc()
        findings.append({
            "severity": "MEDIUM",
            "file": "backend/app/services/context_pack.py",
            "line": 97,
            "issue": f"derive crashes with AttributeError when parent_ids contains non-string item (e.g. [123]): {e}"
        })

    try:
        int_path = t.guarded(t.find_path)(123, 456, token=agent_tok) # type: ignore
        print(f"find_path(123, 456) returned: {int_path}")
    except Exception as e:
        print(f"CRASH on find_path(123, 456): {type(e).__name__}: {e}")
        traceback.print_exc()
        findings.append({
            "severity": "MEDIUM",
            "file": "backend/app/services/context_pack.py",
            "line": 97,
            "issue": f"find_path crashes with AttributeError when from_id or to_id is not a string: {e}"
        })

    # 3.3 Text=None / Label=None
    print("Testing None arguments for text/label...")
    try:
        none_fact = t.guarded(t.remember_fact)("k", "v", None, token=agent_tok) # type: ignore
        print(f"remember_fact with text=None returned: {none_fact}")
    except Exception as e:
        print(f"CRASH on remember_fact(text=None): {type(e).__name__}: {e}")
        findings.append({
            "severity": "MEDIUM",
            "file": "backend/app/services/mcp_write.py",
            "line": 50,
            "issue": f"remember_fact crashes with TypeError when text=None due to text[:60]: {e}"
        })

    try:
        none_derive = t.guarded(t.derive)(None, [fact_id], token=agent_tok) # type: ignore
        print(f"derive with text=None returned: {none_derive}")
    except Exception as e:
        print(f"CRASH on derive(text=None): {type(e).__name__}: {e}")
        findings.append({
            "severity": "MEDIUM",
            "file": "backend/app/services/mcp_write.py",
            "line": 201,
            "issue": f"derive crashes with TypeError when text=None due to text[:60]: {e}"
        })

    try:
        none_stage = t.guarded(t.stage_action)(None, "act_type", "reversible", [fact_id], token=agent_tok) # type: ignore
        print(f"stage_action with label=None returned: {none_stage}")
    except Exception as e:
        print(f"CRASH on stage_action(label=None): {type(e).__name__}: {e}")
        findings.append({
            "severity": "MEDIUM",
            "file": "backend/app/services/mcp_write.py",
            "line": 277,
            "issue": f"stage_action crashes with TypeError when label=None due to label[:60]: {e}"
        })

    # 3.4 retrieve_context parameters
    print("Testing retrieve_context parameter stress...")
    # negative k
    rc_neg_k = t.guarded(t.retrieve_context)("refund", token=agent_tok, k=-5)
    assert rc_neg_k.get("count") >= 0, f"Negative k handled: {rc_neg_k}"
    
    # negative budget_tokens in compact format
    rc_neg_b = t.guarded(t.retrieve_context)("refund", token=agent_tok, format="compact", budget_tokens=-50)
    assert rc_neg_b.get("format") == "compact", f"Negative budget_tokens handled: {rc_neg_b}"

    # empty query
    rc_empty_q = t.guarded(t.retrieve_context)("", token=agent_tok)
    assert rc_empty_q.get("count") == 0, f"Empty query should return 0 count: {rc_empty_q}"

    # unicode & max-size query
    long_query = "refund " * 1000 + " 🚀"
    rc_long = t.guarded(t.retrieve_context)(long_query, token=agent_tok)
    assert rc_long.get("epoch") is not None

    # 3.5 Rate limiting & Call recording
    print("Testing Rate Limiting & Call Recording...")
    calls_before = len(store.calls)
    t.guarded(t.get_map)(token=agent_tok)
    calls_after = len(store.calls)
    assert calls_after == calls_before + 1, f"Call was not recorded: before={calls_before}, after={calls_after}"
    last_call = store.calls[-1]
    assert last_call["tool"] == "get_map"
    assert last_call["role"] == "agent"
    print("Call recording verified successfully.")

    # 3.6 delta tool stress
    print("Testing delta tool...")
    d1 = t.guarded(t.delta)(token=agent_tok)
    assert "epoch" in d1
    print(f"Delta output: {d1.get('text')}")

    # 3.7 observe tool stress
    print("Testing observe tool...")
    obs_res = t.guarded(t.observe)("The refund window in Spain is 45 days.", token=agent_tok)
    print("obs_res:", obs_res)
    assert obs_res.get("success") is True
    assert obs_res.get("stored") >= 1 or len(obs_res.get("duplicates", [])) >= 1

    print("\n=== SUMMARY OF FINDINGS ===")
    for f in findings:
        print(f"[{f['severity']}] {f['file']}:{f['line']} - {f['issue']}")

    return findings

if __name__ == "__main__":
    findings = run_tests()
    print(f"\nTotal findings: {len(findings)}")
