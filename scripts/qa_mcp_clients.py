"""
Protocol-level client check. Talks to a running hub the way real MCP clients do:

  - streamable HTTP with an Authorization header and no token argument (Claude Code, Cursor, VS Code, Windsurf, Antigravity)
  - stdio, spawning mcp/server.py with QUORUM_MCP_TOKEN in the environment (clients that launch a command)
  - stdio through mcp/bridge.py to the hub (Claude Desktop and other stdio-only clients)
  - a hub that requires the header, and one with a low rate limit

This proves the wire behaviour those clients depend on. It does not drive their user interfaces.

  python scripts/qa_mcp_clients.py --hub http://127.0.0.1:8010 --strict-hub http://127.0.0.1:8011
"""
import argparse
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
from mcp import Client  # noqa: E402
from mcp.client.stdio import StdioServerParameters  # noqa: E402
from mcp.client.streamable_http import streamable_http_client  # noqa: E402
from mcp.shared._httpx_utils import create_mcp_http_client  # noqa: E402

results = []


def check(name, ok, extra=""):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {extra}")


def http_client(url, token):
    return Client(streamable_http_client(url, http_client=create_mcp_http_client(headers={"Authorization": f"Bearer {token}"} if token else {})), mode="legacy")


def body(res):
    sc = res.structured_content
    if sc:
        return sc.get("result", sc) if isinstance(sc.get("result"), dict) else sc
    return json.loads(res.content[0].text)


ADMIN = {"token": ""}


def human(hub, tenant):
    return {"Authorization": f"Bearer {ADMIN['token'] or f'token_{tenant}_human'}"}


def issue(hub, tenant, name):
    h = human(hub, tenant)
    team = httpx.post(f"{hub}/api/workspace/teams", json={"name": f"QA {name}", "seats": 3}, headers=h).json()["teams"][0]["id"]
    r = httpx.post(f"{hub}/api/workspace/members", json={"name": name, "role": "agent", "team_id": team, "client": "claude-code", "device": f"{name} laptop"}, headers=h)
    return r.json()["issued"]


async def main(hub, strict):
    tenant = f"qa{uuid.uuid4().hex[:6]}"
    if ADMIN["token"]:  # a production hub: tokens are real, and the tenant is whatever the admin token belongs to
        tenant = httpx.get(f"{hub}/api/whoami", headers=human(hub, "")).json()["tenant"]
    tok = issue(hub, tenant, "Mira")["token"]
    mcp_url = f"{hub}/mcp"

    # --- streamable HTTP, header auth, no token argument ---
    async with http_client(mcp_url, tok) as c:
        tools = (await c.list_tools()).tools
        names = {t.name for t in tools}
        check("http: 28 tools listed", len(names) == 28 and {"observe", "retrieve_context", "get_map"} <= names, str(len(names)))
        rc = next(t for t in tools if t.name == "retrieve_context")
        check("http: token is optional in the tool schema", "token" not in (rc.input_schema or {}).get("required", []), str(rc.input_schema.get("required")))
        r = body(await c.call_tool("remember_fact", {"key": "refund_chile", "value": "21", "text": "The refund window in Chile is 21 days from delivery."}))
        check("http: remember_fact with header only", r.get("fact_id"), str(r)[:80])
        r = body(await c.call_tool("retrieve_context", {"query": "how long do shoppers in Chile have to get their money back", "format": "compact"}))
        check("http: paraphrased question finds the fact", "Chile" in r.get("packed", ""), r.get("packed", "")[:60].replace("\n", " "))
        r = body(await c.call_tool("observe", {"text": "Support tickets from Norway must be answered within 4 hours. Thanks!", "source": "qa"}))
        check("http: observe captures a fact", r.get("stored") == 1, str(r.get("facts"))[:80])
        r = body(await c.call_tool("delta", {}))
        check("http: delta works after reads", "nothing read yet" not in r.get("text", ""), r.get("text", "")[:40])
    try:
        async with http_client(mcp_url, "qrm_nope_nope") as bad:
            await bad.list_tools()
        check("http: a wrong header token is refused", False, "listed tools")
    except BaseException as e:
        check("http: a wrong header token is refused", True, type(e).__name__)
    calls = httpx.get(f"{hub}/api/mcp/activity", headers=human(hub, tenant)).json()
    check("hub: the audit log names the member, not just the token", any(x.get("member_id") for x in calls) and any(x["tool"] == "observe" for x in calls), f"{len(calls)} calls")
    audit = httpx.get(f"{hub}/api/audit", headers=human(hub, tenant)).json()
    check("hub: audit feed reads in plain language", any(e["text"].startswith("captured facts") for e in audit["events"]), audit["events"][0]["text"] if audit["events"] else "")

    # --- stdio: the client launches mcp/server.py (needs FalkorDB reachable from this machine: not in a production stack) ---
    if not ADMIN["token"]:
      env = {**os.environ, "QUORUM_MCP_TOKEN": tok, "QUORUM_RATE_LIMIT_TOOLS_PER_MIN": "0"}
      params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "mcp" / "server.py"), "--stdio"], env=env)
      async with Client(params, mode="legacy") as s:
          r = body(await s.call_tool("get_map", {}))
          check("stdio: server.py with QUORUM_MCP_TOKEN, no token in any argument", "quorum map" in r.get("map", ""), r.get("map", "")[:30])

    # --- stdio through the bridge to the hub ---
    bridge = StdioServerParameters(command=sys.executable, args=[str(ROOT / "mcp" / "bridge.py"), "--url", mcp_url], env={**os.environ, "QUORUM_MCP_TOKEN": tok})
    async with Client(bridge, mode="legacy") as b:
        names = {t.name for t in (await b.list_tools()).tools}
        check("bridge: same 28 tools through stdio", len(names) == 28, str(len(names)))
        r = body(await b.call_tool("remember_fact", {"key": "bridge_probe", "value": "7", "text": "Bridge probe fact: the retention period is 7 days.", "id": "bridge_probe"}))
        check("bridge: a write through stdio", r.get("fact_id") == "bridge_probe", str(r)[:60])
    g = httpx.get(f"{hub}/api/graph", headers=human(hub, tenant)).json()
    check("bridge: the hub and console see it immediately (one writer)", any(n["id"] == "bridge_probe" for n in g["nodes"]))
    bad_bridge = StdioServerParameters(command=sys.executable, args=[str(ROOT / "mcp" / "bridge.py"), "--url", mcp_url], env={**os.environ, "QUORUM_MCP_TOKEN": "qrm_wrong_token"})
    try:
        async with Client(bad_bridge, mode="legacy") as bb:
            await bb.list_tools()
        check("bridge: a wrong token is reported, not swallowed", False, "no error")
    except BaseException as e:
        check("bridge: a wrong token is reported, not swallowed", True, type(e).__name__)

    # --- a hub that requires the header and limits calls ---
    if strict:
        stenant = f"qs{uuid.uuid4().hex[:6]}"
        stok = issue(strict, stenant, "Ravi")["token"]
        r = httpx.post(f"{strict}/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, headers={"accept": "application/json, text/event-stream"})
        check("strict hub: no header = 401 with WWW-Authenticate", r.status_code == 401 and r.headers.get("www-authenticate") == "Bearer", str(r.status_code))
        async with http_client(f"{strict}/mcp", stok) as c:
            outs = []
            for _ in range(16):
                outs.append(body(await c.call_tool("get_map", {})))
            limited = [o for o in outs if o.get("rate_limited")]
            check("strict hub: tool rate limit kicks in and says when to retry", 0 < len(limited) < 16 and "Retry in" in limited[0]["error"], f"{len(limited)} of 16 limited")

    print("ALL PASS" if all(results) else "SOME FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--hub", default="http://127.0.0.1:8010")
    ap.add_argument("--strict-hub", default="")
    ap.add_argument("--admin-token", default="", help="a human token for a production hub (instead of the development ad-hoc tokens)")
    a = ap.parse_args()
    ADMIN["token"] = a.admin_token
    sys.exit(asyncio.run(main(a.hub.rstrip("/"), a.strict_hub.rstrip("/"))))
