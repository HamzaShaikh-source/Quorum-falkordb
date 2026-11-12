# For judges: Quorum on one page

**Track:** Agent Memory and Coordination. **One line:** git revert for AI agent beliefs, on FalkorDB.

When an agent learns something wrong, the wrong fact spreads: conclusions are built on it, actions are staged on it, other agents read it. Quorum stores every belief, conclusion and action in a causal graph in FalkorDB, so one correction quarantines the wrong fact and everything built on it, blocks any action that depends on it, and compensates the ones that already ran.

## What we claim, and what we do not

**We claim** the sequence **correct, quarantine, block, compensate**: when a belief turns out wrong, everything built on it is pulled out of circulation through a FalkorDB traversal, actions that depend on it are refused, and ones that already ran are compensated. The evidence for that is the real-agent scenarios (wrong actions after a correction), the cascade checks on real and generated graphs, and the red team.

**We do not claim** a general memory-accuracy win. The real-agent comparison is against memory *without causal edges* on four scenarios we wrote, with one small model; it measures unsafe actions after a correction, not answer quality. We have not run a head-to-head question-answering comparison against another system's vector baseline with the same answer model and confidence intervals (Relay reports one on 160 questions); our corpus evaluation uses plain TF-IDF search, generated questions and no confidence intervals. We also do not claim throughput: one writer process, modest measured numbers.

## Watch it (under two minutes)

[`docs/demo/quorum-demo.mp4`](demo/quorum-demo.mp4): the poisoned-fact story in the console, a teammate asking to connect and being approved, the paste-one-prompt setup, and the chat commands, using real tool output from two live hubs. Regenerate it: `frontend/scripts/record-demo.mjs`.

## Try it (five minutes, one command)

**Judge mode, nothing to set up and nothing hosted:**

```bash
python scripts/judge.py          # judge.bat on Windows, ./judge.sh elsewhere; python scripts/judge.py --stop to remove it
```

It starts the demo stack as its own Docker project on free ports (never touching a real setup or another Quorum stack), opens the console with the poisoned-fact story playing by itself, and prints what to look at, including the one `curl` that checks the FalkorDB claim. The full product, set up from your AI chat:

```bash
python scripts/setup.py        # Docker + Python 3; then paste the printed prompt into your AI
```

Or just the console with the guided story: `QUORUM_DEMO=1 docker compose up -d`, open http://localhost:5173.

## Criteria, and where the evidence is

| What judges usually look for | Evidence | Where |
| --- | --- | --- |
| **Real, essential use of FalkorDB** | The cascade, the execute guard's ancestry check and the blast radius run as Cypher in FalkorDB (one statement per hop); shortest path, vector index, one-statement checkpoint. A live endpoint compares FalkorDB's answer with an independent in-process walk. | [`FALKORDB.md`](FALKORDB.md), `GET /api/falkordb/proof`, `backend/tests/test_falkordb_native.py` |
| **Does the graph change the outcome?** | 24 generated scenarios: a flat memory let 62 of 62 at-risk actions run, the same nodes with edges in FalkorDB let 0 of 62 run; 170 tainted conclusions stay retrievable in the flat arm versus 0. | `bench/ablation_graph.py`, `bench/ablation_results.json` |
| **Does a real agent behave differently?** | The real `claude -p` CLI (`haiku`) in four scenarios in different domains (refund, access grant, discount quote, production release), interleaved, outcome measured on the hub: a wrong action ran **28 of 28** times with flat memory and **0 of 28** with Quorum, same memory and same correction. | `bench/agent_eval.py`, [`FALKORDB.md`](FALKORDB.md#more-scenarios-with-a-real-agent) |
| **A real corpus, with a plain-search baseline** | 437 installed Python packages, 1,326 facts citing their own metadata, 120 generated questions, no LLM judge. Plain search refuses a nonexistent package 0.0% of the time, Quorum 100.0%; on the multi-hop "what is affected?" question plain search is exact 10.0% of the time, Quorum 100.0%; ripple 100% recall and precision. | `bench/real_corpus_eval.py` |
| **Concurrency** | 100 agents over HTTP: 750 mixed requests, 0 errors, books balance; 20 races of 100 agents on one execute guard, exactly one winner every time. Throughput is modest (31.9 ops/s mixed on a Windows laptop) and said so. | `bench/hub_load.py` |
| **Real data, not only generated** | The cascade on this project's own import graph (70 modules, 300 edges, ripple up to 53 modules): recall and precision 100% against an independent `ast` oracle. A 200-hop chain is quarantined to the end (no depth limit). | `bench/real_graph_ripple.py`, `test_falkordb_native.py` |
| **Hardening** | A red team of the write path and permissions: 31 attacks, all refused or inert. Five got through when we first ran it (forged origin, credentials in facts, oversize and malformed fields, control characters) and are fixed. | `backend/tests/test_redteam.py` |
| **Copied claims do not count twice** | An Echo-style test of our own gate: 18 poisoned claims echoed through six ways of copying (verbatim, same source node, same host, declared copy chain, lightly reworded, reworded with no trace) and 12 genuine claims. Comparing origin labels only trusted 18 of 18 poisoned claims; the lineage-aware gate trusts 3, all of them the reworded copy with no trace, which no provenance can reveal. Genuine claims stay trusted (12 of 12). | `bench/corroboration_eval.py`, `test_independent_corroboration.py` |
| **Working product, end to end** | Two hubs set up by one command each, found each other on the network, connected with approval, and read each other's memory over the real MCP protocol: 18 of 18 checks. Console live QA: 59 of 59. | `scripts/e2e_connect.py`, `frontend/scripts/live.mjs` |
| **Ease of use** | One command, one pasted prompt. The AI shows every command, runs a five-question survey, discovers teammates, shows what is remembered and shared, and manages teams and permission levels, in any MCP-capable AI. No console needed. | [`AI_SETUP.md`](AI_SETUP.md) |
| **Token efficiency** | Compact retrieval with short ids and provenance, `delta` instead of re-reading, `get_map` orientation, tool profile `core`. Measured savings against a top-8 retriever are shown per call and per member. | [`TOKEN_SAVING.md`](TOKEN_SAVING.md), `QUORUM_TOOL_PROFILE` |
| **Security** | Production rules by default: hashed expiring member tokens, header auth on `/mcp`, rate limits, lockout, trusted-proxy allowlist, role levels checked per call, private-network-only peers, approval for every connection. | [`../SECURITY.md`](../SECURITY.md), [`AI_SETUP.md`](AI_SETUP.md#how-connecting-stays-safe) |
| **Verification** | CI on every push: ruff, mypy, 660+ backend tests against a real FalkorDB, frontend types, lint, 218 tests, contrast, contract check, both builds, compose validation. Accessibility: axe-core, 0 violations across every screen at desktop and phone width. | `.github/workflows/ci.yml`, `frontend/scripts/a11y.mjs` |
| **Design** | An editorial, print-proof-room console: every control says what it is in words; reduced-motion respected; the onboarding tour never traps the screen (Escape, Skip and Close all end it). | `docs/screens/` |

## Where it stands against the public Track 02 entries

From their public READMEs (not a verified ranking; entries can change before judging):

| Idea | Seen in | Quorum |
| --- | --- | --- |
| A real with-and-without agent evaluation | Hivemind (Claude Code, 2 runs per variant) | Adopted: `bench/agent_eval.py`, 10 runs per arm, outcome-based. Ours measures a bad belief reaching an external action; theirs measures a coding task that depends on remembered conventions. |
| Warn before two agents collide | Hivemind (PreToolUse hook on files) | Adopted and generalised: `announce_work` plus `scripts/quorum_conflict_hook.py`; a file, an order or a ticket. |
| Temporal memory, "what did we believe then" | Hivemind, Crewbrain | `recall_as_of`, with what happened to each fact since. |
| What leaves with a person | What Leaves With Them, Lineage (a person left, as a graph copy) | `leaver_report` (sole-source facts, what is built on them via FalkorDB, tasks held, likely successors) and a Team page button. Simpler than their island and bridge analysis. |
| A write gate with receipts, and a red team of it | What Leaves With Them (36 attacks) | `ACCEPT`/`SUPERSEDE`/`REJECT`/`REVIEW` receipts and a 31-attack suite. |
| Answers that say "not in memory" | What Leaves With Them, Lineage | `ANSWERED` / `NOT_IN_MEMORY` on every retrieval. |
| Independent origins: repeated is not corroborated | Echo (traces citation, copy and ownership edges between sources) | Adopted in the form our model allows: `copied_from` lineage, source node and host, claimed source, near-duplicate wording; measured in `bench/corroboration_eval.py`. Not adopted: hyperlink, shared-identifier and owner-signal detection. |
| A guard that understands dependency, not just names | Switchboard (import-graph hops, refuses an edit through a path; its benchmark runs the real TypeScript compiler) | `announce_work` is dependency-aware when the target is a fact: work within three causal hops, upstream or downstream, is a conflict (a FalkorDB traversal). Not adopted: parsing code imports, or a compiler-verified benchmark. |
| Un-learning a belief and everything built on it, including actions | Lineage, MnemoIR | Core to Quorum, and the only one here that also blocks and compensates *external actions*. |

Still ahead of us: Lineage's much larger real corpus (about 28,000 nodes) and its curated questions (ours are generated); Hivemind's automatic procedure capture from failed-then-fixed commands and its graph algorithms (PageRank, communities); counterfactual replay of an affected decision (MnemoIR). Our real-data evidence is now a 437-package corpus, a 70-node import graph and four agent scenarios; still smaller than Lineage's corpus, and written by us.

## Honest limits

- **Write throughput is modest.** Every write is durable before it is acknowledged (31.9 ops/s mixed, 132.9 ops/s reads, 100 concurrent agents, Windows laptop with Docker). Hivemind reports about 1,100 ops/s on its own setup; we do not claim parity.
- **Corroboration is only as independent as the provenance Quorum is shown.** The gate treats two facts as one witness when the same agent wrote both, one declares it was copied from the other (`copied_from`, followed through chains), they cite the same source node or sources on the same host, their claimed source is the same once the writer's own prefix is removed, or their wording is identical or nearly so. A review found that a root-tier writer repeating a claim under a different origin label used to count as corroboration (the gate trusted 18 of 18 poisoned echoes); that is fixed and measured. What it cannot see: a reworded copy with no declared lineage, no shared source, a different owner and different wording. The near-duplicate threshold (0.65 word overlap) was set after the first run missed a lightly edited copy; it fails closed, so a borderline pair goes to a person. Echo goes further (hyperlinks, shared identifiers, owner and ad-network signals); we do not.
- **One writer per FalkorDB.** The atomic check-and-set runs under a lock in the single API process; FalkorDB decides ancestry, the lock makes the decision atomic. Horizontal scaling of writers is out of scope.
- **The agent tests are four scenarios we wrote**, with small trial counts and one small model and a model-dependent outcome; a first version, where the true policy sat next to the stale conclusion, did not discriminate (the agent reconciled it in both arms), so we changed it to the realistic case and say so in `docs/FALKORDB.md`.
- **The ablation is generated data** and "flat" means "no causal edges". It shows provenance changes the outcome; it is not a product comparison.
- **The built-in retrieval vectors are not a neural model.** They handle inflection and a lexicon of business paraphrases; connect an OpenAI-compatible embedding endpoint for broad coverage.
- **LAN discovery** needs UDP broadcast on the network (many guest or corporate Wi-Fi networks block it). Connecting by address is the fallback: the same request flow works once a peer is known.
- **Not run in a hosted deployment.** There is no public URL; the demo video and one-command setup stand in for it. A production compose file with TLS is included.
- **Not covered:** counterfactual replay of an affected decision, temporal validity windows for facts, and re-checking an original source for drift. These are the clearest next steps; related projects (MnemoIR, Graphiti, ActiveGraph, MemGuard, graph-memory-mcp) each do one of them better than Quorum does today, and none combines them with guarded, compensated actions.
