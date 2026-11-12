# Testing the product end to end

Everything below runs against a real FalkorDB. Nothing here uses the in-browser sample backend.

| Command | What it proves | Needs |
| --- | --- | --- |
| `python -m pytest --cov` | Backend logic, persistence in FalkorDB, the FalkorDB gate, auth hardening, the graph layer | FalkorDB. The persistence and gate tests fail loudly without it. |
| `cd frontend && npm run verify` | Types, lint, 99 unit tests, contrast, contract, live and sample builds | nothing running |
| `cd frontend && npm run qa` | The console on sample data (104 checks, including the phone layout) | `npm run build:mock && npm run preview:mock` |
| `cd frontend && npm run qa:live` | The console against the live backend: an MCP client as an agent, the 15 tools, header auth, passive capture, semantic ranking, the audit log, teams, member tokens, team scope and links, presence, token savings, the feed and sessions (59 checks) | the stack below |
| `cd frontend && node scripts/resilience.mjs [BASE] [API]` | FalkorDB stopped under a running product: lock screen, 503s, MCP refused, automatic recovery, data intact (15 checks) | the stack, Docker |
| `cd frontend && node scripts/token_bench.mjs [API] [RUNS] [short or long]` | The measured token comparison: fresh Claude Code sessions with a typical top-8 retrieval vs Quorum's compact format. Writes `bench/token_bench_<scenario>.json`, which the Context and landing pages read. Costs a few dollars of Claude usage. | the stack, the `claude` CLI logged in |
| `cd frontend && node scripts/claude_e2e.mjs [API] [BASE]` | Real Claude Code sessions use Quorum as shared memory: write, read from a fresh session, revert a wrong fact (17 checks). Costs a little Claude usage (about $0.70 as reported by the CLI). | the stack, the `claude` CLI logged in |
| `python scripts/qa_mcp_clients.py --hub http://127.0.0.1:8010 --strict-hub http://127.0.0.1:8011` | The wire behaviour real MCP clients depend on, against a running hub: streamable HTTP with only an Authorization header, stdio via `mcp/server.py` and via `mcp/bridge.py`, a wrong token refused, a hub that requires the header, tool rate limits (16 checks). Run the second hub with `QUORUM_MCP_REQUIRE_HEADER=true QUORUM_RATE_LIMIT_TOOLS_PER_MIN=60`. It does not drive any client's user interface. Use `--hub https://localhost:8443` with `SSL_CERT_FILE` set to test through the TLS proxy. | a hub with `QUORUM_EMBED_MCP=1` |
| `python scripts/claude_e2e_v2.py --hub http://127.0.0.1:8010` | Two real Claude Code checks: header auth with a paraphrased question and no token anywhere in the prompt, and passive capture through the Stop hook with no Quorum tools (6 checks). Costs a little Claude usage. | a hub, the `claude` CLI logged in |
| `python bench/retrieval_bench.py` | 190 questions on a 336-fact memory: keyword ranking vs keywords plus vectors, no AI needed. | nothing running |
| `python bench/ai_retrieval_bench.py --hybrid URL --keyword URL` | The same memory answered by real Claude Sonnet and Haiku on two hubs (one started with `QUORUM_RANKING=keyword`). About 90 short runs. Start both hubs with `QUORUM_RATE_LIMIT_TOOLS_PER_MIN=0`. | two hubs, the `claude` CLI |
| `python bench/run.py` | The benchmark on FalkorDB. Refuses to run without it (`--memory` runs the model and labels the results so). | FalkorDB |

| `cd frontend && npm run qa:a11y` | axe-core (WCAG 2.0/2.1 A and AA plus best practices) on all ten screens at 1440 and 390 px, with reduced motion so reveal animations do not skew contrast: 0 violations of any impact | `npm run build:mock && npm run preview:mock` |
| `python scripts/qa_mcp_clients.py --hub URL --admin-token T` | The client protocol checks against a production hub (real tokens, TLS with `SSL_CERT_FILE`) | a production stack |
| `python bench/scale_bench.py` | Latency at 1,000 / 5,000 / 20,000 facts and a modelled 60-turn session | nothing running |

Note: the live QA drives the guided scenario and presses the original number keys, so start the stack with the story on: `QUORUM_DEMO=1 docker compose up -d`. A normal start (demo off) is an empty workspace with six screens, and `GET /api/scenario/*` answers 404.

## The stack

Everything in Docker (this is what a deployment looks like):

```bash
docker compose up -d --build
python scripts/doctor.py
cd frontend && npm run qa:live -- http://127.0.0.1:5173 http://127.0.0.1:8000
```

Or the app on your machine with FalkorDB in Docker:

```bash
docker compose up -d falkordb
QUORUM_ENV=development QUORUM_EMBED_MCP=1 python -m uvicorn app.main:app --app-dir backend --port 8000
cd frontend && npm run build && QUORUM_MCP=http://127.0.0.1:8000 npx vite preview --port 5174 --strictPort
cd frontend && npm run qa:live
```

The default build is live and uses the token sign-in screen. Only a development demo sandbox offers built-in demo credentials. Public config files must never carry bearer credentials. `npm run build:mock` makes the sample-data build for screenshots and demos only.

## What FalkorDB does here

- It is the system of record. Each tenant is a graph named `quorum:<tenant>` with `(:QNode:<Kind>)` beliefs, typed causal links, `(:QHandoff)` handoffs and the context epoch. Look at it with `docker exec quorum-falkordb redis-cli GRAPH.QUERY quorum:acme "MATCH (n:QNode) RETURN n.id, n.status"`.
- Writes are persisted before an API call or MCP tool call returns. Background work (agents, the saga) is persisted within half a second.
- A tenant's graph is rebuilt from FalkorDB the first time it is used, so restarting the backend loses nothing. `docker restart` of the backend container returns the same nodes, epoch and quarantines.
- If FalkorDB is not reachable every API route and MCP tool answers `503 FALKORDB_UNAVAILABLE`, `/api/health` says why, and the console shows the setup screen.
- The Cypher shown in the console's drawer is the Cypher that ran.

Still true: hot-path decisions (cascade, execute guard, quorum gate) run in the API process under locks, then are written to FalkorDB. Janani's atomic Cypher guards in `graph/queries.py` are exercised by the tests and the benchmark but are not what the API calls. Run one API process per FalkorDB.

## What the first end-to-end runs found and fixed

| Where | Problem | Fix |
| --- | --- | --- |
| Console | The backend sends `null` for optional fields; the console rejected the whole graph. | Optional fields accept `null`, with a regression test. |
| Backend `GET /graph` | `seq` was the query-log length, not the event sequence, so the console re-synced forever. | `seq` comes from the event bus; test added. |
| Console events | An event type it did not know was dropped and left a hole in the sequence, which stalled the stream. | Unknown events keep their place and change nothing. |
| Backend `GET /events` | Headers were not sent until the first event, so the stream looked stuck at "connecting". The server also could not tell a restarted server from a client that was ahead. | Immediate heartbeat; a client ahead of the server gets `resync_required`. |
| Console startup | A handoffs request fired before the session token existed. | The query waits for the session. |
| Backend MCP | The standalone MCP server had its own in-memory graph, so agent writes never reached the console, and MCP writes published no events. | MCP is served from the API process (`QUORUM_EMBED_MCP=1`), and the write tools publish events. |
| Backend | Handoffs were one global list shared by every tenant, and handoffs created by the gate, auditor and saga were never stored. | Handoffs live on the tenant's graph store and are persisted. |
| Backend auth | Any unrecognised token became a human on tenant acme, and the MCP tools accepted any token as an agent. | One token resolver for API and MCP. Ad-hoc tokens only outside production; demo tokens are refused in production. |
| Backend API | `/bench/results`, `/cypher-log` and `/tenants/denied-demo` returned canned numbers and text. | They return the recorded benchmark run, the real Cypher log and a live isolation check. |
| Docs | Headline numbers (33,111 ops/s, "350x faster", "900x faster") came from the in-memory model or were not measured. | Replaced with the numbers from `bench/results.json` (FalkorDB, with the engine named); invented comparison columns removed. |
| Docker | The backend image lacked `agents/`, the MCP image lacked `mcp`, the frontend ran the dev server against `localhost`, and compose had no health ordering. | All fixed; the full stack comes up with one command and passes `qa:live`. |
