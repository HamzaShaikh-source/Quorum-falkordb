"""
The cascade on a real graph we did not generate: this project's own Python import graph.

Nodes are the backend modules; an edge B -> A means "A imports B", so a wrong belief about B (a changed module, a withdrawn
fact) must ripple to every module that depends on it, directly or transitively. The ground truth is computed independently
from the source with `ast`; the answer under test is the FalkorDB traversal the quarantine cascade uses. For every module as a
root we compare the two: ripple recall and precision, and the time FalkorDB takes. The import graph has real depth and shared
dependencies, none of which we chose; edges that would close a cycle are refused by the store (causal links must form a DAG) and counted.

    python bench/real_graph_ripple.py --output bench/real_graph_ripple_results.json
"""
import argparse
import ast
import json
import os
import statistics
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "backend"), str(ROOT)]
os.environ.setdefault("QUORUM_ENV", "development")
os.environ.setdefault("QUORUM_PERSISTENCE", "falkordb")

CAUSAL = ("LEADS_TO",)


def import_graph(pkg_dir: Path, pkg: str = "app"):
    mods = {}
    for p in pkg_dir.rglob("*.py"):
        rel = p.relative_to(pkg_dir.parent).with_suffix("")
        name = ".".join(rel.parts)
        mods[name[: -len(".__init__")] if name.endswith(".__init__") else name] = p
    edges = set()
    for name, p in mods.items():
        tree = ast.parse(p.read_text(encoding="utf8"))
        for node in ast.walk(tree):
            targets = []
            if isinstance(node, ast.Import):
                targets = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                targets = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
            for t in targets:
                if t in mods and t != name:
                    edges.add((t, name))  # name depends on t: a change in t flows to name
    return sorted(mods), sorted(edges)


def oracle(edges, root):
    out, stack = set(), [root]
    adj = {}
    for a, b in edges:
        adj.setdefault(a, []).append(b)
    while stack:
        for n in adj.get(stack.pop(), []):
            if n not in out and n != root:
                out.add(n)
                stack.append(n)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", type=Path, default=ROOT / "bench" / "real_graph_ripple_results.json")
    a = ap.parse_args()
    from app import db
    from app.graph.queries import forget_stores, get_graph_store
    from fastapi import HTTPException
    from app.models.common import EdgeType, NodeKind, Status
    from app.models.graph import GraphEdge, GraphNode

    mods, edges = import_graph(ROOT / "backend" / "app")
    tenant = f"realg_{uuid.uuid4().hex[:8]}"
    forget_stores()
    store = get_graph_store(tenant)
    try:
        for m in mods:
            store.add_node(GraphNode(id=m, kind=NodeKind.FACT, label=m, status=Status.HEALTHY, stale=False, text=m))
        accepted, skipped = [], 0
        for i, (x, y) in enumerate(edges):
            try:
                store.add_edge(GraphEdge(id=f"e{i}", source=x, target=y, type=EdgeType.LEADS_TO))
                accepted.append((x, y))
            except HTTPException:  # the store refuses an edge that would close a cycle: causal links must form a DAG
                skipped += 1
        edges = accepted
        store.commit()
        rows, ms = [], []
        tp = fp = fn = 0
        for m in mods:
            truth = oracle(edges, m)
            t0 = time.perf_counter()
            got = store.closure_in_falkordb(m, CAUSAL, "out")
            ms.append((time.perf_counter() - t0) * 1000)
            got = set(got or {})
            tp += len(truth & got)
            fp += len(got - truth)
            fn += len(truth - got)
            rows.append({"root": m, "ripple": len(truth), "found": len(got)})
        deepest = max(rows, key=lambda r: r["ripple"])
        summary = {
            "modules": len(mods), "import_edges": len(edges), "cycle_closing_edges_refused": skipped, "roots_checked": len(rows),
            "ripple_recall": round(tp / (tp + fn), 4) if tp + fn else 1.0, "ripple_precision": round(tp / (tp + fp), 4) if tp + fp else 1.0,
            "mismatches": fp + fn, "widest_ripple": f"{deepest['root']} -> {deepest['ripple']} modules",
            "ms_median": round(statistics.median(ms), 2), "ms_p99": round(sorted(ms)[int(len(ms) * 0.99) - 1], 2),
            "engine": "falkordb",
        }
        out = {"params": {"script": "bench/real_graph_ripple.py", "source": "backend/app import graph (ast)"}, "summary": summary,
               "limits": "A real graph, but a small one, and 'depends on' is not the same as 'derived from a belief'. It validates the traversal, not belief semantics."}
        a.output.write_text(json.dumps(out, indent=2) + "\n", encoding="utf8")
        print(json.dumps(summary, indent=2))
        return 0 if summary["mismatches"] == 0 else 1
    finally:
        forget_stores()
        try:
            db.graph_for(tenant).delete()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    sys.exit(main())
