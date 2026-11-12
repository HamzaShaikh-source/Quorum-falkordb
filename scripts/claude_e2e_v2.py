"""
Two real-AI checks with the `claude` CLI against a running hub (uses a little Claude usage):

  A  header auth + paraphrase: Claude connects with an Authorization header and NO token anywhere in its prompt or tool
     arguments, and finds a fact with a question that shares no key word with it.
  B  passive capture: Claude is given no Quorum tools at all. A Stop hook sends what it wrote to /api/observe, and the
     facts in its answer appear in shared memory.

  python scripts/claude_e2e_v2.py --hub http://127.0.0.1:8010
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
results = []


def check(name, ok, extra=""):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {extra}")


def claude(prompt, cwd, extra, timeout=240):
    cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose", "--max-turns", "10", *extra]
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, encoding="utf8")
    events = []
    for line in r.stdout.splitlines():
        try:
            events.append(json.loads(line))
        except Exception:
            pass
    tools, answer = [], ""
    for e in events:
        if e.get("type") == "assistant":
            for b in e["message"]["content"]:
                if b.get("type") == "tool_use":
                    tools.append((b["name"], b.get("input", {})))
        if e.get("type") == "result":
            answer = e.get("result", "")
    return tools, answer, r


def main(hub):
    tenant = f"ce{uuid.uuid4().hex[:6]}"
    h = {"Authorization": f"Bearer token_{tenant}_human"}
    team = httpx.post(f"{hub}/api/workspace/teams", json={"name": "QA", "seats": 3}, headers=h).json()["teams"][0]["id"]
    tok = httpx.post(f"{hub}/api/workspace/members", json={"name": "Mira", "role": "agent", "team_id": team, "client": "claude-code", "device": "laptop"}, headers=h).json()["issued"]["token"]
    places = ["Chile", "Norway", "Kenya", "Japan", "Brazil", "Poland"]
    for i, p in enumerate(places):
        httpx.post(f"{hub}/api/observe", json={"text": f"The refund window in {p} is {20 + i} days from delivery.", "source": "seed"}, headers={"Authorization": f"Bearer {tok}"})
    work = tempfile.mkdtemp(prefix="quorum-e2e2-")  # an empty folder: Claude must not treat the repo as its task

    # ---- A ----
    cfg = Path(work) / "mcp.json"
    cfg.write_text(json.dumps({"mcpServers": {"quorum": {"type": "http", "url": f"{hub}/mcp", "headers": {"Authorization": f"Bearer {tok}"}}}}))
    prompt = "Use the quorum MCP server's retrieve_context tool with format compact to find out how long shoppers in Norway have to get their money back. Answer in one sentence with the number of days."
    assert tok not in prompt
    tools, ans, r = claude(prompt, work, ["--mcp-config", str(cfg), "--strict-mcp-config", "--allowedTools", "mcp__quorum", "--no-session-persistence"])
    if not tools:
        tools, ans, r = claude(prompt, work, ["--mcp-config", str(cfg), "--strict-mcp-config", "--allowedTools", "mcp__quorum", "--no-session-persistence"])
    names = [n for n, _ in tools]
    check("A: Claude called a quorum tool", any(n.startswith("mcp__quorum__") for n in names), str(names))
    check("A: it never put a token in a tool argument", not any("token" in a and a["token"] for _, a in tools), str([a.keys() for _, a in tools]))
    check("A: the paraphrased question found the Norway fact (21 days)", "21" in ans, ans[:100])
    calls = httpx.get(f"{hub}/api/mcp/activity", headers=h).json()
    check("A: the hub logged the call against the member, not an anonymous token", any(c.get("member_id") and c["tool"] == "retrieve_context" for c in calls), f"{len(calls)} calls")

    # ---- B ----
    hook = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": f'"{sys.executable}" "{ROOT / "scripts" / "quorum_observer.py"}"'}]}]}}
    env = {**os.environ, "QUORUM_URL": hub, "QUORUM_TOKEN": tok}
    prompt_b = "Without using any tools, write exactly three short policy statements for a fictional company called Northwind, each stating a specific number and unit (for example hours, days or percent). One sentence each."
    before = len(httpx.get(f"{hub}/api/graph", headers={"Authorization": f"Bearer {tok}"}).json()["nodes"])
    r = subprocess.run(["claude", "-p", prompt_b, "--settings", json.dumps(hook), "--max-turns", "3", "--output-format", "json"], cwd=work, capture_output=True, text=True, timeout=240, env=env, encoding="utf8")
    answer = ""
    try:
        answer = json.loads(r.stdout).get("result", "")
    except Exception:
        pass
    time.sleep(1.5)
    g = httpx.get(f"{hub}/api/graph", headers={"Authorization": f"Bearer {tok}"}).json()["nodes"]
    got = [n for n in g if n.get("origin") == "observer:claude-code"]
    check("B: facts from Claude's answer reached shared memory with no tool call", len(got) == 3, f"{len(got)} of 3 sentences; graph {before}->{len(g)}")
    check("B: each keeps its source sentence", all(n.get("quote") == n.get("text") for n in got) and bool(got))
    if not got:
        print("  Claude said:", answer[:300].replace("\n", " | "))

    print("ALL PASS" if all(results) else "SOME FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--hub", default="http://127.0.0.1:8010")
    sys.exit(main(ap.parse_args().hub.rstrip("/")))
