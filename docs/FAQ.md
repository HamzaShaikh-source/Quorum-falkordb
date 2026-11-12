# Questions people ask

## What is Quorum, in one sentence?

Shared memory for AI agents and teammates where every belief, conclusion and action is a node in a causal graph in FalkorDB, so one correction can quarantine a wrong fact and everything built on it.

## How is this different from a vector store or a notes file?

A vector store finds similar text. It does not know that conclusion B was derived from fact A, or that action C ran because of B. Quorum records those links, so deleting or correcting A can reach B and C. The measured difference, with the limits stated, is on one page: [`HACKATHON.md`](HACKATHON.md).

## Do I have to use the console?

No. One command (`python scripts/setup.py`) and one pasted prompt, and your AI does the rest: it lists every command, asks a few questions, finds teammates, and shows what is remembered. Ask it `quorum_guide` for any how-to. The console is there when you want to see the graph. See [`USER_GUIDE.md`](USER_GUIDE.md).

## Which AIs work?

Anything that speaks MCP over HTTP with a header, or through the stdio bridge: Claude Code, Claude Desktop, Cursor, VS Code, Windsurf, Antigravity, ChatGPT and OpenAI agents, and other MCP clients. Per-client notes: [`AI_SETUP.md`](AI_SETUP.md) and [`MCP.md`](MCP.md).

## What is stored, and where?

Facts, conclusions, actions, handoffs and an audit trail, in FalkorDB on your machine (`quorum:<tenant>` graphs). Quorum sends nothing to a hosted service; retrieval uses built-in local vectors unless you configure an embedding provider. Credentials are refused at the write gate: a fact that looks like a key or password is not stored.

## How do two people share memory?

Run Quorum on each laptop. `quorum_discover` finds the other hub on your network by name; `quorum_connect` asks to join; the other person's admin approves in their chat at a permission level they choose. Nothing is shared until then. If discovery is blocked on your network, give the address instead. See [`AI_SETUP.md`](AI_SETUP.md#how-connecting-stays-safe).

## What if an agent saves something wrong?

Ask for the blast radius (`explain_blast_radius`, a dry run), then call `quarantine(root_id, reason)` (a Reviewer or Admin can). Everything built on the fact leaves circulation, pending actions that depend on it are blocked, and actions that already ran are compensated or get a drafted correction notice. `restore` brings it back only where every parent is healthy again.

## What if two agents copy each other to look independent?

A claim does not count as corroboration unless its asserter, source, host, declared lineage and wording are independent. A reworded copy with no trace cannot be detected by any provenance system; that boundary is stated, and measured: [`HACKATHON.md`](HACKATHON.md).

## Is it fast?

Reads are fast (a retrieval is a few milliseconds once warm); the cascade through FalkorDB takes tens of milliseconds on the benchmark graphs. Write throughput is modest by design: one writer process keeps the execute guard atomic. On a Windows laptop with Docker, 100 concurrent agents over HTTP measured 31.9 mixed ops/s with zero errors. Numbers and limits: [`PROOF.md`](PROOF.md) and `bench/`.

## Can I run several API workers?

No. One API writer process (with the MCP server embedded) per FalkorDB. Separate workers or standalone MCP writers are unsupported. See [`../SECURITY.md`](../SECURITY.md).

## Is the data real?

No. Every dataset in the demo, the benchmarks and the evaluations is synthetic, or (for the corpus test) the metadata of Python packages already installed on the machine.

## How do I check your claims?

Every number in the README is produced by a script in `bench/` with its result file committed, and the FalkorDB claim has a live check:

```bash
curl -H "Authorization: Bearer $QUORUM_ADMIN_TOKEN" http://localhost:8000/api/falkordb/proof
```

## It will not start

Run `python scripts/doctor.py`. It checks FalkorDB, the backend and MCP, and prints the fix for anything missing. The usual cause is Docker not running.

## How do I stop everything and clean up?

Judge mode: `python scripts/judge.py --stop`. A normal setup: `docker compose down` (add `-v` to delete the data).
