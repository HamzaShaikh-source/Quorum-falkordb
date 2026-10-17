"""
End-to-end check of the chat-first flow against two REAL hubs on one network, over the real MCP protocol (streamable HTTP with
an Authorization header), the way an AI client does it. Nothing is mocked: the beacon finds the other hub, the request crosses
HTTP, the other admin is notified in the chat, approves, and the first hub reads the second hub's memory.

    python scripts/setup.py                      # hub A  (in one checkout)
    python scripts/setup.py                      # hub B  (in another checkout, other ports)
    python scripts/e2e_connect.py --a http://127.0.0.1:8000 --a-token <A admin> --b http://127.0.0.1:18002 --b-token <B admin>
"""
import argparse
import asyncio
import json
import sys
import time

import httpx
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

results = []
TRANSCRIPT = []  # (hub label, tool, arguments, text): what a chat would show, with secrets removed


def scrub(v):
    import re

    return re.sub(r"(qrm|qtk)_[A-Za-z0-9_-]{6,}", lambda m: m.group(1) + "_...", v) if isinstance(v, str) else v


def check(name, ok, extra=""):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {str(extra)[:160]}")


def client(hub, token):
    return Client(streamable_http_client(hub.rstrip("/") + "/mcp", http_client=create_mcp_http_client(headers={"Authorization": f"Bearer {token}"})), mode="legacy")


def body(res):
    sc = res.structured_content
    if sc:
        return sc.get("result", sc) if isinstance(sc.get("result"), dict) else sc
    return json.loads(res.content[0].text)


LABELS = {}  # id(client) -> whose AI it is, for the transcript


async def call(c, tool, **args):
    out = body(await c.call_tool(tool, args))
    if isinstance(out, dict) and out.get("text"):
        TRANSCRIPT.append({"who": LABELS.get(id(c), ""), "tool": tool, "args": {k: scrub(v) for k, v in args.items()}, "text": scrub(out["text"]), "notice": scrub(out.get("notice", ""))})
    return out


async def main(a):
    import uuid

    pname = f"Priya {uuid.uuid4().hex[:4]}"
    async with client(a.a, a.a_token) as A, client(a.b, a.b_token) as B:
        LABELS[id(A)], LABELS[id(B)] = "Maya's AI", "Hamza's AI"
        names = {t.name for t in (await A.list_tools()).tools}
        check("hub A lists the memory tools and the chat layer", len(names) == 25 and {"quorum_help", "quorum_start", "quorum_connect", "retrieve_context"} <= names, len(names))

        h = await call(A, "quorum_help")
        check("quorum_help shows every command and what it does", all(n in h["text"] for n in ("quorum_connect", "quorum_manage", "retrieve_context")), h["tokens"])

        q = await call(A, "quorum_start")
        check("quorum_start returns the survey to ask", len(q["survey"]) == 5, [x["key"] for x in q["survey"]])
        s = await call(A, "quorum_start", team_size=4, team_name="Support Desk", focus="customer support", auto_remember=True, look_for_teammates=True)
        check("survey answers configure the workspace", s.get("success") and "Support Desk" in s["text"], s["text"].splitlines()[0])
        sb = await call(B, "quorum_start", team_size=3, team_name="Platform", focus="engineering", auto_remember=False, look_for_teammates=True)
        check("hub B set up the same way", sb.get("success"), sb["text"].splitlines()[0])

        peer = None
        for _ in range(30):
            d = await call(A, "quorum_discover")
            if d["peers"]:
                peer = d["peers"][0]["name"]
                break
            time.sleep(2)
        check("hub A finds hub B on the network (UDP beacon)", peer, peer)
        if not peer:
            return

        r = await call(A, "quorum_connect", peer=peer, message="Hi, it is the support desk")
        fresh = bool(r.get("success"))
        check("A asks to connect to B by name", fresh or "already connected" in str(r.get("error")), r.get("text", r.get("error")))
        if fresh:
            st = await call(B, "quorum_status")
            check("B is notified inside the chat it is already in", "connection request" in str(st.get("notice", "")), st.get("notice"))
            inbox = await call(B, "quorum_inbox")
            check("B's inbox shows who is asking", len(inbox["requests"]) == 1, inbox["text"].splitlines()[-2] if inbox["requests"] else inbox)
            rid = inbox["requests"][0]["id"]
            ok = await call(B, "quorum_respond", request_id=rid, decision="approve", level="reviewer")
            check("B approves and picks a permission level", ok.get("success"), ok.get("text"))
            quiet = await call(B, "quorum_status")
            check("the notice goes away once answered", "notice" not in quiet)

        await call(B, "remember_fact", key="refund_window", value="30 days", text="Refunds are allowed within 30 days of delivery.")
        conn = {}
        for _ in range(10):
            conn = await call(A, "quorum_connections")
            if conn["connections"]:
                break
            time.sleep(1)
        check("A is connected without copying a token by hand", conn["connections"] and conn["connections"][0]["role"] == "auditor", conn["text"])
        pr = await call(A, "peer_retrieve", peer=peer, query="how many days do customers have to request a refund")
        check("A reads B's shared memory from its own chat", pr.get("count", 0) >= 1 and "30" in pr["text"], pr["text"].splitlines()[-1])

        stat = await call(A, "quorum_status")
        check("quorum_status explains remembered, will-be-remembered and shared", all(w in stat["text"] for w in ("Remembered", "Will be remembered", "Shared with")), stat["counts"])

        added = await call(A, "quorum_manage", action="member_add", name=pname, team="Support Desk", level="contributor")
        check("A creates a person and a token in chat", added.get("success") and added["token"].startswith("qrm_"), added["text"].splitlines()[0])
        async with client(a.a, added["token"]) as P:
            denied = await call(P, "quorum_manage", action="team_create", name="Hack", seats=1)
            check("a Contributor cannot manage the team (and is told why)", denied.get("success") is False and "Admin" in denied["error"], denied.get("error"))
        lv = await call(A, "quorum_manage", action="member_set_level", member=pname, level="reviewer")
        check("A changes a person's permission level in chat", lv.get("success"), lv["text"])

        # a real chain on hub B, quarantined: the cascade must run in FalkorDB and agree with the independent walk
        root = await call(B, "remember_fact", key="promo_window", value="90 days", text="A partner feed says refunds are allowed for 90 days.", id="e2e_root")
        c1 = await call(B, "derive", text="So order 1042 is eligible for a refund.", parent_ids=["e2e_root"], id="e2e_c1")
        c2 = await call(B, "derive", text="So we should refund order 1042 today.", parent_ids=["e2e_c1"], id="e2e_c2", is_decision=True)
        check("hub B holds a three-step causal chain", root.get("success", True) and c1.get("success", True) and c2.get("success", True), [root.get("fact_id"), c1.get("id") or c1.get("conclusion_id"), c2.get("id") or c2.get("conclusion_id")])
        q = await call(B, "quarantine", root_id="e2e_root", reason="wrong policy")
        check("quarantining the root takes everything built on it", q.get("success") and q.get("affected_count") == 3, q.get("affected_count"))
        proof = httpx.get(a.b.rstrip("/") + "/api/falkordb/proof", params={"root": "e2e_root"}, headers={"Authorization": f"Bearer {a.b_token}"}).json()
        cmp = proof.get("comparison") or {}
        check("FalkorDB's traversal equals the independent in-process walk", cmp.get("identical") is True and (cmp.get("falkordb") or {}).get("descendants") == 2, cmp)
        check("the cascade ran in FalkorDB, not the fallback", proof["last_cascade_engine"] == "falkordb" and proof["traversals_answered_by"]["falkordb"] > 0 and proof["traversals_answered_by"]["process"] == 0, proof["traversals_answered_by"])
        check("the database itself holds the nodes and edges", proof["database"]["nodes"] >= 3 and proof["database"]["edges"] >= 2, proof["database"])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", required=True)
    ap.add_argument("--a-token", required=True)
    ap.add_argument("--b", required=True)
    ap.add_argument("--b-token", required=True)
    ap.add_argument("--transcript", default="", help="write the tool results (secrets removed) as JSON, for the demo video")
    args = ap.parse_args()
    asyncio.run(main(args))
    if args.transcript:
        with open(args.transcript, "w", encoding="utf8") as f:
            json.dump(TRANSCRIPT, f, indent=2)
    print(f"\n{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if all(results) else 1)
