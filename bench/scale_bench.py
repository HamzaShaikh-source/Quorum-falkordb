"""
How retrieval behaves as the memory grows, and what a long session costs.

  1. Latency: a ranked, packed answer at 1,000 / 5,000 / 20,000 facts (warm: vectors and word sets already built, as they are
     after the first question, and in FalkorDB after a restart).
  2. A 60-turn session on a 5,000-fact memory, modelled from the measured size of every answer: a top-8 retriever's full JSON
     each turn, Quorum's compact lines each turn, and compact lines with delta() for the follow-up questions. Whole-session
     input is modelled as the fixed Claude Code prompt (about 37,000 tokens, measured in bench/token_bench_*.json) plus every
     tool result still in the context, summed over the turns. It is a model from measured sizes, not a real-AI run.

  python bench/scale_bench.py        writes bench/scale_bench.json
"""
import json
import os
import random
import statistics
import sys
import time
from pathlib import Path

os.environ.setdefault("QUORUM_PERSISTENCE", "memory")
ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "bench")]

import retrieval_bench as rb  # noqa: E402
from app.graph.queries import get_graph_store  # noqa: E402
from app.models.common import NodeKind, Status  # noqa: E402
from app.models.graph import GraphNode  # noqa: E402
from app.services import context_pack as cp  # noqa: E402
from app.services.context_pack import estimate_tokens  # noqa: E402

FIXED_PROMPT = 37_000  # tokens Claude Code sends every turn regardless of tools (bench/token_bench_*.json)


def build(n: int, tenant: str):
    store = get_graph_store(tenant)
    store.nodes.clear()
    rng = random.Random(1)
    facts = []
    for i in range(n):
        t = rb.TOPICS[i % len(rb.TOPICS)]
        place = f"{rb.PLACES[(i // len(rb.TOPICS)) % len(rb.PLACES)]} site {i // (len(rb.TOPICS) * len(rb.PLACES))}"
        text = t[1].format(p=place, n=rng.randint(3, 90))
        node = GraphNode(id=f"s{i}", kind=NodeKind.FACT, label=text[:60], status=Status.HEALTHY, stale=False, key=f"k{i}", value="1", text=text, valid_from=i)
        store.add_node(node)
        facts.append((t, place, node))
    return store, facts


def latency():
    out = []
    for n in (1000, 5000, 20000):
        store, facts = build(n, f"scale{n}")
        qs = [facts[i][0][2].format(p=facts[i][1]) for i in range(0, n, max(1, n // 12))][:12]
        t = time.time()
        cp.pack(store, qs[0])
        cold = (time.time() - t) * 1000
        runs = []
        for q in qs:
            t = time.time()
            cp.pack(store, q)
            runs.append((time.time() - t) * 1000)
        out.append({"facts": n, "cold_first_answer_ms": round(cold), "warm_median_ms": round(statistics.median(runs)), "warm_p95_ms": round(sorted(runs)[int(len(runs) * 0.95) - 1])})
    return out


def session(turns: int = 60):
    store, facts = build(5000, "scale_session")
    rng = random.Random(5)
    nodes = list(store.nodes.values())
    idf = cp.idf_weights(nodes)
    plain, compact, delta_mode = [], [], []
    seen_ids = set()
    for turn in range(turns):
        topic, place, node = rng.choice(facts)
        q = topic[2].format(p=place) if turn % 3 else topic[3].format(p=place)  # direct and reworded questions
        top = [x for x, s, _ in cp.rank(nodes, q, idf) if s > 0][:8]
        plain.append(estimate_tokens(json.dumps([{"id": f.id, "key": f.key, "value": f.value, "text": f.text, "quote": f.quote} for f in top], indent=2)))
        p = cp.pack(store, q, budget=400)
        compact.append(p["tokens"])
        if turn % 3 == 2 and seen_ids:  # a follow-up on something already read: delta says only what changed
            d = cp.pack(store, q, budget=300, have_ids=seen_ids)
            delta_mode.append(d["tokens"] + 8)
        else:
            delta_mode.append(p["tokens"])
        seen_ids.update(p["ids"])

    def whole(results):
        total, ctx = 0, 0
        for r in results:
            ctx += r
            total += FIXED_PROMPT + ctx
        return total

    row = lambda name, r: {"strategy": name, "tool_result_tokens": sum(r), "whole_session_input_tokens": whole(r)}  # noqa: E731
    rows = [row("top-8 retriever, full JSON", plain), row("Quorum compact lines", compact), row("Quorum compact + delta on follow-ups", delta_mode)]
    base = rows[0]
    for r in rows:
        r["tool_result_saving_pct"] = round((1 - r["tool_result_tokens"] / base["tool_result_tokens"]) * 100, 1)
        r["whole_session_saving_pct"] = round((1 - r["whole_session_input_tokens"] / base["whole_session_input_tokens"]) * 100, 1)
    return {"turns": turns, "facts": 5000, "fixed_prompt_tokens": FIXED_PROMPT, "strategies": rows}


def main():
    res = {"latency": latency(), "session_model": session(), "note": "Latency is this machine, in process, no network. The session is a model from measured answer sizes, not a real-AI run."}
    (ROOT / "bench" / "scale_bench.json").write_text(json.dumps(res, indent=2), encoding="utf8")
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
