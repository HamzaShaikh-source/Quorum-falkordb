"""
Test Redis ACL Graph Isolation & GRAPH.LIST Leakage Check
"""

import redis
from falkordb import FalkorDB

def test_acl():
    print("=" * 60)
    print("TESTING REDIS ACL ISOLATION & GRAPH.LIST LEAKAGE")
    print("=" * 60)

    # 1. Connect as admin to create test graphs and set up ACL users
    r_admin = redis.Redis(host="localhost", port=6379, decode_responses=True)
    
    # Configure ACL users
    r_admin.execute_command("ACL", "SETUSER", "tenant_acme", "on", ">acme_pass", "~tenant_acme:*", "&*", "+@all")
    r_admin.execute_command("ACL", "SETUSER", "tenant_globex", "on", ">globex_pass", "~tenant_globex:*", "&*", "+@all")
    print("Configured ACL users: tenant_acme and tenant_globex")

    # Connect admin via FalkorDB and create graphs for acme and globex
    f_admin = FalkorDB(host="localhost", port=6379)
    g_acme = f_admin.select_graph("tenant_acme:commons")
    g_acme.query("CREATE (:Fact {id: 'acme_fact_1', text: 'Confidential Acme Knowledge'})")
    
    g_globex = f_admin.select_graph("tenant_globex:commons")
    g_globex.query("CREATE (:Fact {id: 'globex_fact_1', text: 'Globex Knowledge'})")
    print("Created graphs: tenant_acme:commons and tenant_globex:commons")

    # 2. Test Globex client reading its own graph
    r_globex = redis.Redis(host="localhost", port=6379, username="tenant_globex", password="globex_pass", decode_responses=True)
    f_globex = FalkorDB(host="localhost", port=6379, username="tenant_globex", password="globex_pass")
    
    g_own = f_globex.select_graph("tenant_globex:commons")
    res_own = g_own.query("MATCH (f:Fact) RETURN f.id").result_set
    print(f"Globex reading tenant_globex:commons: SUCCESS ({res_own})")

    # 3. Test Globex client reading Acme graph (MUST BE DENIED)
    denied = False
    try:
        g_denied = f_globex.select_graph("tenant_acme:commons")
        g_denied.query("MATCH (f:Fact) RETURN f.id")
    except Exception as e:
        denied = True
        print(f"Globex reading tenant_acme:commons: DENIED as expected! ({type(e).__name__}: {e})")

    if not denied:
        print("ERROR: Globex was NOT denied access to tenant_acme:commons!")

    # 4. Check GRAPH.LIST under Globex
    try:
        graph_list = r_globex.execute_command("GRAPH.LIST")
        print(f"GRAPH.LIST under tenant_globex returned: {graph_list}")
        leaked = any("tenant_acme" in str(name) for name in graph_list)
        if leaked:
            print("ALERT: GRAPH.LIST reveals other tenants' graph names! Gateway-level filtering fallback is REQUIRED.")
        else:
            print("SAFE: GRAPH.LIST does not leak other tenants' graph names.")
    except Exception as e:
        print(f"GRAPH.LIST under tenant_globex returned error / blocked: {e}")

if __name__ == "__main__":
    test_acl()
