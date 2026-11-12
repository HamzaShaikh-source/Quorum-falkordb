"""
tests/test_blast_stress.py

Subagent 16: Stress-testing Blast Radius Dry-run Preview (blast_preview)

Covers:
1. Build asymmetric DAGs with varying fan-outs (fan-out 2, 5, 20).
2. Call blast_preview(node_id) and verify returned impacted nodes count and list
   match exact descendant subgraph.
3. Verify that blast_preview is read-only and causes 0 mutations or status changes to nodes.
4. Concurrently run blast_preview on 100 nodes across 10 threads while writes occur.
5. In-depth verdict on logic, parameters, and query latencies.
"""

import time
import threading
import random
from typing import Dict, List, Set, Any, Tuple
import graph.queries as queries

TENANT_PREFIX = "test_blast_stress"


def reset_graph(tenant_id: str):
    g = queries.get_graph(tenant_id, "commons")
    try:
        g.query("MATCH (n) DETACH DELETE n")
    except Exception:
        pass
    queries.ensure_indexes(tenant_id)


def compute_ground_truth(
    adjacency: Dict[str, List[str]],
    node_meta: Dict[str, Dict[str, Any]],
    root_id: str,
    max_depth: int = 15,
) -> Dict[str, Any]:
    """
    Computes exact ground-truth descendant subgraph for LEADS_TO / SUPPORTS edges
    using breadth-first search from root_id (including root_id at depth 0).
    """
    if root_id not in node_meta:
        return {
            "node_ids": set(),
            "impacted_count": 0,
            "facts": 0,
            "conclusions": 0,
            "staged": 0,
            "executed_reversible": 0,
            "executed_compensable": 0,
            "executed_irreversible": 0,
        }

    visited: Set[str] = {root_id}
    queue: List[Tuple[str, int]] = [(root_id, 0)]

    while queue:
        curr, depth = queue.pop(0)
        if depth >= max_depth:
            continue
        for nxt in adjacency.get(curr, []):
            if nxt not in visited:
                visited.add(nxt)
                queue.append((nxt, depth + 1))

    facts = sum(1 for nid in visited if node_meta[nid].get("type") == "Fact")
    conclusions = sum(1 for nid in visited if node_meta[nid].get("type") == "Conclusion")
    staged = sum(
        1 for nid in visited
        if node_meta[nid].get("type") == "Action" and not node_meta[nid].get("executed", False)
    )
    exec_rev = sum(
        1 for nid in visited
        if node_meta[nid].get("type") == "Action"
        and node_meta[nid].get("executed", False)
        and node_meta[nid].get("reversibility") == "REVERSIBLE"
    )
    exec_comp = sum(
        1 for nid in visited
        if node_meta[nid].get("type") == "Action"
        and node_meta[nid].get("executed", False)
        and node_meta[nid].get("reversibility", "COMPENSABLE") == "COMPENSABLE"
    )
    exec_irrev = sum(
        1 for nid in visited
        if node_meta[nid].get("type") == "Action"
        and node_meta[nid].get("executed", False)
        and node_meta[nid].get("reversibility") == "IRREVERSIBLE"
    )

    return {
        "node_ids": visited,
        "impacted_count": len(visited),
        "facts": facts,
        "conclusions": conclusions,
        "staged": staged,
        "executed_reversible": exec_rev,
        "executed_compensable": exec_comp,
        "executed_irreversible": exec_irrev,
    }


def verify_preview_matches_ground_truth(preview: Dict[str, Any], truth: Dict[str, Any], node_id: str):
    preview_ids = set(preview["node_ids"])
    truth_ids = truth["node_ids"]
    assert preview_ids == truth_ids, (
        f"Node {node_id}: node_ids mismatch!\n"
        f"Extra in preview: {preview_ids - truth_ids}\n"
        f"Missing from preview: {truth_ids - preview_ids}"
    )
    assert preview["impacted_count"] == truth["impacted_count"], (
        f"Node {node_id}: impacted_count mismatch! Got {preview['impacted_count']}, expected {truth['impacted_count']}"
    )
    assert preview["facts"] == truth["facts"], (
        f"Node {node_id}: facts count mismatch! Got {preview['facts']}, expected {truth['facts']}"
    )
    assert preview["conclusions"] == truth["conclusions"], (
        f"Node {node_id}: conclusions count mismatch! Got {preview['conclusions']}, expected {truth['conclusions']}"
    )
    assert preview["actions"]["staged"] == truth["staged"], (
        f"Node {node_id}: staged actions mismatch! Got {preview['actions']['staged']}, expected {truth['staged']}"
    )
    assert preview["actions"]["executed_reversible"] == truth["executed_reversible"], (
        f"Node {node_id}: exec_rev mismatch! Got {preview['actions']['executed_reversible']}, expected {truth['executed_reversible']}"
    )
    assert preview["actions"]["executed_compensable"] == truth["executed_compensable"], (
        f"Node {node_id}: exec_comp mismatch! Got {preview['actions']['executed_compensable']}, expected {truth['executed_compensable']}"
    )
    assert preview["actions"]["executed_irreversible"] == truth["executed_irreversible"], (
        f"Node {node_id}: exec_irrev mismatch! Got {preview['actions']['executed_irreversible']}, expected {truth['executed_irreversible']}"
    )


# =========================================================================
# TEST 1 & 2: ASYMMETRIC DAGs WITH VARYING FAN-OUTS (2, 5, 20)
# =========================================================================

def test_asymmetric_dag_fanout_2():
    """
    Builds asymmetric DAG with fan-out 2:
    - Root fact fans out to 2 branches.
    - Left branch goes 4 levels deep; right branch goes 2 levels deep.
    - Multiple cross-links (diamonds) where nodes have multiple parents.
    - Mixture of Facts, Conclusions, Decisions, and Actions (staged & executed with 3 reversibilities).
    - Verifies blast_preview on all nodes against exact ground-truth descendant subgraph.
    """
    tenant = f"{TENANT_PREFIX}_fo2"
    reset_graph(tenant)
    g = queries.get_graph(tenant, "commons")

    adjacency: Dict[str, List[str]] = {}
    node_meta: Dict[str, Dict[str, Any]] = {}

    def add_node(nid: str, ntype: str, extra_labels: List[str] = None, **props):
        lbls = [ntype, "MemoryNode"]
        if extra_labels:
            lbls.extend(extra_labels)
        label_str = ":" + ":".join(lbls)
        prop_parts = [f"id: '{nid}'", "status: 'HEALTHY'"]
        for k, v in props.items():
            if isinstance(v, bool):
                prop_parts.append(f"{k}: {'true' if v else 'false'}")
            elif isinstance(v, (int, float)):
                prop_parts.append(f"{k}: {v}")
            else:
                prop_parts.append(f"{k}: '{v}'")
        prop_str = ", ".join(prop_parts)
        g.query(f"CREATE ({label_str} {{{prop_str}}})")
        node_meta[nid] = {"type": ntype, **props}
        adjacency.setdefault(nid, [])

    def add_edge(src: str, dst: str, rel: str = "LEADS_TO"):
        g.query(f"""
        MATCH (a:MemoryNode {{id: '{src}'}}), (b:MemoryNode {{id: '{dst}'}})
        CREATE (a)-[:{rel}]->(b)
        """)
        adjacency[src].append(dst)

    # Level 0
    add_node("root_fact", "Fact", text="Root Fact")

    # Level 1 (2 nodes)
    add_node("c_L1_0", "Conclusion", text="Conclusion L1.0")
    add_node("c_L1_1", "Conclusion", text="Conclusion L1.1")
    add_edge("root_fact", "c_L1_0", "LEADS_TO")
    add_edge("root_fact", "c_L1_1", "SUPPORTS")

    # Level 2 (asymmetric: branch 0 gets 2 children, branch 1 gets 1 child + cross-link)
    add_node("c_L2_0", "Conclusion", text="Conclusion L2.0")
    add_node("c_L2_1", "Conclusion", text="Conclusion L2.1")
    add_node("c_L2_2", "Conclusion", text="Conclusion L2.2")
    add_edge("c_L1_0", "c_L2_0", "LEADS_TO")
    add_edge("c_L1_0", "c_L2_1", "SUPPORTS")
    add_edge("c_L1_1", "c_L2_2", "LEADS_TO")
    # Cross-link from L1_0 to L2_2 (diamond)
    add_edge("c_L1_0", "c_L2_2", "SUPPORTS")

    # Level 3 (Decisions on branch 0, branch 1 terminates here)
    add_node("d_L3_0", "Conclusion", extra_labels=["Decision"], text="Decision L3.0", stale=False)
    add_node("d_L3_1", "Conclusion", extra_labels=["Decision"], text="Decision L3.1", stale=False)
    add_edge("c_L2_0", "d_L3_0", "LEADS_TO")
    add_edge("c_L2_1", "d_L3_1", "SUPPORTS")
    # Cross link d_L3_0 also supported by c_L2_1
    add_edge("c_L2_1", "d_L3_0", "SUPPORTS")

    # Level 4 Actions with different reversibility & executed states
    add_node("act_staged_rev", "Action", action_type="NOTIFY", executed=False, reversibility="REVERSIBLE")
    add_node("act_exec_comp", "Action", action_type="REFUND", executed=True, reversibility="COMPENSABLE")
    add_node("act_exec_irrev", "Action", action_type="WIRE", executed=True, reversibility="IRREVERSIBLE")
    add_node("act_exec_rev", "Action", action_type="FLAG", executed=True, reversibility="REVERSIBLE")

    add_edge("d_L3_0", "act_staged_rev", "LEADS_TO")
    add_edge("d_L3_0", "act_exec_comp", "LEADS_TO")
    add_edge("d_L3_1", "act_exec_irrev", "LEADS_TO")
    add_edge("c_L2_2", "act_exec_rev", "LEADS_TO")

    # Sample nodes to test blast_preview
    test_nodes = ["root_fact", "c_L1_0", "c_L1_1", "c_L2_0", "c_L2_2", "d_L3_0", "act_exec_comp", "nonexistent"]

    for nid in test_nodes:
        truth = compute_ground_truth(adjacency, node_meta, nid)
        preview = queries.blast_preview(tenant, nid)
        verify_preview_matches_ground_truth(preview, truth, nid)

    print("Test 1 (Fan-out 2 Asymmetric DAG): ALL blast_preview assertions PASSED.")


def test_asymmetric_dag_fanout_5():
    """
    Builds asymmetric DAG with fan-out 5:
    - Root fact branches out to 5 conclusions.
    - Intermediate nodes have variable fan-outs: some 5, some 2, some 1, some 0.
    - Several cross-linking edges to test DAG reconvergence and deduplication.
    - ~35 nodes total with diverse Action statuses.
    """
    tenant = f"{TENANT_PREFIX}_fo5"
    reset_graph(tenant)
    g = queries.get_graph(tenant, "commons")

    adjacency: Dict[str, List[str]] = {}
    node_meta: Dict[str, Dict[str, Any]] = {}

    def add_node(nid: str, ntype: str, extra_labels: List[str] = None, **props):
        lbls = [ntype, "MemoryNode"]
        if extra_labels:
            lbls.extend(extra_labels)
        label_str = ":" + ":".join(lbls)
        prop_parts = [f"id: '{nid}'", "status: 'HEALTHY'"]
        for k, v in props.items():
            if isinstance(v, bool):
                prop_parts.append(f"{k}: {'true' if v else 'false'}")
            elif isinstance(v, (int, float)):
                prop_parts.append(f"{k}: {v}")
            else:
                prop_parts.append(f"{k}: '{v}'")
        prop_str = ", ".join(prop_parts)
        g.query(f"CREATE ({label_str} {{{prop_str}}})")
        node_meta[nid] = {"type": ntype, **props}
        adjacency.setdefault(nid, [])

    def add_edge(src: str, dst: str, rel: str = "LEADS_TO"):
        g.query(f"""
        MATCH (a:MemoryNode {{id: '{src}'}}), (b:MemoryNode {{id: '{dst}'}})
        CREATE (a)-[:{rel}]->(b)
        """)
        adjacency[src].append(dst)

    # Root
    add_node("root_5", "Fact", text="Root Fact FO-5")

    # Level 1: 5 children
    for i in range(5):
        nid = f"c1_{i}"
        add_node(nid, "Conclusion", text=f"Conclusion L1.{i}")
        add_edge("root_5", nid, "LEADS_TO" if i % 2 == 0 else "SUPPORTS")

    # Level 2: Variable fan-out
    # c1_0 gets 5 children; c1_1 gets 2; c1_2 gets 1; c1_3 gets 3; c1_4 gets 0
    fan_outs = [5, 2, 1, 3, 0]
    l2_nodes = []
    for parent_idx, fo in enumerate(fan_outs):
        p_id = f"c1_{parent_idx}"
        for j in range(fo):
            child_id = f"c2_{parent_idx}_{j}"
            add_node(child_id, "Conclusion", text=f"Conclusion L2.{parent_idx}.{j}")
            add_edge(p_id, child_id, "LEADS_TO" if j % 2 == 0 else "SUPPORTS")
            l2_nodes.append(child_id)

    # Cross-links: connect c1_4 to child of c1_0, and c1_1 to child of c1_3
    add_edge("c1_4", "c2_0_0", "SUPPORTS")
    add_edge("c1_1", "c2_3_0", "LEADS_TO")

    # Level 3: Leaf Actions under selected Level 2 nodes
    actions_spec = [
        ("c2_0_0", "act_0_staged", False, "REVERSIBLE"),
        ("c2_0_1", "act_0_comp", True, "COMPENSABLE"),
        ("c2_0_2", "act_0_irrev", True, "IRREVERSIBLE"),
        ("c2_0_3", "act_0_rev", True, "REVERSIBLE"),
        ("c2_0_4", "act_0_staged2", False, "COMPENSABLE"),
        ("c2_1_0", "act_1_comp", True, "COMPENSABLE"),
        ("c2_2_0", "act_2_irrev", True, "IRREVERSIBLE"),
        ("c2_3_0", "act_3_staged", False, "REVERSIBLE"),
        ("c2_3_1", "act_3_comp", True, "COMPENSABLE"),
        ("c2_3_2", "act_3_irrev", True, "IRREVERSIBLE"),
    ]
    for parent_id, aid, exec_flag, rev in actions_spec:
        add_node(aid, "Action", action_type="CALL", executed=exec_flag, reversibility=rev)
        add_edge(parent_id, aid, "LEADS_TO")

    # Reconvergence cross-link: act_0_comp also supported by c2_1_1
    add_node("c2_1_1_placeholder", "Conclusion", text="Placeholder")
    add_edge("c1_1", "c2_1_1_placeholder", "SUPPORTS")
    add_edge("c2_1_1_placeholder", "act_0_comp", "SUPPORTS")

    # Verify preview for every node in the graph
    all_nids = list(node_meta.keys()) + ["ghost_node"]
    for nid in all_nids:
        truth = compute_ground_truth(adjacency, node_meta, nid)
        preview = queries.blast_preview(tenant, nid)
        verify_preview_matches_ground_truth(preview, truth, nid)

    print(f"Test 2 (Fan-out 5 Asymmetric DAG - {len(node_meta)} nodes): ALL blast_preview assertions PASSED.")


def test_asymmetric_dag_fanout_20():
    """
    Builds asymmetric DAG with fan-out 20:
    - Root fact branches to 20 intermediate nodes.
    - Two intermediate nodes branch to 20 leaves each (fan-out 20).
    - Other intermediate nodes have smaller or zero fan-out.
    - Total > 70 nodes.
    - Tests high fan-out deduplication, multi-path reconvergence, and scaling.
    """
    tenant = f"{TENANT_PREFIX}_fo20"
    reset_graph(tenant)
    g = queries.get_graph(tenant, "commons")

    adjacency: Dict[str, List[str]] = {}
    node_meta: Dict[str, Dict[str, Any]] = {}

    def add_node(nid: str, ntype: str, extra_labels: List[str] = None, **props):
        lbls = [ntype, "MemoryNode"]
        if extra_labels:
            lbls.extend(extra_labels)
        label_str = ":" + ":".join(lbls)
        prop_parts = [f"id: '{nid}'", "status: 'HEALTHY'"]
        for k, v in props.items():
            if isinstance(v, bool):
                prop_parts.append(f"{k}: {'true' if v else 'false'}")
            elif isinstance(v, (int, float)):
                prop_parts.append(f"{k}: {v}")
            else:
                prop_parts.append(f"{k}: '{v}'")
        prop_str = ", ".join(prop_parts)
        g.query(f"CREATE ({label_str} {{{prop_str}}})")
        node_meta[nid] = {"type": ntype, **props}
        adjacency.setdefault(nid, [])

    def add_edge(src: str, dst: str, rel: str = "LEADS_TO"):
        g.query(f"""
        MATCH (a:MemoryNode {{id: '{src}'}}), (b:MemoryNode {{id: '{dst}'}})
        CREATE (a)-[:{rel}]->(b)
        """)
        adjacency[src].append(dst)

    # Root
    add_node("root_20", "Fact", text="Root Fact FO-20")

    # Level 1: 20 children
    for i in range(20):
        nid = f"c1_{i:02d}"
        add_node(nid, "Conclusion", text=f"Conclusion L1.{i:02d}")
        add_edge("root_20", nid, "LEADS_TO" if i % 2 == 0 else "SUPPORTS")

    # Level 2 under c1_00: 20 actions (fan-out 20)
    for j in range(20):
        aid = f"act_00_{j:02d}"
        exec_flag = (j % 2 == 0)
        revs = ["REVERSIBLE", "COMPENSABLE", "IRREVERSIBLE"]
        rev = revs[j % 3]
        add_node(aid, "Action", action_type="TASK", executed=exec_flag, reversibility=rev)
        add_edge("c1_00", aid, "LEADS_TO")

    # Level 2 under c1_01: 20 conclusions (fan-out 20)
    for j in range(20):
        cid = f"c2_01_{j:02d}"
        add_node(cid, "Conclusion", text=f"Conclusion L2.01.{j:02d}")
        add_edge("c1_01", cid, "SUPPORTS")

    # Cross-links: connect c1_02..c1_05 to subsets of act_00_xx (reconvergent paths)
    for i in range(2, 6):
        add_edge(f"c1_{i:02d}", f"act_00_{i:02d}", "SUPPORTS")

    # Verify preview for root_20, c1_00, c1_01, cross-linked nodes, and leaves
    nodes_to_test = ["root_20", "c1_00", "c1_01", "c1_02", "c1_19", "act_00_00", "c2_01_10"]
    for nid in nodes_to_test:
        truth = compute_ground_truth(adjacency, node_meta, nid)
        preview = queries.blast_preview(tenant, nid)
        verify_preview_matches_ground_truth(preview, truth, nid)

    print(f"Test 3 (Fan-out 20 Asymmetric DAG - {len(node_meta)} nodes): ALL blast_preview assertions PASSED.")


# =========================================================================
# TEST 3: VERIFY BLAST_PREVIEW IS STRICTLY READ-ONLY (0 MUTATIONS)
# =========================================================================

def test_blast_preview_is_strictly_read_only():
    """
    3. Verify that blast_preview is read-only and causes 0 mutations or status changes to nodes.
    - Captures complete graph fingerprint before calling blast_preview.
    - Executes blast_preview across all nodes multiple times.
    - Captures graph fingerprint after.
    - Verifies 0 status changes, 0 property changes, 0 epoch bumps, 0 new nodes/edges.
    """
    tenant = f"{TENANT_PREFIX}_readonly"
    reset_graph(tenant)
    g = queries.get_graph(tenant, "commons")

    # Build a sample graph with 10 nodes of mixed types and statuses
    g.query("""
    CREATE (f1:Fact:MemoryNode {id: 'f_ro', status: 'HEALTHY', text: 'Fact RO', epoch: 1})
    CREATE (c1:Conclusion:MemoryNode {id: 'c_ro1', status: 'HEALTHY', text: 'C RO 1', stale: false})
    CREATE (c2:Conclusion:MemoryNode {id: 'c_ro2', status: 'HEALTHY', text: 'C RO 2', stale: false})
    CREATE (a1:Action:MemoryNode {id: 'a_ro1', status: 'HEALTHY', executed: false, reversibility: 'REVERSIBLE'})
    CREATE (a2:Action:MemoryNode {id: 'a_ro2', status: 'HEALTHY', executed: true, reversibility: 'COMPENSABLE'})
    CREATE (f1)-[:LEADS_TO]->(c1)
    CREATE (f1)-[:SUPPORTS]->(c2)
    CREATE (c1)-[:LEADS_TO]->(a1)
    CREATE (c2)-[:LEADS_TO]->(a2)
    """)

    def get_graph_state():
        res_nodes = g.query("MATCH (m:MemoryNode) RETURN m.id, m.status, labels(m), properties(m) ORDER BY m.id")
        res_edges = g.query("MATCH (a)-[r]->(b) RETURN a.id, type(r), b.id ORDER BY a.id, b.id")
        res_meta = g.query("MATCH (m:Meta {id: 'meta'}) RETURN m.epoch")
        epoch = res_meta.result_set[0][0] if res_meta.result_set else None
        return {
            "nodes": res_nodes.result_set,
            "edges": res_edges.result_set,
            "epoch": epoch,
        }

    before_state = get_graph_state()
    initial_epoch = before_state["epoch"]
    assert len(before_state["nodes"]) == 5
    assert len(before_state["edges"]) == 4

    # Run blast_preview 50 times across all nodes and nonexistent nodes
    test_ids = ["f_ro", "c_ro1", "c_ro2", "a_ro1", "a_ro2", "ghost_1", "ghost_2"]
    for _ in range(10):
        for nid in test_ids:
            queries.blast_preview(tenant, nid)

    after_state = get_graph_state()

    # Invariant assertions:
    assert before_state["epoch"] == after_state["epoch"], (
        f"Meta.epoch changed! Before: {before_state['epoch']}, After: {after_state['epoch']}"
    )
    assert len(before_state["nodes"]) == len(after_state["nodes"]), (
        f"Node count changed! Before: {len(before_state['nodes'])}, After: {len(after_state['nodes'])}"
    )
    assert len(before_state["edges"]) == len(after_state["edges"]), (
        f"Edge count changed! Before: {len(before_state['edges'])}, After: {len(after_state['edges'])}"
    )
    assert before_state["nodes"] == after_state["nodes"], "Node properties or statuses mutated during blast_preview!"
    assert before_state["edges"] == after_state["edges"], "Edges mutated during blast_preview!"

    # Invariants check
    inv = queries.invariant_check(tenant)
    assert inv["all_passed"] is True, f"Invariant check failed after blast_preview: {inv}"

    print("Test 3 (Strict Read-Only Verification): 0 mutations, 0 epoch bumps, 0 status changes. PASSED.")


# =========================================================================
# TEST 4: CONCURRENT BLAST_PREVIEW ON 100 NODES ACROSS 10 THREADS UNDER WRITES
# =========================================================================

def test_concurrent_blast_preview_under_writes():
    """
    4. Concurrently run blast_preview on 100 nodes across 10 threads while writes occur.
    - Seeds a graph with >= 100 nodes.
    - Spawns 10 reader threads running blast_preview concurrently on 100 distinct nodes.
    - Simultaneously spawns 3 writer threads performing concurrent graph additions/mutations.
    - Monitors latency metrics, error counts, and invariant preservation.
    """
    tenant = f"{TENANT_PREFIX}_concurrent"
    reset_graph(tenant)
    g = queries.get_graph(tenant, "commons")

    # Seed 100 nodes in graph: 1 root fact -> 10 conclusions -> each has 9 actions (total = 1 + 10 + 90 = 101 nodes)
    g.query("CREATE (:Fact:MemoryNode {id: 'seed_root', status: 'HEALTHY', text: 'Root Fact'})")
    all_node_ids = ["seed_root"]

    for i in range(10):
        cid = f"seed_c_{i}"
        g.query(f"""
        MATCH (r:MemoryNode {{id: 'seed_root'}})
        CREATE (c:Conclusion:MemoryNode {{id: '{cid}', status: 'HEALTHY', text: 'C {i}'}})
        CREATE (r)-[:LEADS_TO]->(c)
        """)
        all_node_ids.append(cid)
        for j in range(9):
            aid = f"seed_a_{i}_{j}"
            exec_flag = "true" if j % 2 == 0 else "false"
            rev = "REVERSIBLE" if j % 3 == 0 else ("COMPENSABLE" if j % 3 == 1 else "IRREVERSIBLE")
            g.query(f"""
            MATCH (c:MemoryNode {{id: '{cid}'}})
            CREATE (a:Action:MemoryNode {{id: '{aid}', status: 'HEALTHY', executed: {exec_flag}, reversibility: '{rev}'}})
            CREATE (c)-[:LEADS_TO]->(a)
            """)
            all_node_ids.append(aid)

    assert len(all_node_ids) == 101
    target_100_nodes = all_node_ids[:100]

    stop_event = threading.Event()
    writer_errors: List[str] = []
    reader_errors: List[str] = []
    latencies: List[float] = []
    latency_lock = threading.Lock()

    # Writer Worker
    def writer_worker(worker_id: int):
        counter = 0
        while not stop_event.is_set():
            counter += 1
            new_cid = f"dyn_c_{worker_id}_{counter}"
            try:
                # Dynamically derive new conclusions under random existing conclusion
                parent = f"seed_c_{random.randint(0, 9)}"
                g.query(f"""
                MATCH (p:MemoryNode {{id: '{parent}'}})
                CREATE (c:Conclusion:MemoryNode {{id: '{new_cid}', status: 'HEALTHY', text: 'Dynamic {worker_id}-{counter}'}})
                CREATE (p)-[:SUPPORTS]->(c)
                """)
                time.sleep(0.005)
            except Exception as e:
                writer_errors.append(f"Writer {worker_id}: {str(e)}")

    # Reader Worker
    def reader_worker(thread_id: int, assigned_nodes: List[str]):
        local_lats = []
        try:
            for nid in assigned_nodes:
                t0 = time.time()
                res = queries.blast_preview(tenant, nid)
                dt_ms = (time.time() - t0) * 1000
                local_lats.append(dt_ms)

                # Consistency check
                if nid in all_node_ids:
                    assert res["root_id"] == nid
                    assert len(res["node_ids"]) >= 1, f"Expected at least self in preview for {nid}"
                    assert res["impacted_count"] == len(res["node_ids"])
                    action_sum = (
                        res["actions"]["staged"]
                        + res["actions"]["executed_reversible"]
                        + res["actions"]["executed_compensable"]
                        + res["actions"]["executed_irreversible"]
                    )
                    assert res["impacted_count"] == res["facts"] + res["conclusions"] + action_sum, (
                        f"Inconsistent count breakdown for {nid}: {res}"
                    )
        except Exception as e:
            reader_errors.append(f"Reader {thread_id}: {str(e)}")
        finally:
            with latency_lock:
                latencies.extend(local_lats)

    # Spawn 3 writer threads
    writer_threads = [threading.Thread(target=writer_worker, args=(w,)) for w in range(3)]
    for wt in writer_threads:
        wt.start()

    # Partition 100 nodes across 10 reader threads (10 nodes per thread)
    chunks = [target_100_nodes[i * 10 : (i + 1) * 10] for i in range(10)]
    reader_threads = [threading.Thread(target=reader_worker, args=(i, chunks[i])) for i in range(10)]

    t_start = time.time()
    for rt in reader_threads:
        rt.start()

    for rt in reader_threads:
        rt.join()

    total_query_time = time.time() - t_start

    # Signal writers to stop
    stop_event.set()
    for wt in writer_threads:
        wt.join()

    # Latency statistics
    latencies.sort()
    n_queries = len(latencies)
    avg_lat = sum(latencies) / n_queries if n_queries else 0.0
    p50 = latencies[int(n_queries * 0.50)] if n_queries else 0.0
    p95 = latencies[int(n_queries * 0.95)] if n_queries else 0.0
    p99 = latencies[int(n_queries * 0.99)] if n_queries else 0.0
    max_lat = max(latencies) if n_queries else 0.0

    print("\n--- Concurrent Blast Preview Benchmark (100 nodes, 10 threads) ---")
    print(f"Total blast_preview queries completed: {n_queries}")
    print(f"Total time: {total_query_time:.3f} s")
    print(f"Throughput: {n_queries / total_query_time:.1f} queries/sec")
    print(f"Latency Avg: {avg_lat:.2f} ms")
    print(f"Latency P50: {p50:.2f} ms")
    print(f"Latency P95: {p95:.2f} ms")
    print(f"Latency P99: {p99:.2f} ms")
    print(f"Latency Max: {max_lat:.2f} ms")
    print(f"Reader errors: {len(reader_errors)}")
    print(f"Writer errors: {len(writer_errors)}")

    assert len(reader_errors) == 0, f"Reader errors encountered: {reader_errors}"
    assert len(writer_errors) == 0, f"Writer errors encountered: {writer_errors}"
    assert n_queries == 100, f"Expected 100 queries, got {n_queries}"

    # Verify invariants post concurrent execution
    inv = queries.invariant_check(tenant)
    assert inv["all_passed"] is True, f"Invariant check failed: {inv}"
    print("Test 4 (Concurrent 10-Thread Execution): PASSED with 0 errors.")


if __name__ == "__main__":
    test_asymmetric_dag_fanout_2()
    test_asymmetric_dag_fanout_5()
    test_asymmetric_dag_fanout_20()
    test_blast_preview_is_strictly_read_only()
    test_concurrent_blast_preview_under_writes()
    print("\n=======================================================")
    print("ALL 5 BLAST PREVIEW STRESS TESTS COMPLETED SUCCESSFULLY!")
    print("=======================================================")
