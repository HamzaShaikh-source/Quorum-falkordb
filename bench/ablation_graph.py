"""
Does the graph change the outcome? A like-for-like ablation on 24 generated poisoned-fact scenarios.

Both arms hold the SAME nodes (facts, conclusions, staged actions) and receive the SAME correction ("fact F0 is wrong"):

  flat     memory without provenance: the nodes exist but no causal edges connect them (a vector/flat store). The wrong fact is
           flagged; nothing else knows it was built on F0.
  quorum   the same nodes with their LEADS_TO/SUPPORTS edges in FalkorDB. The correction runs the quarantine cascade (Cypher
           in FalkorDB), then every staged action goes through the execute guard.

Per scenario we count the staged actions that were really built on the wrong fact ("at risk") and how many of them were let
through. We also count independent actions blocked by mistake, and conclusions built on F0 that stay retrievable.

Limits, stated plainly: the scenarios are generated (seeded, reproducible), the "flat" arm is defined as "no edges", and the
numbers say how provenance changes the outcome, not how Quorum compares with any particular product.

    python bench/ablation_graph.py --scenarios 24 --seed 7 --output bench/ablation_results.json
"""
import argparse
import json
import os
import random
import statistics
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "backend"), str(ROOT)]
os.environ.setdefault("QUORUM_ENV", "development")
os.environ.setdefault("QUORUM_DEMO", "0")
os.environ.setdefault("QUORUM_PERSISTENCE", "falkordb")


def build(store, spec, with_edges):
    from app.models.common import EdgeType, GateStatus, NodeKind, Reversibility, Status
    from app.models.graph import ActionInfo, GraphEdge, GraphNode

    for nid, kind in spec["nodes"]:
        if kind == "action":
            store.add_node(GraphNode(id=nid, kind=NodeKind.ACTION, label=f"Refund {nid}", status=Status.HEALTHY,
                                     action=ActionInfo(type="refund", reversibility=Reversibility.REVERSIBLE, gate=GateStatus.PASSED)))
        else:
            k = NodeKind.FACT if kind == "fact" else NodeKind.CONCLUSION
            store.add_node(GraphNode(id=nid, kind=k, label=nid, status=Status.HEALTHY, stale=False, text=f"{nid} statement"))
    if with_edges:
        for i, (a, b) in enumerate(spec["edges"]):
            store.add_edge(GraphEdge(id=f"e{i}", source=a, target=b, type=EdgeType.LEADS_TO))
    store.commit()


def scenario(seed):
    rng = random.Random(seed)
    nf, nc, na = rng.randint(6, 12), rng.randint(10, 30), rng.randint(4, 8)
    nodes = [(f"f{i}", "fact") for i in range(nf)] + [(f"c{i}", "concl") for i in range(nc)] + [(f"a{i}", "action") for i in range(na)]
    edges = []
    base = [n for n, _ in nodes if n.startswith("f")]
    for i in range(nc):
        parents = rng.sample(base + [f"c{j}" for j in range(i)], min(len(base) + i, rng.randint(1, 3)))
        edges += [(p, f"c{i}") for p in parents]
    pool = base + [f"c{j}" for j in range(nc)]
    for i in range(na):
        edges += [(p, f"a{i}") for p in rng.sample(pool, rng.randint(1, 3))]
    return {"nodes": nodes, "edges": edges}


def reachable(spec, root):
    out, stack, seen = {}, [root], {root}
    adj = {}
    for a, b in spec["edges"]:
        adj.setdefault(a, []).append(b)
    while stack:
        cur = stack.pop()
        for nxt in adj.get(cur, []):
            if nxt not in seen:
                seen.add(nxt)
                out[nxt] = True
                stack.append(nxt)
    return set(out)


def run_arm(tenant, spec, with_edges):
    from app import db
    from app.graph.queries import forget_stores, get_graph_store
    from app.models.common import NodeKind, Status

    forget_stores()
    store = get_graph_store(tenant)
    try:
        build(store, spec, with_edges)
        t0 = time.perf_counter()
        affected = store.cascade_quarantine("f0")  # the correction: flag F0 (with edges this cascades in FalkorDB)
        ms = (time.perf_counter() - t0) * 1000
        executed = [n.id for n in store.nodes.values() if n.kind == NodeKind.ACTION and store.execute_guard(n.id)]
        stale_retrievable = [n.id for n in store.nodes.values() if n.kind == NodeKind.CONCLUSION and n.status == Status.HEALTHY]
        return {"executed": set(executed), "healthy_conclusions": set(stale_retrievable), "ms": ms, "affected": len(affected), "engine": store.last_cascade_engine}
    finally:
        forget_stores()
        try:
            db.graph_for(tenant).delete()
        except Exception:  # noqa: BLE001
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenarios", type=int, default=24)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--output", type=Path, default=ROOT / "bench" / "ablation_results.json")
    a = ap.parse_args()
    rows = []
    for i in range(a.scenarios):
        spec = scenario(a.seed * 1000 + i)
        risk = {n for n in reachable(spec, "f0") if n.startswith("a")}
        safe_actions = {n for n, k in spec["nodes"] if k == "action"} - risk
        tainted_concl = {n for n in reachable(spec, "f0") if n.startswith("c")}
        tag = uuid.uuid4().hex[:8]
        flat = run_arm(f"abl_flat_{tag}", spec, False)
        quorum = run_arm(f"abl_q_{tag}", spec, True)
        rows.append({
            "scenario": i, "nodes": len(spec["nodes"]), "edges": len(spec["edges"]), "actions_at_risk": len(risk),
            "flat_leaked": len(flat["executed"] & risk), "quorum_leaked": len(quorum["executed"] & risk),
            "quorum_blocked_independent": len(safe_actions - quorum["executed"]),
            "flat_tainted_conclusions_retrievable": len(flat["healthy_conclusions"] & tainted_concl),
            "quorum_tainted_conclusions_retrievable": len(quorum["healthy_conclusions"] & tainted_concl),
            "cascade_ms": round(quorum["ms"], 2), "cascade_engine": quorum["engine"],
        })
        print(f"scenario {i:2d}: at risk {len(risk):2d}  flat let through {rows[-1]['flat_leaked']:2d}  quorum let through {rows[-1]['quorum_leaked']}  engine {quorum['engine']}")
    tot = lambda k: sum(r[k] for r in rows)  # noqa: E731
    summary = {
        "scenarios": len(rows), "actions_at_risk": tot("actions_at_risk"),
        "flat_leaked": tot("flat_leaked"), "quorum_leaked": tot("quorum_leaked"),
        "quorum_blocked_independent_actions": tot("quorum_blocked_independent"),
        "flat_tainted_conclusions_still_retrievable": tot("flat_tainted_conclusions_retrievable"),
        "quorum_tainted_conclusions_still_retrievable": tot("quorum_tainted_conclusions_retrievable"),
        "cascade_ms_median": round(statistics.median(r["cascade_ms"] for r in rows), 2),
        "cascade_ms_max": round(max(r["cascade_ms"] for r in rows), 2),
        "cascade_engine": sorted({r["cascade_engine"] for r in rows}),
    }
    out = {"params": {"scenarios": a.scenarios, "seed": a.seed, "script": "bench/ablation_graph.py"}, "summary": summary, "rows": rows,
           "limits": "Generated scenarios; the flat arm is defined as the same nodes without causal edges; cascade_ms is end to end through FalkorDB on this machine."}
    a.output.write_text(json.dumps(out, indent=2) + "\n", encoding="utf8")
    print(json.dumps(summary, indent=2))
    return 0 if summary["quorum_leaked"] == 0 and summary["quorum_blocked_independent_actions"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
