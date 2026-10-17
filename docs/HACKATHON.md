# For judges: Quorum on one page

**Track:** Agent Memory and Coordination. **One line:** git revert for AI agent beliefs, on FalkorDB.

When an agent learns something wrong, the wrong fact spreads: conclusions are built on it, actions are staged on it, other agents read it. Quorum stores every belief, conclusion and action in a causal graph in FalkorDB, so one correction quarantines the wrong fact and everything built on it, blocks any action that depends on it, and compensates the ones that already ran.

## Watch it (95 seconds)

[`docs/demo/quorum-demo.mp4`](demo/quorum-demo.mp4): the poisoned-fact story in the console, a teammate asking to connect and being approved, the paste-one-prompt setup, and the chat commands, using real tool output from two live hubs. Regenerate it: `frontend/scripts/record-demo.mjs`.

## Try it (five minutes, one command)

```bash
python scripts/setup.py        # Docker + Python 3; then paste the printed prompt into your AI
```

Or just the console with the guided story: `QUORUM_DEMO=1 docker compose up -d`, open http://localhost:5173.

## Criteria, and where the evidence is

| What judges usually look for | Evidence | Where |
| --- | --- | --- |
| **Real, essential use of FalkorDB** | The cascade, the execute guard's ancestry check and the blast radius run as Cypher in FalkorDB (one statement per hop); shortest path, vector index, one-statement checkpoint. A live endpoint compares FalkorDB's answer with an independent in-process walk. | [`FALKORDB.md`](FALKORDB.md), `GET /api/falkordb/proof`, `backend/tests/test_falkordb_native.py` |
| **Does the graph change the outcome?** | 24 generated scenarios: a flat memory let 62 of 62 at-risk actions run, the same nodes with edges in FalkorDB let 0 of 62 run; 170 tainted conclusions stay retrievable in the flat arm versus 0. | `bench/ablation_graph.py`, `bench/ablation_results.json` |
| **Working product, end to end** | Two hubs set up by one command each, found each other on the network, connected with approval, and read each other's memory over the real MCP protocol: 18 of 18 checks. Console live QA: 59 of 59. | `scripts/e2e_connect.py`, `frontend/scripts/live.mjs` |
| **Ease of use** | One command, one pasted prompt. The AI shows every command, runs a five-question survey, discovers teammates, shows what is remembered and shared, and manages teams and permission levels, in any MCP-capable AI. No console needed. | [`AI_SETUP.md`](AI_SETUP.md) |
| **Token efficiency** | Compact retrieval with short ids and provenance, `delta` instead of re-reading, `get_map` orientation, tool profile `core`. Measured savings against a top-8 retriever are shown per call and per member. | [`TOKEN_SAVING.md`](TOKEN_SAVING.md), `QUORUM_TOOL_PROFILE` |
| **Security** | Production rules by default: hashed expiring member tokens, header auth on `/mcp`, rate limits, lockout, trusted-proxy allowlist, role levels checked per call, private-network-only peers, approval for every connection. | [`../SECURITY.md`](../SECURITY.md), [`AI_SETUP.md`](AI_SETUP.md#how-connecting-stays-safe) |
| **Verification** | CI on every push: ruff, mypy, 439 backend tests against a real FalkorDB, frontend types, lint, 215 tests, contrast, contract check, both builds, compose validation. Accessibility: axe-core, 0 violations across every screen at desktop and phone width. | `.github/workflows/ci.yml`, `frontend/scripts/a11y.mjs` |
| **Design** | An editorial, print-proof-room console: every control says what it is in words; reduced-motion respected; the onboarding tour never traps the screen (Escape, Skip and Close all end it). | `docs/screens/` |

## Honest limits

- **One writer per FalkorDB.** The atomic check-and-set runs under a lock in the single API process; FalkorDB decides ancestry, the lock makes the decision atomic. Horizontal scaling of writers is out of scope.
- **The ablation is generated data** and "flat" means "no causal edges". It shows provenance changes the outcome; it is not a product comparison.
- **The built-in retrieval vectors are not a neural model.** They handle inflection and a lexicon of business paraphrases; connect an OpenAI-compatible embedding endpoint for broad coverage.
- **LAN discovery** needs UDP broadcast on the network (many guest or corporate Wi-Fi networks block it). Connecting by address is the fallback: the same request flow works once a peer is known.
- **Not run in a hosted deployment.** There is no public URL; the demo video and one-command setup stand in for it. A production compose file with TLS is included.
- **Not covered:** counterfactual replay of an affected decision, temporal validity windows for facts, and re-checking an original source for drift. These are the clearest next steps; related projects (MnemoIR, Graphiti, ActiveGraph, MemGuard, graph-memory-mcp) each do one of them better than Quorum does today, and none combines them with guarded, compensated actions.
