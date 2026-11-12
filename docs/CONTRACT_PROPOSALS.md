# Console features and the backend: what is real now, what is still open

Everything the Context, Connect and Team pages need from the backend now exists and is exercised by `npm run qa:live`. This file used to be a wish list; it is now the record of what was added, so the contract owners can fold it into `docs/CONTRACT.md`.

## Implemented

### MCP tools (15)

The original ten, plus `get_map`, `get_node`, `find_path`, `delta` and `observe` (passive fact capture). `retrieve_context` gained `format` (`"full"` default, or `"compact"`) and `budget_tokens`. `remember_fact` gained `scope` (`"commons"` default, or `"team"`). Every response carries `tokens` and `tokens_baseline`. Method and measurements: [`TOKEN_SAVING.md`](TOKEN_SAVING.md).

### Workspace service (server side, persisted in FalkorDB with the tenant graph)

```
GET    /api/workspace                         plan, limits, teams, members (presence, calls, tokens saved), links
PUT    /api/workspace                         { org?, plan?, hub? }               human token
POST   /api/workspace/teams                   { name, seats }
PATCH  /api/workspace/teams/{id}              { seats }
DELETE /api/workspace/teams/{id}
POST   /api/workspace/members                 { name, role, team_id, client, device } -> { issued: { member, token } }
POST   /api/workspace/members/{id}/rotate     -> { issued: { member, token } }
DELETE /api/workspace/members/{id}
POST   /api/workspace/links                   { a, b, scope }
PATCH  /api/workspace/links/{id}              { paused }
DELETE /api/workspace/links/{id}
```

Rules enforced by the server: plan and per-team seat limits, unique team and member names, valid roles and clients, no self or duplicate links, an http(s) hub URL. Writes need a `human` token. A member's token (`qrm_<tenant>_<secret>`) is shown once; only its SHA-256 is stored; rotating or removing the member invalidates it. Member tokens are the only tokens besides `QUORUM_TOKENS_JSON` that work in production.

### Team scope

A fact saved with `scope="team"` carries `scope_team` and is visible to that team and to teams of members linked to it by an active link. A node derived from a private parent inherits the privacy. The scope applies to the retrieval tools, `get_node`, `find_path`, `get_map`, `delta`, `GET /api/graph`, `GET /api/node/{id}`, the event stream and the call feed. Service and demo tokens are unscoped.

### Activity, presence and savings

```
GET /api/mcp/activity?limit=&member_id=   every MCP call: tool, agent, summary, tokens, baseline, ms, ok
GET /api/token-stats                      calls, measured calls, tokens, baseline, saved, saved_pct, by tool, by agent
GET /api/context/preview?query=&budget=   the map, the packed answer and every baseline for the live graph
GET /api/health                           FalkorDB status and setup steps (no token)
```

Presence is derived from calls: online within two minutes of a call, idle within fifteen, otherwise offline. New events on the stream: `mcp_call` (`data.call`) and `workspace_changed`.

### Added later

```
GET  /api/mcp/catalog                      the tools the hub serves (read from the code), header/limit/embedding settings, public URL
POST /api/observe                          { text, source?, dry_run?, scope? } -> facts extracted from a transcript or tool output
GET  /api/audit?limit=&since=&agent=&severity=&format=json|csv     the call log as plain sentences, with a CSV export
GET  /api/context/semantic-check?query=    FalkorDB's own vector index vs the API's ranking
```

`/mcp` accepts `Authorization: Bearer <token>` (or `X-Quorum-Token`); with it every tool's `token` argument is optional. 401 for a bad header token, 429 + `Retry-After` for HTTP limits, `{"rate_limited": true}` for tool limits. Agent read-state, node vectors and the call log (5,000 calls) are persisted in FalkorDB. Details: [`MCP.md`](MCP.md).

## Still open

- **Chats.** Quorum sees tool calls and, with the capture hook, what an agent wrote; not the conversation. The Sessions tab shows the calls and says so.
- **Several API processes** against one FalkorDB are not supported: guards run in the process under locks.
- **Hub.** There is one hub per FalkorDB, with HTTPS from `docker compose --profile tls`. Nothing relays between hubs, and the workspace `hub` field is a label.
- **Neural embeddings** are wired and tested against a stub endpoint, not yet run against a real embedding model.
- **Console actions** (a person quarantining in the UI) are not in the audit log; MCP tool calls are.
