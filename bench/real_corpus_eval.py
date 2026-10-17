"""
A deterministic evaluation on a REAL corpus we did not write: the Python packages installed in this environment.

Source material is each package's own METADATA (name, version, Requires-Dist), several hundred packages and thousands of real
dependency lines. Every fact keeps its source (`metadata:<pkg>-<version>`) and the raw line it came from as its quote. Conclusions
("A is compatible with the installed B", "A is installable") are derived through the graph, so a wrong version fact has a real
blast radius: everything that transitively depends on that package.

Three things are measured, with no LLM judge (ground truth comes from the metadata itself):

  1. Answers and citations: "which version of P is installed?" and "which versions of D does P accept?" against two retrievers
     over the same facts: a plain TF-IDF search (top 8) and Quorum's retrieve_context (top 8). A hit needs the right value and the
     right source line.
  2. Abstention: questions about packages that do not exist. Does retrieval refuse, or hand back something?
  2b. A multi-hop question: "which packages are affected if the version fact of P is wrong?" Quorum answers from the graph (the blast-radius
     tool); plain search can only read the direct "X requires P" lines it happens to retrieve.
  3. Change propagation: withdraw one package's version fact and quarantine through FalkorDB. Ripple recall and precision are
     compared with the reverse-dependency closure computed independently at package level; a flat store with no edges is shown too.

Limits, stated plainly: the corpus is whatever is installed where you run it (the counts are recorded in the output); questions
are generated from the metadata by a seeded script, not written by people; "installable" and "compatible" are conclusions we
define, so propagation validates the traversal on a real dependency structure, not any claim about pip.

    python bench/real_corpus_eval.py --questions 120 --ripple 40 --seed 7 --output bench/real_corpus_results.json
"""
import argparse
import json
import math
import os
import random
import re
import statistics
import sys
import time
import uuid
from collections import Counter
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "backend"), str(ROOT)]
os.environ.setdefault("QUORUM_ENV", "development")
os.environ.setdefault("QUORUM_PERSISTENCE", "falkordb")
os.environ.setdefault("QUORUM_RATE_LIMIT_TOOLS_PER_MIN", "0")

K = 8


def canon(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def load_corpus():
    from packaging.requirements import Requirement

    pkgs = {}
    for d in metadata.distributions():
        n = d.metadata["Name"]
        if not n:
            continue
        c = canon(n)
        if c in pkgs:
            continue
        pkgs[c] = {"name": c, "version": d.version, "reqs": []}
        for line in d.requires or []:
            try:
                r = Requirement(line)
            except Exception:  # noqa: BLE001
                continue
            if r.marker is not None and "extra" in str(r.marker):
                continue
            pkgs[c]["reqs"].append({"dep": canon(r.name), "spec": str(r.specifier) or "(any)", "raw": line})
    for p in pkgs.values():
        seen, keep = set(), []
        for r in p["reqs"]:
            if r["dep"] in pkgs and r["dep"] != p["name"] and r["dep"] not in seen:
                seen.add(r["dep"])
                keep.append(r)
        p["reqs"] = keep
    return pkgs


def break_cycles(pkgs):
    """Real dependency graphs have cycles; the store (rightly) keeps causal links acyclic. Drop DFS back-edges, deterministically."""
    state, dropped = {}, 0
    sys.setrecursionlimit(10000)

    def visit(n):
        nonlocal dropped
        state[n] = 1
        keep = []
        for r in pkgs[n]["reqs"]:
            s = state.get(r["dep"], 0)
            if s == 1:
                dropped += 1
                continue
            if s == 0:
                visit(r["dep"])
            keep.append(r)
        pkgs[n]["reqs"] = keep
        state[n] = 2

    for n in sorted(pkgs):
        if state.get(n, 0) == 0:
            visit(n)
    return dropped


def topo(pkgs):
    order, seen = [], set()

    def visit(n):
        if n in seen:
            return
        seen.add(n)
        for r in pkgs[n]["reqs"]:
            visit(r["dep"])
        order.append(n)

    for n in sorted(pkgs):
        visit(n)
    return order


def rev_closure(pkgs, root):
    rev = {}
    for n, p in pkgs.items():
        for r in p["reqs"]:
            rev.setdefault(r["dep"], []).append(n)
    out, stack = set(), [root]
    while stack:
        for x in rev.get(stack.pop(), []):
            if x not in out:
                out.add(x)
                stack.append(x)
    return out


def expected_ripple(pkgs, root):
    """
    Package-level ground truth for "withdraw the version fact of root": the node ids that must be quarantined, from the definition of
    the conclusions. c:X>D (X works with D) is built from v:D, X's requirement line and i:D; i:X (X is installable) is built from X's own
    c:X>* conclusions, or from v:X when X has no dependencies. So i:X is downstream of v:root for every package X that transitively
    depends on root, and for root itself only if root has no dependencies. (A first version of this oracle counted i:root always;
    comparing it with the traversal exposed that as an oracle bug, not an engine bug.)
    """
    rev = rev_closure(pkgs, root)
    affected = {root} | rev
    ids = {f"i:{x}" for x in rev}
    if not pkgs[root]["reqs"]:
        ids.add(f"i:{root}")
    for x, p in pkgs.items():
        for r in p["reqs"]:
            if r["dep"] in affected:
                ids.add(f"c:{x}>{r['dep']}")
    return ids


class Tfidf:
    """The plain-search baseline: TF-IDF cosine over the same fact texts, no graph, no provenance."""

    def __init__(self, docs):
        self.ids = list(docs)
        self.tok = {i: Counter(re.findall(r"[a-z0-9]+", docs[i].lower())) for i in self.ids}
        df = Counter(t for c in self.tok.values() for t in c)
        n = len(self.ids)
        self.idf = {t: math.log((n + 1) / (v + 1)) + 1 for t, v in df.items()}
        self.norm = {i: math.sqrt(sum((c[t] * self.idf[t]) ** 2 for t in c)) or 1.0 for i, c in self.tok.items()}

    def top(self, q, k=K):
        qt = Counter(re.findall(r"[a-z0-9]+", q.lower()))
        scored = []
        for i in self.ids:
            dot = sum(qt[t] * self.idf.get(t, 0) * self.tok[i][t] * self.idf[t] for t in qt if t in self.tok[i])
            if dot > 0:
                scored.append((dot / self.norm[i], i))
        return [i for _, i in sorted(scored, reverse=True)[:k]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", type=int, default=120)
    ap.add_argument("--ripple", type=int, default=40)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--output", type=Path, default=ROOT / "bench" / "real_corpus_results.json")
    a = ap.parse_args()

    from app import db
    from app.graph.queries import forget_stores, get_graph_store
    from app.models.common import EdgeType, NodeKind, Status
    from app.models.graph import GraphEdge, GraphNode
    from app.services import mcp_read

    rng = random.Random(a.seed)
    pkgs = load_corpus()
    dropped = break_cycles(pkgs)
    order = topo(pkgs)
    tenant = f"realc{uuid.uuid4().hex[:7]}"
    token = f"token_{tenant}_agent"
    forget_stores()
    store = get_graph_store(tenant)
    texts, facts = {}, {}
    n_edges = 0
    try:
        t0 = time.perf_counter()
        for n in order:
            p = pkgs[n]
            vid = f"v:{n}"
            vt = f"{n} version {p['version']}"
            node = GraphNode(id=vid, kind=NodeKind.FACT, label=vt[:60], status=Status.HEALTHY, stale=False, key=f"{n}.version", value=p["version"], text=vt,
                             quote=f"Version: {p['version']}", origin=f"metadata:{n}-{p['version']}", valid_from=1)
            store.add_node(node)
            texts[vid], facts[vid] = vt, node
            compat_ids = []
            for r in p["reqs"]:
                rid = f"r:{n}>{r['dep']}"
                rt = f"{n} requires {r['dep']} {r['spec']}"
                store.add_node(GraphNode(id=rid, kind=NodeKind.FACT, label=rt[:60], status=Status.HEALTHY, stale=False, key=f"{n}.requires.{r['dep']}", value=r["spec"], text=rt,
                                         quote=r["raw"], origin=f"metadata:{n}-{p['version']}", valid_from=1))
                texts[rid] = rt
                cid = f"c:{n}>{r['dep']}"
                store.add_node(GraphNode(id=cid, kind=NodeKind.CONCLUSION, label=f"{n} works with {r['dep']}"[:60], status=Status.HEALTHY, stale=False,
                                         text=f"{n} is compatible with the installed {r['dep']} {pkgs[r['dep']]['version']}", valid_from=1))
                for src in (f"v:{r['dep']}", rid, f"i:{r['dep']}"):
                    store.add_edge(GraphEdge(id=f"e{n_edges}", source=src, target=cid, type=EdgeType.LEADS_TO))
                    n_edges += 1
                compat_ids.append(cid)
            iid = f"i:{n}"
            store.add_node(GraphNode(id=iid, kind=NodeKind.CONCLUSION, label=f"{n} is installable"[:60], status=Status.HEALTHY, stale=False, text=f"{n} is installable", valid_from=1))
            for src in compat_ids or [vid]:
                store.add_edge(GraphEdge(id=f"e{n_edges}", source=src, target=iid, type=EdgeType.LEADS_TO))
                n_edges += 1
        store.commit()
        build_s = time.perf_counter() - t0

        # ---- 1 and 2: answers, citations, abstention ----
        base = Tfidf({i: t for i, t in texts.items()})
        names = sorted(pkgs)
        with_deps = [n for n in names if pkgs[n]["reqs"]]
        qs = []
        for n in rng.sample(names, min(a.questions * 5 // 12, len(names))):
            qs.append({"kind": "version", "q": f"Which version of {n} is installed?", "pkg": n, "want_key": f"{n}.version", "want_value": pkgs[n]["version"], "want_origin": f"metadata:{n}-{pkgs[n]['version']}"})
        for n in rng.sample(with_deps, min(a.questions * 5 // 12, len(with_deps))):
            r = rng.choice(pkgs[n]["reqs"])
            qs.append({"kind": "requirement", "q": f"Which versions of {r['dep']} does {n} accept?", "pkg": n, "want_quote": r["raw"], "want_origin": f"metadata:{n}-{pkgs[n]['version']}"})
        words = ["zorblax", "quantifly", "nebulon", "vortexa", "plinth", "grimble", "snarfle", "wibbleton", "frobnic", "yeldrix", "krumble", "oxidane"]
        for i in range(a.questions - len(qs)):
            fake = f"{rng.choice(words)}-{rng.choice(words)}{rng.randint(10, 99)}"
            qs.append({"kind": "abstain", "q": f"Which version of {fake} is installed?", "pkg": fake})

        def hit(item, q):
            if q["kind"] == "version":
                return item.get("key") == q["want_key"] and item.get("value") == q["want_value"]
            if q["kind"] == "requirement":
                return item.get("quote") == q["want_quote"]
            return False

        res = {"quorum": Counter(), "plain": Counter()}
        lat = {"quorum": [], "plain": []}
        for q in qs:
            t1 = time.perf_counter()
            out = mcp_read.retrieve_context(q["q"], token, k=K, format="full")
            lat["quorum"].append((time.perf_counter() - t1) * 1000)
            items = out.get("context", [])
            t2 = time.perf_counter()
            plain_ids = base.top(q["q"])
            lat["plain"].append((time.perf_counter() - t2) * 1000)
            plain_items = [{"key": facts[i].key if i in facts else None, "value": facts[i].value if i in facts else None, "quote": getattr(store.nodes[i], "quote", None), "origin": getattr(store.nodes[i], "origin", None)} for i in plain_ids if i in store.nodes]
            quorum_items = []
            for it in items:
                nd = store.nodes.get(it["id"])
                quorum_items.append({**it, "origin": getattr(nd, "origin", None)})
            for arm, its in (("quorum", quorum_items), ("plain", plain_items)):
                c = res[arm]
                c[f"{q['kind']}_n"] += 1
                if q["kind"] == "abstain":
                    c["abstain_ok"] += int(len(its) == 0)
                else:
                    good = [x for x in its if hit(x, q)]
                    c[f"{q['kind']}_correct"] += int(bool(good))
                    c["cited_correctly"] += int(any(x.get("origin") == q["want_origin"] for x in good))
                    c["answered"] += 1

        def pct(c, num, den):
            return round(100 * c[num] / c[den], 1) if c[den] else None

        answers = {}
        for arm in ("plain", "quorum"):
            c = res[arm]
            answers[arm] = {
                "version_correct_pct": pct(c, "version_correct", "version_n"), "requirement_correct_pct": pct(c, "requirement_correct", "requirement_n"),
                "cited_to_the_right_metadata_pct": round(100 * c["cited_correctly"] / max(1, c["version_correct"] + c["requirement_correct"]), 1),
                "abstained_on_nonexistent_pct": pct(c, "abstain_ok", "abstain_n"),
                "median_ms": round(statistics.median(lat[arm]), 2),
            }

        # ---- 2b: a multi-hop question: "which packages are affected if the version fact of P is wrong?" ----
        from app.services import mcp_admin

        impact_pool = [n for n in names if 2 <= len({i for i in expected_ripple(pkgs, n) if i.startswith("i:")}) <= 80]
        impact_roots = rng.sample(impact_pool, min(30, len(impact_pool)))
        imp = {"quorum": [], "plain": []}
        for root in impact_roots:
            truth = {i[2:] for i in expected_ripple(pkgs, root) if i.startswith("i:")}  # packages whose installable conclusion rests on this fact
            r = mcp_admin.explain_blast_radius(f"v:{root}", token)
            got_q = {i[2:] for i in r.get("predicted_impact_nodes", []) if i.startswith("i:")}
            top = base.top(f"Which packages depend on {root}?")
            got_p = set()
            for i in top:
                t = texts[i]
                if " requires " in t and t.split(" requires ")[1].split(" ")[0] == root:
                    got_p.add(t.split(" requires ")[0])
            for arm, got in (("quorum", got_q), ("plain", got_p)):
                imp[arm].append((len(truth & got) / len(truth), got == truth, len(got - truth)))
        impact = {arm: {"questions": len(v), "mean_recall_pct": round(100 * statistics.mean(x[0] for x in v), 1), "exact_set_pct": round(100 * sum(x[1] for x in v) / len(v), 1), "wrong_packages_named": sum(x[2] for x in v)} for arm, v in imp.items()}

        # ---- 3: change propagation ----
        roots = rng.sample([n for n in names if rev_closure(pkgs, n)], min(a.ripple, sum(1 for n in names if rev_closure(pkgs, n))))
        tp = fp = fn = 0
        flat_tp = flat_fn = 0
        ms = []
        widest = (None, 0)
        for root in roots:
            exp = expected_ripple(pkgs, root)
            s2 = store
            before = {i: n.status for i, n in s2.nodes.items()}
            t3 = time.perf_counter()
            affected = {x["id"] for x in s2.cascade_quarantine(f"v:{root}")}
            ms.append((time.perf_counter() - t3) * 1000)
            got = {i for i in affected if i != f"v:{root}"}
            tp += len(exp & got)
            fp += len(got - exp)
            fn += len(exp - got)
            flat_fn += len(exp)  # a flat store flags the withdrawn fact itself and nothing built on it
            if len(exp) > widest[1]:
                widest = (root, len(exp))
            for i, n in s2.nodes.items():  # restore for the next root, in memory and in FalkorDB
                n.status = before[i]
            s2.commit()
        ripple = {
            "roots": len(roots), "recall": round(tp / (tp + fn), 4) if tp + fn else 1.0, "precision": round(tp / (tp + fp), 4) if tp + fp else 1.0,
            "mismatches": fp + fn, "flat_store_recall": round(flat_tp / (flat_tp + flat_fn), 4) if flat_tp + flat_fn else 0.0,
            "widest": f"{widest[0]} -> {widest[1]} nodes", "ms_median": round(statistics.median(ms), 1), "ms_p99": round(sorted(ms)[max(0, int(len(ms) * 0.99) - 1)], 1),
            "engine": "falkordb",
        }
        summary = {
            "corpus": {"packages": len(pkgs), "facts": sum(1 + len(p["reqs"]) for p in pkgs.values()), "conclusions": sum(1 + len(p["reqs"]) for p in pkgs.values()), "causal_edges": n_edges,
                       "cycle_closing_requirements_dropped": dropped, "build_seconds": round(build_s, 1), "source": "importlib.metadata of the Python environment"},
            "questions": {"total": len(qs), "version": res["quorum"]["version_n"], "requirement": res["quorum"]["requirement_n"], "nonexistent": res["quorum"]["abstain_n"], "top_k": K},
            "answers": answers, "impact_question": impact, "ripple": ripple,
        }
        out = {"params": {"seed": a.seed, "script": "bench/real_corpus_eval.py"}, "summary": summary,
               "limits": "Corpus is whatever is installed where this was run; questions are generated from metadata by a seeded script; the 'compatible' and 'installable' conclusions are ours, so propagation validates the traversal on a real dependency structure, not any claim about pip."}
        a.output.write_text(json.dumps(out, indent=2) + "\n", encoding="utf8")
        print(json.dumps(summary, indent=2))
        return 0
    finally:
        forget_stores()
        try:
            db.graph_for(tenant).delete()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    sys.exit(main())
