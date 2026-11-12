# Quorum MCP: how to connect, what is checked, what is not

One hub (the API process, with MCP at `/mcp`) holds the graph in FalkorDB. Every AI client connects to that hub, so every agent reads and writes the same memory, and the console shows it live. The hub needs FalkorDB and refuses to run without it.

## Connect a client

The Connect page in the console prints these with your hub's address and a member token. A token comes from the Team page and is shown once.

| Client | How it connects | Where the token goes | What was actually run |
| --- | --- | --- | --- |
| Claude Code | HTTP, or stdio through the bridge | `--header "Authorization: Bearer …"` (HTTP), `QUORUM_MCP_TOKEN` (stdio) | **Run for real** with the `claude` CLI: header auth, paraphrased retrieval, passive capture hook (`scripts/claude_e2e_v2.py`) |
| Claude Desktop | stdio, through `mcp/bridge.py` | `QUORUM_MCP_TOKEN` in the config's `env` | The bridge was run as a stdio server against a hub (`scripts/qa_mcp_clients.py`). The Claude Desktop application itself was not driven. |
| Cursor | HTTP (`url` + `headers`) or stdio | Authorization header | Not installed here. The HTTP/header behaviour it relies on is what the protocol check exercises. Config shape is from Cursor's documentation, unverified in the app. |
| VS Code | HTTP (`type: http`) or stdio, with an `inputs` prompt for the token | Prompted at start, never stored in the file | Installed here; its config shape is unit tested, but a Copilot chat was not driven. |
| Windsurf | HTTP (`serverUrl` + `headers`) or stdio | Authorization header | Not installed here. Shape from documentation. |
| Antigravity | HTTP (`serverUrl`) or stdio. The app reads `~/.gemini/antigravity/mcp_config.json`; the **`agy` command line keeps its own list** | Authorization header. For `agy`: `agy mcp add --header "Authorization: Bearer <token>" quorum http://localhost:8000/mcp` | **Run for real with `agy`** against a live hub: it wrote a fact, a conclusion, and read context as its own member, and after the fact was quarantined its next lookup returned nothing. `headers` in the JSON file were not sent by `agy` (every request got 401); registering with `agy mcp add --header` fixed it. |
| ChatGPT / OpenAI Agents | HTTPS only | **Cannot send a header.** The token is a tool argument, so the model sees it | Not run. Use a private hub. |

"Run for real" means a real session of that client called the tools. "The protocol check" means `scripts/qa_mcp_clients.py` spoke streamable HTTP and stdio the way those clients do. It proves the wire behaviour, not their user interfaces. If a snippet is wrong for your version of a client, that is a documentation bug: please say so.

## Authentication

- A client sends `Authorization: Bearer <token>` (or `X-Quorum-Token`). The hub checks it before a request reaches a tool, and a tool then runs as that member: their team scope, their name in the audit log.
- With the header, **the `token` argument is optional and the model never sees the secret.** Without it (ChatGPT, old clients) the token is still accepted as a tool argument.
- A wrong header token is refused with 401 even when headers are optional. Set `QUORUM_MCP_REQUIRE_HEADER=true` (the default under `QUORUM_ENV=production`) to refuse requests with no header at all.
- A stdio server reads the token from `QUORUM_MCP_TOKEN`, so it is not in any command line or argument.

## Permission levels

Three levels, shown with plain names on the Team page (the stored role names are in brackets):

| Level | Can do | Cannot |
| --- | --- | --- |
| **Contributor** (`agent`) | Look things up, save facts and conclusions, stage and run actions, claim tasks | Quarantine or restore, approve reviews, claim reviews, manage people |
| **Reviewer** (`auditor`) | Everything a Contributor can, plus quarantine and restore, and claim any handoff | Approve reviews, manage people |
| **Admin** (`human`) | Everything, including approving reviews and managing people, teams, links, plan and tokens | |

An admin changes a person's level on the **Team** page (the Permission level column) or with `PATCH /api/workspace/members/{id}` `{"role": "auditor"}`. It applies on that token's next call; no new token is needed. A refused call now says who was refused, why, what level is needed and where to change it (Activity shows a "Change permissions" button on those rows). The table on the Team page is the hub's own (`GET /api/roles`, `backend/app/services/roles.py`); a test checks every row against what is enforced. When the levels change, regenerate `backend/tests/fixtures/roles.json` (`python -c "import json; from app.services import roles; open('backend/tests/fixtures/roles.json','w').write(json.dumps(roles.catalog(), indent=2)+chr(10))"` with `backend` on the path).

## Coordination and time

| Tool | Who | What it does |
| --- | --- | --- |
| `announce_work` | Contributor and up | Say what you are about to work on (a file, an order, a ticket). Returns who else announced it, or something inside or around it, in the last `minutes` (default 30). If the target is a fact id it also returns work on any fact within three causal hops, upstream or downstream (`relation`, `hops` in each conflict). `done=true` releases your note. Notes expire and are not facts. The same call is `POST /api/work/announce`, which `scripts/quorum_conflict_hook.py` (a Claude Code `PreToolUse` hook) uses to warn before an edit. |
| `recall_as_of` | anyone | What the team believed at `as_of` (milliseconds) or `minutes_ago`; each fact says whether it is still believed, replaced or quarantined since. Scope rules apply as for `retrieve_context`. |

`remember_fact` takes an optional `copied_from` (the id of a fact you are restating): the quorum gate never counts a fact together with its own ancestry as two witnesses. Every `remember_fact` result carries `outcome` (`ACCEPT`, `SUPERSEDE`, `REJECT`, `REVIEW`) and a plain `receipt`; every retrieval carries `outcome` (`ANSWERED` or `NOT_IN_MEMORY`). Writes are refused when a field looks like a credential, contains control characters, is oversize, or when an id is malformed; the origin always names the caller.

## The chat layer: set up and run a team without the console

Eleven more tools sit beside the 17 memory and coordination tools (28 in all; `QUORUM_TOOL_PROFILE=core` lists only the 17). Each returns a short `text` the AI can show as is, plus the data behind it. Full walkthrough: [`AI_SETUP.md`](AI_SETUP.md).

| Tool | Who | What it does |
| --- | --- | --- |
| `quorum_help` | anyone | Every command and what it does. |
| `quorum_guide` | anyone | The full use guide in the chat. No arguments: the topics. `topic=<id>`: its steps. `topic=install client=<your AI>`: the exact file and text to add so this AI follows the working agreement (plus the capture and collision hooks for Claude Code). The same data is `GET /api/guide`, the console's **How to use Quorum** panel and `docs/USER_GUIDE.md`. |
| `quorum_start` | anyone to read, Admin to apply | No arguments: the five survey questions. With answers: names the team, sets seats, records the capture preference, looks for teammates. |
| `quorum_status` | anyone | What is remembered, what will be remembered, who it is shared with, who is connected, tokens saved. |
| `quorum_manage` | Admin (overview: Reviewer too) | `team_create`, `team_seats`, `team_remove`, `member_add`, `member_set_level`, `member_move`, `member_new_token`, `member_remove`, `leaver_report`, `link`, `pause_link`, `resume_link`, `unlink`. A new token is shown once. |
| `quorum_discover` | anyone | Other Quorum hubs on the network (found by the host-side beacon). |
| `quorum_connect` | Admin | Ask to join a hub by name or by address. Its admin is notified. |
| `quorum_inbox`, `quorum_respond` | Admin | See and answer requests; approving adds a member at the level chosen (Contributor by default). |
| `quorum_connections` | Admin | Hubs you joined and requests you sent; `reveal=true` prints the MCP settings for attaching a hub to an AI client. |
| `peer_retrieve` | Admin | Ask a connected hub a question, within the level its admin granted you. |

An Admin token also gets one extra line, `N connection request(s) waiting for you`, on any tool result while a request is open. REST equivalents for the console: `GET /api/peers`, `GET /api/connect/inbox`, `POST /api/connect/inbox/{id}/respond`, `POST /api/connect/outgoing`; the public handshake another hub uses is `GET /api/connect/hello`, `POST /api/connect/request`, `GET /api/connect/requests/{id}/status` (with the requester's secret in `X-Connect-Secret`).

## Rate limits

A token bucket per caller: `QUORUM_RATE_LIMIT_TOOLS_PER_MIN` (default 600, burst 100) for tool calls per token, and `QUORUM_RATE_LIMIT_HTTP_PER_MIN` (default 3000, burst 500) for HTTP requests per token or address. `/api/health` and the event stream are exempt. A throttled HTTP request gets `429` and `Retry-After`; a throttled tool call returns `{"rate_limited": true, "retry_after": …}` and writes nothing. `0` turns a limit off. **A bulk import (hundreds of `remember_fact` calls in a burst) will be throttled: raise the limit or set it to `0` for the import**, and check the count afterwards. The limiter is in memory in one process; it protects the hub from a runaway loop, it is not a billing quota.

## stdio: the bridge, and the standalone server

`mcp/bridge.py` is the way for apps that launch a command. It holds no state and forwards every call to the hub over HTTP(S) with your token in a header, so there is one writer, one graph, one rate limit and one audit log.

```bash
python mcp/bridge.py --url https://hub.example.com:8443/mcp     # token from QUORUM_MCP_TOKEN
```

`mcp/server.py` remains for historical memory-mode demos, but standalone FalkorDB writers are refused: reloading a snapshot per call does not make guards or leases safe across processes. Use the stdio bridge to the one API process with embedded MCP.

## Passive capture (no tool call needed)

Agents forget to save what they learn. Quorum can read what they wrote instead.

- **`observe(text)`** (MCP tool) and **`POST /api/observe`**: extract the sentences in some text that look like durable facts (a number with a unit, a policy such as "must" or "within", a limit, a date), store each as a fact whose `quote` is its source sentence and whose `origin` is `observer:<source>`. Duplicates are skipped. A conflicting number for the same thing opens a human review instead of replacing the old fact. `dry_run=true` previews.
- **`scripts/quorum_observer.py`** is a Claude Code `Stop` hook that sends the new part of the session transcript to `/api/observe`, so facts reach shared memory with no tool calls at all. It never blocks the session, redacts obvious credentials, and reads only assistant text unless you set `QUORUM_OBSERVE_TOOL_RESULTS=1`.

It is rule based with no model call: conservative on purpose. It drops questions, hedges ("maybe", "I think"), narration of what the agent just did, code, and anything that looks like a credential. It will miss facts stated without a number or a policy word, and it cannot judge whether a sentence is true: that is what the auditor, tiers and review handoffs are for.

## Ranking: keywords plus vectors

`retrieve_context` ranks healthy, in-scope facts by IDF-weighted keyword coverage **plus cosine similarity of vectors** (50/50). A node with no shared keyword needs a cosine of at least 0.30 to count, so unrelated questions return nothing.

- **Local vectors (default).** A deterministic hashed vector: word stems, a built-in concept lexicon (refund = reimbursement = money back, 81 groups of business vocabulary), word pairs and character trigrams. No model, no network. It is not a neural model: it handles inflection, typos and the paraphrases the lexicon knows. A paraphrase with words outside the lexicon still misses.
- **Neural embeddings (optional).** `QUORUM_EMBED_PROVIDER=openai`, `QUORUM_EMBED_URL`, `QUORUM_EMBED_MODEL` (and `QUORUM_EMBED_API_KEY`) use any OpenAI-compatible `/v1/embeddings` endpoint: OpenAI, a local Ollama or LM Studio, vLLM. If the endpoint fails, ranking degrades to the local vectors instead of failing the call. This path is covered by tests with a stub endpoint; it has not been run against a real model here.
- **In FalkorDB.** Vectors are stored on the nodes (`embedding`, with a vector index) and reloaded after a restart instead of recomputed. `GET /api/context/semantic-check?query=…` asks FalkorDB's own index for the nearest nodes and says whether they agree with the API's ranking. `QUORUM_RANKING=keyword` turns the vectors off, for comparison.

Measured: `bench/retrieval_bench.py` (offline) and `bench/ai_retrieval_bench.py` (real Claude models, 44 questions). Numbers are in [`TOKEN_SAVING.md`](TOKEN_SAVING.md#does-the-right-fact-come-back).

## State that survives a restart

Everything is in FalkorDB: nodes, links, handoffs, the epoch, the workspace (teams, members, links, connection requests and the survey answers), the call log (last 5,000 calls), **what each agent has already read** (so `delta` still works after a restart) and the node vectors.

## HTTPS for a team

```bash
docker compose --profile tls up -d        # a Caddy proxy on https://localhost:8443
```

One address serves the console, the API and `/mcp`. Set `QUORUM_PUBLIC_URL=https://your-host:8443` on the backend so the Connect page prints that address. For a public name, set `QUORUM_DOMAIN` and `QUORUM_TLS=you@example.com` for a Let's Encrypt certificate. With the default `internal` mode Caddy makes its own certificate authority: copy its root certificate (`docker cp quorum-proxy:/data/caddy/pki/authorities/local/root.crt .`) and trust it on each laptop. Run one hub per FalkorDB; there is no relay between hubs, and the workspace `hub` field is only a label.

## Audit log

`GET /api/audit` (and the **Activity** page) reads the call log as plain sentences: who did what, when, the tokens it cost, flagged when it needed a person or was refused. `?format=csv` exports it, with cells that start with `=`, `+`, `-` or `@` neutralised so a spreadsheet does not run them. It records MCP tool calls; actions taken by hand in the console are not in it yet.

## Known limits

- One API process per FalkorDB: the atomic guards run in the process under locks.
- The call log keeps the last 5,000 calls. Export before it rolls over if you need longer.
- No conversation text: Quorum sees tool calls and, with the hook, what an agent wrote, not the whole chat.
- Retrieval quality beyond the built-in vocabulary needs a neural embedding endpoint.
- The console is a desktop layout (it is 1,024 px wide at minimum).
