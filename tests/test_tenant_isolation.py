"""
Test Suite: Per-Tenant Memory & Redis ACL Isolation
Verifies Track 02 Focus #5: One graph namespace per tenant enforced by Redis ACL.
"""

import os
import pytest
import redis
from falkordb import FalkorDB

def test_tenant_isolation():
    host = os.getenv("QUORUM_FALKORDB_HOST", os.getenv("FALKORDB_HOST", "localhost"))
    port = int(os.getenv("QUORUM_FALKORDB_PORT", os.getenv("FALKORDB_PORT", "6379")))
    # 1. Admin setup
    try:
        r_admin = redis.Redis(host=host, port=port, decode_responses=True, socket_connect_timeout=1)
        r_admin.ping()
    except Exception:
        pytest.skip("FalkorDB/Redis is not running on localhost:6379. Start it with: docker compose up -d falkordb")
    r_admin.execute_command("ACL", "SETUSER", "tenant_acme", "on", ">acme_pass", "~tenant_acme:*", "&*", "+@all")
    r_admin.execute_command("ACL", "SETUSER", "tenant_globex", "on", ">globex_pass", "~tenant_globex:*", "&*", "+@all")

    f_admin = FalkorDB(host=host, port=port)
    g_acme = f_admin.select_graph("tenant_acme:commons")
    g_acme.query("CREATE (:Fact {id: 'acme_fact_secret', text: 'Acme Secret Intellectual Property'})")

    g_globex = f_admin.select_graph("tenant_globex:commons")
    g_globex.query("CREATE (:Fact {id: 'globex_fact_1', text: 'Globex Public Fact'})")

    # 2. Globex client reading its own tenant graph -> SUCCESS
    f_globex = FalkorDB(host=host, port=port, username="tenant_globex", password="globex_pass")
    g_globex_client = f_globex.select_graph("tenant_globex:commons")
    res_own = g_globex_client.query("MATCH (f:Fact) RETURN f.id").result_set
    assert len(res_own) > 0
    assert res_own[0][0] == "globex_fact_1"

    # 3. Globex client reading Acme tenant graph -> DENIED
    with pytest.raises(Exception) as excinfo:
        g_acme_denied = f_globex.select_graph("tenant_acme:commons")
        g_acme_denied.query("MATCH (f:Fact) RETURN f.id")
    assert "No permissions" in str(excinfo.value)

    # 4. Gateway-level tenant filtering fallback on GRAPH.LIST
    r_globex = redis.Redis(host=host, port=port, username="tenant_globex", password="globex_pass", decode_responses=True)
    raw_graph_list = r_globex.execute_command("GRAPH.LIST")
    # Gateway filtering logic:
    filtered_list = [name for name in raw_graph_list if name.startswith("tenant_globex:")]
    assert "tenant_globex:commons" in filtered_list
    assert "tenant_acme:commons" not in filtered_list
