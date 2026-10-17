"""
Real AIs answering from Quorum's memory: does semantic ranking change what a model gets right?

Two hubs hold the same 336 facts: one ranks with keywords plus vectors, one with keywords only (QUORUM_RANKING=keyword).
Two Claude models (sonnet and haiku, through the `claude` CLI) each answer the same questions on both, using only
Quorum's retrieve_context. Answers are checked for the exact number in the fact; off-topic questions must come back NOT FOUND.

  python bench/ai_retrieval_bench.py --hybrid http://127.0.0.1:8010 --keyword http://127.0.0.1:8012

Uses real Claude usage (about 90 short runs). Writes bench/ai_retrieval_bench.json.
"""
import argparse
import asyncio
import json
import random
import re
import statistics
import subprocess
import sys
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "bench"), str(ROOT / "backend")]
import os  # noqa: E402

os.environ.setdefault("QUORUM_PERSISTENCE", "memory")
import retrieval_bench as rb  # noqa: E402
from mcp import Client  # noqa: E402
from mcp.client.streamable_http import streamable_http_client  # noqa: E402
from mcp.shared._httpx_utils import create_mcp_http_client  # noqa: E402

MODELS = ["sonnet", "haiku"]


async def seed(hub: str, tenant: str, store) -> None:
    tok = f"token_{tenant}_agent"
    client = Client(streamable_http_client(f"{hub}/mcp", http_client=create_mcp_http_client(headers={"Authorization": f"Bearer {tok}"})), mode="legacy")
    async with client as c:
        for n in store.nodes.values():
            for _ in range(60):  # a hub with rate limits on answers "rate_limited" instead of storing: wait and retry
                res = await c.call_tool("remember_fact", {"key": n.key, "value": n.value, "text": n.text, "id": n.id})
                sc = res.structured_content or {}
                body = sc.get("result", sc) if isinstance(sc.get("result"), dict) else sc
                if not body.get("rate_limited"):
                    break
                await asyncio.sleep(max(0.2, float(body.get("retry_after", 0.5))))
    stored = len(httpx.get(f"{hub}/api/graph", headers={"Authorization": f"Bearer {tok}"}).json()["nodes"])
    if stored != len(store.nodes):
        raise SystemExit(f"seeding stored {stored} of {len(store.nodes)} facts on {hub}; the benchmark would be meaningless")


def ask(hub: str, tenant: str, model: str, q: str, work: str):
    tok = f"token_{tenant}_agent"
    cfg = Path(work) / f"mcp-{uuid.uuid4().hex[:6]}.json"
    cfg.write_text(json.dumps({"mcpServers": {"quorum": {"type": "http", "url": f"{hub}/mcp", "headers": {"Authorization": f"Bearer {tok}"}}}}))
    prompt = (
        "Answer using ONLY the quorum MCP server's memory: call retrieve_context with format compact (you may rephrase the query). "
        f"Question: {q}\nReply with one short sentence that includes the exact number from the memory. If the memory does not contain the answer, reply exactly: NOT FOUND."
    )
    for _attempt in range(2):
        r = subprocess.run(
            ["claude", "-p", prompt, "--model", model, "--mcp-config", str(cfg), "--strict-mcp-config", "--allowedTools", "mcp__quorum", "--no-session-persistence", "--max-turns", "8", "--output-format", "json"],
            cwd=work, capture_output=True, text=True, timeout=300, encoding="utf8",
        )
        try:
            d = json.loads(r.stdout)
        except Exception:
            d = {}
        if d.get("result") is not None:
            u = d.get("usage") or {}
            toks = u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
            return {"answer": d["result"], "turns": d.get("num_turns", 0), "input_tokens": toks, "cost": d.get("total_cost_usd", 0.0)}
    return {"answer": "", "turns": 0, "input_tokens": 0, "cost": 0.0}


def judge(kind: str, answer: str, truth) -> bool:
    if kind == "none":
        return "NOT FOUND" in answer.upper()
    return re.search(rf"(?<![\d.]){re.escape(str(truth))}(?![\d])", answer) is not None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hybrid", default="http://127.0.0.1:8010")
    ap.add_argument("--keyword", default="http://127.0.0.1:8012")
    ap.add_argument("--known", type=int, default=12)
    ap.add_argument("--unknown", type=int, default=6)
    ap.add_argument("--parallel", type=int, default=4)
    ap.add_argument("--seed", type=int, default=11)
    a = ap.parse_args()

    store, facts, rng = rb.build(seed=a.seed)
    all_q = rb.questions(facts, rng, per_kind=a.known + a.unknown)
    known = [q for q in all_q if q[0] == "para_known"][: a.known]
    unknown = [q for q in all_q if q[0] == "para_unknown"][a.unknown * -1 :]
    none = [q for q in all_q if q[0] == "none"][: max(4, a.known // 3)]
    qs = known + unknown + none
    truth = {fid: store.nodes[fid].value for fid in store.nodes}

    arms = {"hybrid": a.hybrid.rstrip("/"), "keyword": a.keyword.rstrip("/")}
    tenants = {}
    for arm, hub in arms.items():
        tenants[arm] = f"ab{uuid.uuid4().hex[:6]}"
        asyncio.run(seed(hub, tenants[arm], store))
        print(f"seeded {len(store.nodes)} facts on {arm} hub", flush=True)

    work = tempfile.mkdtemp(prefix="quorum-aibench-")
    jobs = [(arm, m, k, q, fid) for arm in arms for m in MODELS for (k, q, fid) in qs]
    random.Random(3).shuffle(jobs)

    def run(job):
        arm, m, k, q, fid = job
        out = ask(arms[arm], tenants[arm], m, q, work)
        ok = judge(k, out["answer"], truth.get(fid) if fid else None)
        print(f"{'ok ' if ok else 'MISS'} {arm:7} {m:6} {k:12} {q[:60]}", flush=True)
        return {**out, "arm": arm, "model": m, "kind": k, "question": q, "correct": ok}

    with ThreadPoolExecutor(a.parallel) as ex:
        rows = list(ex.map(run, jobs))

    summary = {}
    for arm in arms:
        for m in MODELS:
            r = [x for x in rows if x["arm"] == arm and x["model"] == m]
            by = {}
            for kind in ("para_known", "para_unknown", "none"):
                kr = [x for x in r if x["kind"] == kind]
                by[kind] = {"questions": len(kr), "correct": sum(x["correct"] for x in kr)}
            summary[f"{arm}/{m}"] = {
                "by_kind": by,
                "correct": sum(x["correct"] for x in r),
                "questions": len(r),
                "median_turns": statistics.median(x["turns"] for x in r),
                "median_input_tokens": int(statistics.median(x["input_tokens"] for x in r)),
                "cost_usd_total": round(sum(x["cost"] for x in r), 3),
            }
    res = {"facts": len(store.nodes), "questions": len(qs), "models": MODELS, "summary": summary, "rows": rows}
    (ROOT / "bench" / "ai_retrieval_bench.json").write_text(json.dumps(res, indent=2), encoding="utf8")
    slim = {"facts": res["facts"], "questions": res["questions"], "models": MODELS, "summary": summary, "note": "Each model answered each question once per hub. Small sample: read it as direction, not precision."}
    (ROOT / "bench" / "ai_retrieval_summary.json").write_text(json.dumps(slim, indent=2), encoding="utf8")
    for k, v in summary.items():
        print(k, f"{v['correct']}/{v['questions']}", {kk: f"{vv['correct']}/{vv['questions']}" for kk, vv in v["by_kind"].items()}, "turns", v["median_turns"], "in-tok", v["median_input_tokens"], "$", v["cost_usd_total"])


if __name__ == "__main__":
    main()
