"""
Test FalkorDB Data Persistence Across Container Restarts
"""

import subprocess
import time
import redis
from falkordb import FalkorDB

def test_persistence():
    print("=" * 60)
    print("TESTING DATA PERSISTENCE ACROSS CONTAINER RESTARTS")
    print("=" * 60)

    # 1. Connect and insert test node
    f = FalkorDB(host="localhost", port=6379)
    g = f.select_graph("persist_test")
    try:
        g.delete()
    except Exception:
        pass

    g.query("CREATE (:Fact {id: 'persist_node_1', text: 'Persistent Data Test Node', ts: timestamp()})")
    res_before = g.query("MATCH (f:Fact {id: 'persist_node_1'}) RETURN f.text").result_set
    print(f"Node created before restart: {res_before[0][0]}")

    # Trigger save to disk
    r = redis.Redis(host="localhost", port=6379)
    r.save()
    print("Executed synchronous SAVE to disk.")

    # 2. Restart container
    print("Restarting falkordb container...")
    subprocess.run(["docker", "restart", "falkordb"], check=True)
    time.sleep(3)

    # 3. Reconnect and verify data still exists
    f2 = FalkorDB(host="localhost", port=6379)
    g2 = f2.select_graph("persist_test")
    res_after = g2.query("MATCH (f:Fact {id: 'persist_node_1'}) RETURN f.text").result_set
    print(f"Node queried after restart: {res_after[0][0]}")

    assert res_after[0][0] == "Persistent Data Test Node"
    print("SUCCESS: Data successfully persisted across container restart!")

if __name__ == "__main__":
    test_persistence()
