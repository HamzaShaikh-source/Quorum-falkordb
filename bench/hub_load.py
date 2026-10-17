"""
Load and race test against a RUNNING hub over real HTTP (not in-process): 100 concurrent agents.

  1. Mixed workload: by default 70% retrieve, 20% observe (write), 10% announce_work (write); --read-share changes the mix, one shared tenant, N seconds. Reports throughput,
     p50 / p95 / p99 latency and errors, then checks the books: every acknowledged write is really in the graph.
  2. Execute race: one staged action, 100 agents call execute at the same moment, repeated for T trials. The atomic guard must
     let exactly one through every time. Reports the winner count per trial.

Use a development hub with the rate limits off (QUORUM_RATE_LIMIT_TOOLS_PER_MIN=0 QUORUM_RATE_LIMIT_HTTP_PER_MIN=0), otherwise the
hub's own limiter (by design) throttles you. The numbers are for the machine they ran on, with FalkorDB in Docker beside it.

    python bench/hub_load.py --hub http://127.0.0.1:8012 --agents 100 --seconds 20 --trials 20 --output bench/hub_load_results.json
"""
import argparse
import asyncio
import json
import random
import statistics
import time
import uuid
from pathlib import Path

import httpx


async def main_async(a):
    tenant = f"load{uuid.uuid4().hex[:6]}"
    human = {"Authorization": f"Bearer token_{tenant}_human"}
    lim = httpx.Limits(max_connections=a.agents + 20, max_keepalive_connections=a.agents + 20)
    async with httpx.AsyncClient(base_url=a.hub, timeout=60, limits=lim) as c:
        await c.put("/api/workspace", json={"plan": "enterprise"}, headers=human)  # 100 seats
        team = (await c.post("/api/workspace/teams", json={"name": "Load", "seats": a.agents + 10}, headers=human)).json()["teams"][0]["id"]
        tokens = []
        for i in range(a.agents):
            r = await c.post("/api/workspace/members", json={"name": f"agent{i:03d}", "role": "agent", "team_id": team, "client": "custom", "expires_in_days": 0}, headers=human)
            tokens.append(r.json()["issued"]["token"])
        for i in range(30):  # something to find
            await c.post("/api/observe", json={"text": f"The refund window in region {i} is {20 + i} days from delivery.", "source": "seed"}, headers={"Authorization": f"Bearer {tokens[0]}"})

        # ---- 1. mixed workload ----
        lat, errors, writes_ok = [], 0, {"observe": 0, "announce": 0}
        by_kind = {"retrieve": [], "observe": [], "announce": []}
        deadline = time.monotonic() + a.seconds

        async def worker(i):
            nonlocal errors
            h = {"Authorization": f"Bearer {tokens[i]}"}
            rng = random.Random(i)
            n = 0
            while time.monotonic() < deadline:
                x = rng.random()
                kind = "retrieve" if x < a.read_share else ("observe" if x < a.read_share + (1 - a.read_share) * 2 / 3 else "announce")
                t0 = time.perf_counter()
                try:
                    if kind == "retrieve":
                        r = await c.post("/api/context/retrieve", json={"query": f"refund window in region {rng.randint(0, 29)}", "k": 3, "format": "compact"}, headers=h)
                    elif kind == "observe":
                        n += 1
                        r = await c.post("/api/observe", json={"text": f"The limit for agent {i} request {n} is {rng.randint(10, 99)} calls per minute.", "source": f"load{i}"}, headers=h)
                        if r.status_code == 200 and r.json().get("stored", 0) >= 1:
                            writes_ok["observe"] += r.json()["stored"]
                    else:
                        r = await c.post("/api/work/announce", json={"target": f"file_{rng.randint(0, 20)}.py", "intent": "edit"}, headers=h)
                        if r.status_code == 200:
                            writes_ok["announce"] += 1
                    if r.status_code != 200:
                        errors += 1
                except Exception:  # noqa: BLE001
                    errors += 1
                lat.append((time.perf_counter() - t0) * 1000)
                by_kind[kind].append(lat[-1])

        t0 = time.monotonic()
        await asyncio.gather(*(worker(i) for i in range(a.agents)))
        wall = time.monotonic() - t0
        lat.sort()

        def pct(p):
            return round(lat[min(len(lat) - 1, int(len(lat) * p))], 1)

        graph = (await c.get("/api/graph", headers=human)).json()
        observed = sum(1 for n in graph["nodes"] if str(n.get("origin", "")).startswith("observer:load"))
        mixed = {"agents": a.agents, "seconds": round(wall, 1), "requests": len(lat), "throughput_ops_s": round(len(lat) / wall, 1), "p50_ms": pct(0.50), "p95_ms": pct(0.95), "p99_ms": pct(0.99),
                 "median_ms_by_kind": {k: round(statistics.median(v), 1) for k, v in by_kind.items() if v}, "errors": errors, "acknowledged_observe_facts": writes_ok["observe"], "facts_in_graph": observed, "books_balance": observed == writes_ok["observe"]}

        # ---- 2. execute race ----
        winners = []
        for trial in range(a.trials):
            f = (await c.post("/api/observe", json={"text": f"Trial {trial} policy: orders under 100 euros ship free within 2 days.", "source": "race"}, headers=human)).json()
            fid = (f.get("facts") or [{}])[0].get("id")
            if not fid:
                continue
            st = await c.post("/api/actions/stage", json={"label": f"Ship order {trial}", "action_type": "ship", "reversibility": "COMPENSABLE", "parent_ids": [fid]}, headers=human)
            if st.status_code != 200:
                continue
            aid = st.json()["id"]
            rs = await asyncio.gather(*(c.post(f"/api/actions/{aid}/execute", headers={"Authorization": f"Bearer {tokens[i]}"}) for i in range(a.agents)), return_exceptions=True)
            won = sum(1 for r in rs if not isinstance(r, Exception) and r.status_code == 200)
            winners.append(won)
        race = {"trials": len(winners), "agents_per_trial": a.agents, "winners_per_trial": winners, "exactly_one_winner_every_trial": bool(winners) and all(w == 1 for w in winners)}
    return {"params": {"hub": a.hub, "script": "bench/hub_load.py"}, "mixed_workload": {**mixed, "read_share": a.read_share}, "execute_race": race,
            "limits": "One machine, FalkorDB in Docker beside the hub, rate limits off, a development hub; numbers are for that setup only."}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hub", default="http://127.0.0.1:8012")
    ap.add_argument("--agents", type=int, default=100)
    ap.add_argument("--seconds", type=int, default=20)
    ap.add_argument("--trials", type=int, default=20)
    ap.add_argument("--read-share", type=float, default=0.70, help="fraction of requests that are retrievals (the rest: two thirds observe, one third announce_work)")
    ap.add_argument("--output", type=Path, default=Path(__file__).resolve().parent / "hub_load_results.json")
    a = ap.parse_args()
    out = asyncio.run(main_async(a))
    a.output.write_text(json.dumps(out, indent=2) + "\n", encoding="utf8")
    print(json.dumps({k: out[k] for k in ("mixed_workload", "execute_race")}, indent=2))


if __name__ == "__main__":
    main()
