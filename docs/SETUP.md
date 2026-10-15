# Setting up Quorum

Quorum stores every belief, handoff and action in **FalkorDB**. It does not run without it: if FalkorDB is missing or misconfigured, the API answers `503 FALKORDB_UNAVAILABLE`, the MCP server refuses tool calls, and the console shows a locked setup screen instead of the product. There is no sample-data fallback in a normal build.

## 1. Start everything (Docker)

```bash
docker compose up -d
python scripts/doctor.py
```

Open `http://localhost:5173`. The console opens by itself once FalkorDB is ready.

`doctor.py` checks Python packages, Docker, FalkorDB (including that the graph module is loaded), the backend and the MCP endpoint, and prints the exact command that fixes anything missing.

## 2. Start it without Docker for the app (FalkorDB still in Docker)

```bash
docker compose up -d falkordb
pip install -r backend/requirements.txt
QUORUM_ENV=development QUORUM_EMBED_MCP=1 python -m uvicorn app.main:app --app-dir backend --port 8000
cd frontend && npm ci && npm run build && npx vite preview --port 5173
python scripts/doctor.py
```

On Windows PowerShell set the variable first: `$env:QUORUM_EMBED_MCP = "1"`.

## 3. Connect an AI

Open the **Connect** page in the console for paste-ready setup for Claude Code, Claude Desktop, Cursor, VS Code, Windsurf, Antigravity and any MCP client. For Claude Code:

```bash
claude mcp add --transport http quorum http://localhost:8000/mcp --header "Authorization: Bearer <your-token>"
```

The token travels in the connection (an `Authorization` header, or `QUORUM_MCP_TOKEN` for stdio), so the model never sees it and the tools need no `token` argument. Clients that cannot send a header (ChatGPT) pass it as a tool argument instead. Which clients were really run, and how, is in [`MCP.md`](MCP.md). For apps that only launch commands (Claude Desktop), use the stdio bridge, `python mcp/bridge.py`.

To capture facts without the agent calling anything, add the Claude Code hook from [`MCP.md`](MCP.md#passive-capture-no-tool-call-needed) (`scripts/quorum_observer.py`).

## 4. Teams, members and tokens

Open **Team** in the console (you need a `human` token). Add a team, then add a member to it: you get that person's token **once** (`qrm_<tenant>_<secret>`); only its hash is stored, and rotating or removing the member invalidates it. They paste it into their AI's rules (the Connect page generates the text) and connect their own laptop to the same backend URL.

- Plans set the seat and team limits; teams set their own seats. The server enforces both.
- A fact saved with `scope="team"` is private to that team. **Link** two members and each sees the other's team facts, from any laptop and any AI; pause or unlink and access ends at once.
- Presence, call counts and "tokens saved" per member come from their real MCP calls.

## What happens when FalkorDB goes away

| Moment | What you see |
| --- | --- |
| FalkorDB not running at start | The console shows "FalkorDB is not set up, so Quorum is locked" with the steps. The backend still starts so it can say so. |
| FalkorDB stops while you work | Within a few seconds every API call returns 503, the event stream ends, and the console is replaced by the same screen. Writes made in that moment are held and saved when it returns. |
| FalkorDB comes back | The console reopens by itself and reloads from the database. Nothing is lost. |
| Backend not running | The console says the backend is not running and shows the command to start it. |

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `QUORUM_FALKORDB_URL` | `redis://localhost:6379` | Where FalkorDB is. `FALKORDB_HOST` and `FALKORDB_PORT` are also read. |
| `QUORUM_EMBED_MCP` | off | Serve MCP from the API process at `/mcp` so agents and the console share one graph. Needs `mcp>=2`. |
| `QUORUM_ENV` | **`production`** | Secure by default: with nothing set there are no demo tokens, no ad-hoc tokens, `/mcp` needs an Authorization header, and the console asks for a token. `development` turns the demo tokens on for a local demo (`docker-compose.yml` sets it; the commands above do too). |
| `QUORUM_TOKENS_JSON` | empty | `{"<token>": {"tenant": "acme", "role": "agent", "agent_id": "scout"}}`. With member tokens issued from the Team page, the only tokens accepted in production. |
| `QUORUM_ALLOW_DYNAMIC_TOKENS` | on only in development | Accept ad-hoc `token_<tenant>_<role>` tokens. Anyone can forge these, so keep it off in production. |
| `QUORUM_CORS_ORIGINS` | localhost only | Comma separated origins allowed to call the API from a browser. |
| `QUORUM_TRUSTED_PROXIES` | `127.0.0.1,::1` | Addresses (or CIDR ranges) allowed to tell the hub the real client address through `X-Forwarded-For` / `X-Real-IP`. Anyone else's header is ignored. The compose files pin the TLS proxy to `172.29.77.10` and trust only that; do not trust a whole private range on a port you publish, because a client reaching it arrives from the Docker gateway and could then spoof its address. |
| `QUORUM_PUBLIC_URL` | empty | Where other machines reach this hub (for example `https://quorum.example.com:8443`). The Connect page prints it in the snippets. |
| `QUORUM_MCP_REQUIRE_HEADER` | on in production | Refuse `/mcp` requests that carry no `Authorization` header. A wrong header token is always refused. |
| `QUORUM_RATE_LIMIT_TOOLS_PER_MIN` | `600` | Tool calls per token per minute (burst a sixth of it). `0` is off. Bulk imports need it raised. |
| `QUORUM_RATE_LIMIT_HTTP_PER_MIN` | `3000` | HTTP requests per token or address per minute. `0` is off. |
| `QUORUM_RANKING` | `hybrid` | `keyword` turns the vector part of ranking off, for comparison. |
| `QUORUM_EMBED_PROVIDER`, `QUORUM_EMBED_URL`, `QUORUM_EMBED_MODEL`, `QUORUM_EMBED_API_KEY` | `local` | Use any OpenAI-compatible embeddings endpoint (OpenAI, Ollama, LM Studio, vLLM) instead of the built-in vectors. |
| `QUORUM_OBSERVER_MAX_FACTS` | `12` | Most facts one `observe` call may store. |
| `QUORUM_DEMO` | off | The guided poisoned-webhook story, its sample agents and the four teaching screens (Memory, Brain, Tenants, Benchmark). Off on a fresh install: the workspace is empty and the console shows six screens (Overview, Console, Context, Connect, Team, Activity). Set `QUORUM_DEMO=1` to present the story. |
| `QUORUM_PERSISTENCE` | `falkordb` | `memory` exists for unit tests only and shows a warning banner. Never use it for real work. |

## A clean first install

A new install is an empty workspace: no teams, no members, no facts, no sample agents, no guided scenario. The console opens on the **Overview**, which shows three steps (add a person, connect their AI, watch it work) until something exists, then turns into live charts: memory health, who is online, tokens saved, what needs attention, and calls per hour. To start again from nothing, `docker compose down -v` (also removes the database volume) or `python scripts/reset_data.py --yes`.

## Production in one command

```bash
python scripts/make_token.py --tenant acme --role human --name ops     # prints the token once and the QUORUM_TOKENS_JSON line
cp .env.production.example .env.production                              # fill in FALKORDB_PASSWORD, the tokens, your public URL
docker compose -f docker-compose.yml -f docker-compose.production.yml --env-file .env.production --profile tls up -d --build
```

This was run end to end: the hub starts with production rules, FalkorDB asks for its password and is not published on the host, only the HTTPS proxy (8443) is reachable, `/api/dev-config` is 404, demo and ad-hoc tokens get 401, `/mcp` without a header gets 401, the console shows a sign-in screen, and a member token works over header auth and the stdio bridge.

**Signing in.** Without demo tokens the console opens a sign-in screen: paste a human token (from `make_token.py`, or a member token). It is checked with the hub (`GET /api/whoami`), kept in that tab's sessionStorage only, and a token the hub later refuses (expired, revoked) sends you back to sign-in. Member tokens expire after `QUORUM_MEMBER_TOKEN_TTL_DAYS` (the Team page lets you choose per member; rotating renews).

**Also in place:** failed sign-ins lock an address out for a minute after `QUORUM_AUTH_FAILURES_PER_MIN` (20), request bodies are capped at `QUORUM_MAX_BODY_BYTES` (1 MB), every response carries `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, and API answers are `no-store`. Things people do in the console (team and member changes, handoff reviews, scenario steps) are in the Activity log next to agent calls. Threat model: [`../SECURITY.md`](../SECURITY.md).

## Production checklist

1. `QUORUM_ENV=production`, and `QUORUM_TOKENS_JSON` with real tokens. Sign in using the console token form. Keep bearer credentials in backend secrets and the signed-in tab only; never deploy them in public files.
2. FalkorDB with a volume and a password in `QUORUM_FALKORDB_URL` (`redis://:password@host:6379`). Do not expose port 6379.
3. One backend process. Atomic guards run in the process under locks, so do not run several API workers against one FalkorDB.
4. Put HTTPS in front of the console, the API and `/mcp`: `docker compose --profile tls up -d` runs a Caddy proxy (see [`MCP.md`](MCP.md#https-for-a-team)). Set `QUORUM_PUBLIC_URL` to its address and `QUORUM_CORS_ORIGINS` to the console's origin.
5. `python scripts/doctor.py` and `GET /api/health` should both report ready.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| "Database not ready" screen | `docker compose up -d falkordb`, then `python scripts/doctor.py` |
| "Redis is running but the FalkorDB graph module is not loaded" | You started plain Redis. Use the `falkordb/falkordb` image. |
| "Backend not running" screen | Start the backend (step 2), or `docker compose up -d backend`. |
| Console loads but every call is 401 | Sign in with a configured or issued member token; check expiry and revocation (see `QUORUM_TOKENS_JSON`). |
| Agent writes do not show in the console | The MCP server is a separate process. Use `QUORUM_EMBED_MCP=1`, and for stdio apps use `mcp/bridge.py` rather than the standalone server. |
| An MCP client gets 401 | The `Authorization` header is missing or the token is wrong. A member token is shown once; rotate it on the Team page. |
| Tool calls answer `rate_limited` or HTTP 429 | The per-token limit was hit. Wait `retry_after` seconds, or raise `QUORUM_RATE_LIMIT_TOOLS_PER_MIN` (a bulk import needs it). |
| `docker compose build` fails at `pip install` | The Docker engine has no network. Check Docker Desktop's proxy and DNS settings. |
