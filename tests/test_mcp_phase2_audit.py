import os
import sys
import pytest
from starlette.testclient import TestClient

# Ensure backend in path and dev mode
os.environ.setdefault("QUORUM_ENV", "development")
os.environ.setdefault("QUORUM_DEMO", "1")
backend_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend"))
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)

from app.mcp_embed import build_mcp_app, TOOLS


@pytest.fixture(scope="module")
def mcp_client():
    app = build_mcp_app()
    with TestClient(app) as client:
        # Perform MCP initialize handshake
        r = client.post(
            "/mcp",
            headers={"Host": "localhost:8000"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test-client", "version": "1.0"},
                },
            },
        )
        assert r.status_code == 200
        session_id = r.headers.get("mcp-session-id")
        assert session_id is not None

        # Send initialized notification
        r_notif = client.post(
            "/mcp",
            headers={"Host": "localhost:8000", "mcp-session-id": session_id},
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )
        assert r_notif.status_code == 202

        from app.graph.queries import get_graph_store
        store = get_graph_store("acme")
        with store._lock:
            for nid in ["fact_bound_1", "conc_bound_1", "act_bound_1"]:
                store.nodes.pop(nid, None)
            store.edges = [e for e in store.edges if e.source not in ("fact_bound_1", "conc_bound_1", "act_bound_1") and e.target not in ("fact_bound_1", "conc_bound_1", "act_bound_1")]

        yield {"client": client, "session_id": session_id}


class TestMCPProtocolAndJSONRPC:
    def test_jsonrpc_parse_error_32700(self, mcp_client):
        client = mcp_client["client"]
        r = client.post(
            "/mcp",
            headers={"Host": "localhost:8000", "Content-Type": "application/json"},
            content="{invalid_json",
        )
        assert r.status_code == 400
        data = r.json()
        assert data["jsonrpc"] == "2.0"
        assert data["error"]["code"] == -32700

    def test_jsonrpc_invalid_request_32600_32602(self, mcp_client):
        client = mcp_client["client"]
        sid = mcp_client["session_id"]
        # Missing jsonrpc field
        r = client.post(
            "/mcp",
            headers={"Host": "localhost:8000", "mcp-session-id": sid},
            json={"id": 99, "method": "tools/list"},
        )
        assert r.status_code == 400
        data = r.json()
        assert data["jsonrpc"] == "2.0"
        assert data["error"]["code"] == -32602

    def test_jsonrpc_method_not_found_32601(self, mcp_client):
        client = mcp_client["client"]
        sid = mcp_client["session_id"]
        r = client.post(
            "/mcp",
            headers={"Host": "localhost:8000", "mcp-session-id": sid},
            json={"jsonrpc": "2.0", "id": 100, "method": "unknown/method"},
        )
        assert r.status_code == 200
        # Parse SSE stream response
        line = [l for l in r.text.splitlines() if l.startswith("data: ")][0]
        import json
        payload = json.loads(line[6:])
        assert payload["id"] == 100
        assert payload["error"]["code"] == -32601

    def test_tools_list_returns_all_15_tools(self, mcp_client):
        client = mcp_client["client"]
        sid = mcp_client["session_id"]
        r = client.post(
            "/mcp",
            headers={"Host": "localhost:8000", "mcp-session-id": sid},
            json={"jsonrpc": "2.0", "id": 101, "method": "tools/list"},
        )
        assert r.status_code == 200
        import json
        line = [l for l in r.text.splitlines() if l.startswith("data: ")][0]
        payload = json.loads(line[6:])
        tools = payload["result"]["tools"]
        tool_names = {t["name"] for t in tools}
        assert len(tools) == len(TOOLS) == 27
        assert tool_names == set(TOOLS)


class TestMCPSecurityAndRoles:
    def test_header_token_extraction(self, mcp_client):
        client = mcp_client["client"]
        sid = mcp_client["session_id"]
        # Valid agent header
        r = client.post(
            "/mcp",
            headers={"Host": "localhost:8000", "mcp-session-id": sid, "Authorization": "Bearer token_agent"},
            json={
                "jsonrpc": "2.0",
                "id": 102,
                "method": "tools/call",
                "params": {"name": "get_map", "arguments": {}},
            },
        )
        assert r.status_code == 200
        import json
        line = [l for l in r.text.splitlines() if l.startswith("data: ")][0]
        payload = json.loads(line[6:])
        assert payload["result"]["isError"] is False

    def test_missing_token_rejection(self, mcp_client):
        client = mcp_client["client"]
        sid = mcp_client["session_id"]
        # No header, no argument token, env token empty
        old_env = os.environ.pop("QUORUM_MCP_TOKEN", None)
        try:
            r = client.post(
                "/mcp",
                headers={"Host": "localhost:8000", "mcp-session-id": sid},
                json={
                    "jsonrpc": "2.0",
                    "id": 103,
                    "method": "tools/call",
                    "params": {"name": "remember_fact", "arguments": {"key": "k", "value": "v", "text": "t"}},
                },
            )
            assert r.status_code == 200
            import json
            line = [l for l in r.text.splitlines() if l.startswith("data: ")][0]
            payload = json.loads(line[6:])
            assert "Missing or invalid token" in payload["result"]["content"][0]["text"]
        finally:
            if old_env is not None:
                os.environ["QUORUM_MCP_TOKEN"] = old_env

    def test_role_spoofing_prevention(self, mcp_client):
        client = mcp_client["client"]
        sid = mcp_client["session_id"]
        # Agent connects with header token_agent, but passes token='token_human' in arguments
        r = client.post(
            "/mcp",
            headers={"Host": "localhost:8000", "mcp-session-id": sid, "Authorization": "Bearer token_agent"},
            json={
                "jsonrpc": "2.0",
                "id": 104,
                "method": "tools/call",
                "params": {
                    "name": "review_handoff",
                    "arguments": {"id": "h_dummy", "decision": "approve", "token": "token_human"},
                },
            },
        )
        assert r.status_code == 200
        import json
        line = [l for l in r.text.splitlines() if l.startswith("data: ")][0]
        payload = json.loads(line[6:])
        res_text = payload["result"]["content"][0]["text"]
        # Must refuse because the connection header (agent) overrides the argument token!
        assert "cannot approve or reject reviews" in res_text

    def test_agent_forbidden_from_admin_and_review_tools(self, mcp_client):
        client = mcp_client["client"]
        sid = mcp_client["session_id"]
        headers = {"Host": "localhost:8000", "mcp-session-id": sid, "Authorization": "Bearer token_agent"}
        import json

        # 1. review_handoff
        r1 = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 105,
                "method": "tools/call",
                "params": {"name": "review_handoff", "arguments": {"id": "h_1", "decision": "approve"}},
            },
        )
        line1 = [l for l in r1.text.splitlines() if l.startswith("data: ")][0]
        p1 = json.loads(line1[6:])
        assert "cannot approve or reject reviews" in p1["result"]["content"][0]["text"]

        # 2. quarantine
        r2 = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 106,
                "method": "tools/call",
                "params": {"name": "quarantine", "arguments": {"root_id": "fact_1"}},
            },
        )
        line2 = [l for l in r2.text.splitlines() if l.startswith("data: ")][0]
        p2 = json.loads(line2[6:])
        assert "cannot quarantine facts" in p2["result"]["content"][0]["text"]

        # 3. restore
        r3 = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 107,
                "method": "tools/call",
                "params": {"name": "restore", "arguments": {"root_id": "fact_1"}},
            },
        )
        line3 = [l for l in r3.text.splitlines() if l.startswith("data: ")][0]
        p3 = json.loads(line3[6:])
        assert "cannot restore facts" in p3["result"]["content"][0]["text"]


class TestAll15ToolsExecutionAndBoundaries:
    def test_remember_fact_and_boundaries(self, mcp_client):
        client = mcp_client["client"]
        sid = mcp_client["session_id"]
        headers = {"Host": "localhost:8000", "mcp-session-id": sid, "Authorization": "Bearer token_agent"}
        import json

        # Missing required parameter 'text'
        r_miss = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 201,
                "method": "tools/call",
                "params": {"name": "remember_fact", "arguments": {"key": "policy_limit", "value": "500"}},
            },
        )
        line = [l for l in r_miss.text.splitlines() if l.startswith("data: ")][0]
        p = json.loads(line[6:])
        assert p["result"]["isError"] is True
        assert "validation error" in p["result"]["content"][0]["text"].lower()

        # Invalid scope
        r_scope = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 202,
                "method": "tools/call",
                "params": {
                    "name": "remember_fact",
                    "arguments": {"key": "policy_limit", "value": "500", "text": "Limit is 500", "scope": "invalid"},
                },
            },
        )
        line = [l for l in r_scope.text.splitlines() if l.startswith("data: ")][0]
        p = json.loads(line[6:])
        assert "scope must be commons or team" in p["result"]["content"][0]["text"]

        # Successful remember_fact
        r_ok = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 203,
                "method": "tools/call",
                "params": {
                    "name": "remember_fact",
                    "arguments": {"id": "fact_bound_1", "key": "policy_limit", "value": "500", "text": "Limit is $500."},
                },
            },
        )
        line = [l for l in r_ok.text.splitlines() if l.startswith("data: ")][0]
        p = json.loads(line[6:])
        assert p["result"]["isError"] is False
        assert "fact_bound_1" in p["result"]["content"][0]["text"]

    def test_observe_tool(self, mcp_client):
        client = mcp_client["client"]
        sid = mcp_client["session_id"]
        headers = {"Host": "localhost:8000", "mcp-session-id": sid, "Authorization": "Bearer token_agent"}
        import json

        r = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 204,
                "method": "tools/call",
                "params": {"name": "observe", "arguments": {"text": "The refund window is 30 days.", "dry_run": True}},
            },
        )
        line = [l for l in r.text.splitlines() if l.startswith("data: ")][0]
        p = json.loads(line[6:])
        assert p["result"]["isError"] is False
        assert "candidates" in p["result"]["content"][0]["text"]

    def test_derive_tool_and_boundaries(self, mcp_client):
        client = mcp_client["client"]
        sid = mcp_client["session_id"]
        headers = {"Host": "localhost:8000", "mcp-session-id": sid, "Authorization": "Bearer token_agent"}
        import json

        # Non-existent parent ID
        r_fail = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 205,
                "method": "tools/call",
                "params": {
                    "name": "derive",
                    "arguments": {"text": "Conclusion", "parent_ids": ["non_existent_id_999"]},
                },
            },
        )
        line = [l for l in r_fail.text.splitlines() if l.startswith("data: ")][0]
        p = json.loads(line[6:])
        assert "Atomic derivation check failed" in p["result"]["content"][0]["text"]

        # Empty parent_ids
        r_empty = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 206,
                "method": "tools/call",
                "params": {"name": "derive", "arguments": {"text": "Conclusion", "parent_ids": []}},
            },
        )
        line = [l for l in r_empty.text.splitlines() if l.startswith("data: ")][0]
        p = json.loads(line[6:])
        assert "parent_ids must be a non-empty list" in p["result"]["content"][0]["text"]

        # Valid derive from existing fact
        r_ok = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 207,
                "method": "tools/call",
                "params": {
                    "name": "derive",
                    "arguments": {"id": "conc_bound_1", "text": "Valid conclusion", "parent_ids": ["fact_bound_1"]},
                },
            },
        )
        line = [l for l in r_ok.text.splitlines() if l.startswith("data: ")][0]
        p = json.loads(line[6:])
        assert "conc_bound_1" in p["result"]["content"][0]["text"]

    def test_stage_and_execute_action_and_boundaries(self, mcp_client):
        client = mcp_client["client"]
        sid = mcp_client["session_id"]
        headers = {"Host": "localhost:8000", "mcp-session-id": sid, "Authorization": "Bearer token_agent"}
        import json

        # Non-existent parent
        r_fail = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 208,
                "method": "tools/call",
                "params": {
                    "name": "stage_action",
                    "arguments": {
                        "label": "Do something",
                        "action_type": "refund",
                        "reversibility": "REVERSIBLE",
                        "parent_ids": ["non_existent_id"],
                    },
                },
            },
        )
        line = [l for l in r_fail.text.splitlines() if l.startswith("data: ")][0]
        p = json.loads(line[6:])
        assert "Atomic derivation check failed" in p["result"]["content"][0]["text"]

        # Valid stage
        r_stage = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 209,
                "method": "tools/call",
                "params": {
                    "name": "stage_action",
                    "arguments": {
                        "id": "act_bound_1",
                        "label": "Process $500 refund",
                        "action_type": "refund",
                        "reversibility": "REVERSIBLE",
                        "parent_ids": ["conc_bound_1"],
                    },
                },
            },
        )
        line = [l for l in r_stage.text.splitlines() if l.startswith("data: ")][0]
        p = json.loads(line[6:])
        assert "act_bound_1" in p["result"]["content"][0]["text"]

        # Execute non-existent action
        r_exec_missing = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 210,
                "method": "tools/call",
                "params": {"name": "execute_action", "arguments": {"id": "non_existent_act"}},
            },
        )
        line = [l for l in r_exec_missing.text.splitlines() if l.startswith("data: ")][0]
        p = json.loads(line[6:])
        assert "not found" in p["result"]["content"][0]["text"].lower() or "404" in p["result"]["content"][0]["text"]

    def test_claim_handoff_and_boundaries(self, mcp_client):
        client = mcp_client["client"]
        sid = mcp_client["session_id"]
        headers = {"Host": "localhost:8000", "mcp-session-id": sid, "Authorization": "Bearer token_agent"}
        import json

        # Non-existent handoff
        r = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 211,
                "method": "tools/call",
                "params": {"name": "claim_handoff", "arguments": {"id": "h_none"}},
            },
        )
        line = [l for l in r.text.splitlines() if l.startswith("data: ")][0]
        p = json.loads(line[6:])
        assert "not found" in p["result"]["content"][0]["text"].lower()

    def test_quarantine_restore_explain_blast_radius(self, mcp_client):
        client = mcp_client["client"]
        sid = mcp_client["session_id"]
        auditor_headers = {"Host": "localhost:8000", "mcp-session-id": sid, "Authorization": "Bearer token_auditor"}
        import json

        # explain_blast_radius on non-existent node
        r_b_none = client.post(
            "/mcp",
            headers=auditor_headers,
            json={
                "jsonrpc": "2.0",
                "id": 212,
                "method": "tools/call",
                "params": {"name": "explain_blast_radius", "arguments": {"root_id": "non_existent_node"}},
            },
        )
        line = [l for l in r_b_none.text.splitlines() if l.startswith("data: ")][0]
        p = json.loads(line[6:])
        assert "not found" in p["result"]["content"][0]["text"].lower()

        # explain_blast_radius on fact_bound_1
        r_b = client.post(
            "/mcp",
            headers=auditor_headers,
            json={
                "jsonrpc": "2.0",
                "id": 213,
                "method": "tools/call",
                "params": {"name": "explain_blast_radius", "arguments": {"root_id": "fact_bound_1"}},
            },
        )
        line = [l for l in r_b.text.splitlines() if l.startswith("data: ")][0]
        p = json.loads(line[6:])
        assert p["result"]["isError"] is False
        assert "predicted_impact_count" in p["result"]["content"][0]["text"]

        # quarantine fact_bound_1
        r_q = client.post(
            "/mcp",
            headers=auditor_headers,
            json={
                "jsonrpc": "2.0",
                "id": 214,
                "method": "tools/call",
                "params": {"name": "quarantine", "arguments": {"root_id": "fact_bound_1", "reason": "Test quarantine"}},
            },
        )
        line = [l for l in r_q.text.splitlines() if l.startswith("data: ")][0]
        p = json.loads(line[6:])
        assert p["result"]["isError"] is False
        assert "affected_count" in p["result"]["content"][0]["text"]

        # restore fact_bound_1
        r_res = client.post(
            "/mcp",
            headers=auditor_headers,
            json={
                "jsonrpc": "2.0",
                "id": 215,
                "method": "tools/call",
                "params": {"name": "restore", "arguments": {"root_id": "fact_bound_1"}},
            },
        )
        line = [l for l in r_res.text.splitlines() if l.startswith("data: ")][0]
        p = json.loads(line[6:])
        assert p["result"]["isError"] is False
        assert "restored_count" in p["result"]["content"][0]["text"]

    def test_read_tools_and_boundaries(self, mcp_client):
        client = mcp_client["client"]
        sid = mcp_client["session_id"]
        headers = {"Host": "localhost:8000", "mcp-session-id": sid, "Authorization": "Bearer token_agent"}
        import json

        # 1. retrieve_context full and compact
        r_c1 = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 216,
                "method": "tools/call",
                "params": {"name": "retrieve_context", "arguments": {"query": "refund limit", "k": -5}},
            },
        )
        line = [l for l in r_c1.text.splitlines() if l.startswith("data: ")][0]
        p = json.loads(line[6:])
        assert p["result"]["isError"] is False

        r_c2 = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 217,
                "method": "tools/call",
                "params": {"name": "retrieve_context", "arguments": {"query": "refund limit", "format": "compact", "budget_tokens": 100}},
            },
        )
        line = [l for l in r_c2.text.splitlines() if l.startswith("data: ")][0]
        p = json.loads(line[6:])
        assert p["result"]["isError"] is False

        # 2. get_map
        r_map = client.post(
            "/mcp",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 218, "method": "tools/call", "params": {"name": "get_map", "arguments": {}}},
        )
        line = [l for l in r_map.text.splitlines() if l.startswith("data: ")][0]
        p = json.loads(line[6:])
        assert p["result"]["isError"] is False
        assert "map" in p["result"]["content"][0]["text"]

        # 3. get_node valid and non-existent
        r_node_none = client.post(
            "/mcp",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 219, "method": "tools/call", "params": {"name": "get_node", "arguments": {"id": "non_existent_node"}}},
        )
        line = [l for l in r_node_none.text.splitlines() if l.startswith("data: ")][0]
        p = json.loads(line[6:])
        assert "not found" in p["result"]["content"][0]["text"].lower()

        r_node_ok = client.post(
            "/mcp",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 220, "method": "tools/call", "params": {"name": "get_node", "arguments": {"id": "fact_bound_1"}}},
        )
        line = [l for l in r_node_ok.text.splitlines() if l.startswith("data: ")][0]
        p = json.loads(line[6:])
        assert p["result"]["isError"] is False

        # 4. find_path
        r_path = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 221,
                "method": "tools/call",
                "params": {"name": "find_path", "arguments": {"from_id": "fact_bound_1", "to_id": "conc_bound_1"}},
            },
        )
        line = [l for l in r_path.text.splitlines() if l.startswith("data: ")][0]
        p = json.loads(line[6:])
        assert p["result"]["isError"] is False

        # 5. delta
        r_delta = client.post(
            "/mcp",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 222, "method": "tools/call", "params": {"name": "delta", "arguments": {}}},
        )
        line = [l for l in r_delta.text.splitlines() if l.startswith("data: ")][0]
        p = json.loads(line[6:])
        assert p["result"]["isError"] is False
